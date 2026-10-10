"""SSE 存活机制测试（2026-10-09 生产间歇性卡顿修复）。

聊天事件流（/ai/chat/sessions/<sid>/events）改为 pump 线程 + 队列解耦
「读上游」与「写客户端」：
  - 上游静默时周期性向客户端 yield ': ping'（SSE 注释帧）——死 tab 在一个
    ping 周期内触发 GeneratorExit，waitress 线程随即释放；
  - subscribe_events 带 read_timeout：上游挂死时 pump 有限期退出，客户端流
    结束，由浏览器 EventSource 重连收敛（语义与旧断线路径一致）。

批事件流（/ai/chat/batches/events）的连接建立鉴权与 15s tick 终态检查
改为单条 IN 查询（原先逐 id get_batch_detail——×20 id ×每次重连 ×每 tick
是 DB 池同相位瞬时借满的主源）——DB 型用例钉住「SSE 路由不再调
get_batch_detail」且归属过滤/终态收流语义不变。
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _drain(resp, want, deadline_sec=10):
    """增量消费流直到 body 满足谓词或流自然结束；返回累计 body。

    不用 resp.data 整体缓冲：被测对象是流本身，且行为回退（不再收流）时
    整体缓冲会把用例挂死在 30 分钟硬上限上。
    """
    body = ''
    deadline = time.time() + deadline_sec
    for chunk in resp.response:
        body += chunk.decode() if isinstance(chunk, bytes) else chunk
        if want(body):
            return body
        assert time.time() < deadline, f'sse 流超时未满足条件，已收到: {body!r}'
    return body


class _QuietUpstream:
    """上游静默的假 runtime：get_messages 空历史；subscribe 卡到放行。"""

    def __init__(self):
        self.hold = threading.Event()
        self.read_timeouts = []

    def get_messages(self, session_id, directory=''):
        return []

    def subscribe_events(self, directory='', read_timeout=None):
        self.read_timeouts.append(read_timeout)
        self.hold.wait(timeout=10)
        return
        yield  # 让本函数成为 generator（与真实客户端同形）


class _DeadUpstream:
    """上游一订阅即挂的假 runtime。"""

    def get_messages(self, session_id, directory=''):
        return []

    def subscribe_events(self, directory='', read_timeout=None):
        raise RuntimeError('upstream gone')
        yield


def _fake_sess(sid, uid):
    # _load_session_for_user 的行形状：(id, user_id, opencode_session_id,
    # status, workspace_path, batch_id, orchestration_run_id)
    return (sid, uid, f'oc-{sid}', 'active', '/tmp/sse-t-ws', None, None)


def test_chat_sse_pings_while_upstream_idle(client, admin_headers, monkeypatch):
    """上游静默 → 客户端按 SSE_PING_SEC 周期收到 ': ping' 注释帧。"""
    import routes.ai_chat as m
    fake = _QuietUpstream()
    monkeypatch.setattr(m, 'SSE_PING_SEC', 0.2)
    monkeypatch.setattr(m, '_load_session_for_user', _fake_sess)
    monkeypatch.setattr(m, 'get_runtime', lambda: fake)

    resp = client.get('/ai/chat/sessions/s-ping/events', headers=admin_headers)
    assert resp.status_code == 200
    body = _drain(resp, lambda b: b.count(': ping') >= 2)
    assert body.count(': ping') >= 2
    # read_timeout 必须下发到上游订阅——上游挂死时 pump 有限期退出的保证
    assert fake.read_timeouts and \
        fake.read_timeouts[0] == m.SSE_UPSTREAM_READ_TIMEOUT

    # 放行上游 → 哨兵 → 客户端流自然收口（迭代到 StopIteration，不悬挂）
    fake.hold.set()
    rest = ''
    for chunk in resp.response:
        rest += chunk.decode() if isinstance(chunk, bytes) else chunk
    # 订阅锚点（session.hello）之外全程无真实事件，只有注释帧
    assert body.count('event:') + rest.count('event:') == 1
    assert 'event: session.hello' in body
    resp.close()


def test_chat_sse_ends_promptly_when_upstream_dies(client, admin_headers,
                                                   monkeypatch):
    """上游立刻挂 → 哨兵先于任何 ping 到达：空流收口，不悬挂。"""
    import routes.ai_chat as m
    monkeypatch.setattr(m, 'SSE_PING_SEC', 0.2)
    monkeypatch.setattr(m, '_load_session_for_user', _fake_sess)
    monkeypatch.setattr(m, 'get_runtime', lambda: _DeadUpstream())

    resp = client.get('/ai/chat/sessions/s-dead/events', headers=admin_headers)
    assert resp.status_code == 200
    body = _drain(resp, lambda b: False, deadline_sec=10)
    # 上游挂死即收口：锚点（hello）之外无任何事件/ping 帧
    assert body.count('event:') == 1 and 'event: session.hello' in body
    assert ': ping' not in body
    resp.close()


# ---------------------------------------------------------------------------
# 批事件流：归属单查 + 终态单查（DB 型）
# ---------------------------------------------------------------------------

_B1 = 'b-ssetest-done'
_B2 = 'b-ssetest-run'
_BDEV = 'b-ssetest-dev'


@pytest.fixture
def real_client():
    """不 mock db.get_db 的 Flask 测试客户端（批事件流查真实测试库）。"""
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    yield flask_app.test_client()


@pytest.fixture
def batch_rows(db_conn):
    with db_conn.cursor() as cur:
        for bid, uid, st in ((_B1, 'user-admin', 'completed'),
                             (_B2, 'user-admin', 'running'),
                             (_BDEV, 'user-dev', 'running')):
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, status) "
                "VALUES (%s, %s, 'sse-test', 'p', %s) "
                "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status",
                (bid, uid, st))
    db_conn.commit()
    yield
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_batch_events WHERE batch_id = ANY(%s)",
                    ([_B1, _B2, _BDEV],))
        cur.execute("DELETE FROM ai_chat_batches WHERE id = ANY(%s)",
                    ([_B1, _B2, _BDEV],))
    db_conn.commit()


def _forbid_get_batch_detail(monkeypatch):
    import routes.ai_chat_batches as m

    def _bomb(*a, **k):
        raise AssertionError('get_batch_detail 不应再被 SSE 路由调用'
                             '（归属/终态检查已改为单条 IN 查询）')
    monkeypatch.setattr(m, 'get_batch_detail', _bomb)


def test_batch_events_sse_owned_filter_and_done_without_detail(
        real_client, batch_rows, admin_token, monkeypatch):
    """非归属/不存在 id 剔除；全部终态 → batch_done 收流；零 detail 调用。"""
    _forbid_get_batch_detail(monkeypatch)
    url = (f'/ai/chat/batches/events?ids={_B1},{_BDEV},b-ssetest-nope'
           f'&access_token={admin_token}')
    resp = real_client.get(url)
    assert resp.status_code == 200
    body = _drain(resp, lambda b: 'batch_done' in b)
    assert f'"{_B1}": "completed"' in body
    assert _BDEV not in body            # 他人批次静默剔除
    assert 'b-ssetest-nope' not in body  # 不存在的 id 静默剔除
    assert 'event: batch_done' in body
    resp.close()


def test_batch_events_sse_running_batch_pings_without_done(
        real_client, batch_rows, admin_token, monkeypatch):
    """非终态批次：connected + ping，不收流；同样零 detail 调用。"""
    _forbid_get_batch_detail(monkeypatch)
    url = f'/ai/chat/batches/events?ids={_B2}&access_token={admin_token}'
    resp = real_client.get(url)
    assert resp.status_code == 200
    body = _drain(resp, lambda b: ': ping' in b)
    assert ': connected' in body
    assert ': ping' in body
    assert 'batch_done' not in body
    resp.close()
