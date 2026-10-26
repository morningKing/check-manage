"""对外公开接管蓝图 /external/takeover/*（无 JWT，接管令牌 + 双层限速）。

模式照抄 test_open_api_takeover_link.py：认证网关
（takeover_repo.resolve_takeover_token）整体打桩；batch_repo /
batch_engine.get_worker / operation_log._write_log_row 打桩做行为隔离；
限速走真 RateLimiter——每测换新实例 + 调低阈值，确定性无睡眠。
"""
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes import external_takeover as ext  # noqa: E402  (RED: 模块尚不存在)

BASE = '/external/takeover'
VALID_T = 'tk_ok'


# ---------------------------------------------------------------------------
# 合跑隔离（回归修复，同 test_open_api_takeover_link.py）：conftest 的 `app`
# 夹具先 patch db.get_db/db.pool 再 `from app import app`——进程内第一次用到
# 它时，整个 app 导入图在 patch 窗口内首次加载，顶层 `from db import get_db`
# 的模块（auth / utils.ai_query / utils.memory / utils.gate_policy_repo /
# utils.agent_ledger / routes.* …）从此永久绑住 yield 一次性 mock_conn 的
# lambda，patch 退出只还原 db.get_db 本身。同进程后续真库用例（P0 门禁四例）
# 会读到假连接：merged_action_checks 查无批次 → applicable=0 → 门禁被误判
# 'skipped'（应为 'inconclusive'）。已有 Heal（conftest 只覆盖 4 个模块、
# P0 的 _rebind_real_db 不认 _default/_default_get_db 别名）覆盖不到，
# 本套件每个用例前后统一把泄漏绑定指回真实 db.get_db。
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _heal_leaked_get_db_bindings():
    import db as _db

    def _heal():
        real = _db.get_db
        for name, mod in list(sys.modules.items()):
            if mod is None or not (name.startswith(('routes.', 'utils.'))
                                   or name in ('auth', 'app')):
                continue
            for attr in ('get_db', '_default', '_default_get_db'):
                cur = getattr(mod, attr, None)
                if cur is not None and cur is not real and callable(cur):
                    try:
                        setattr(mod, attr, real)
                    except (AttributeError, TypeError):
                        pass
        # app 夹具留下的 MagicMock 池：下次 get_db() 需重建真池
        if hasattr(_db.pool, '_mock_name'):
            _db.pool = None

    _heal()
    yield
    _heal()


def _tok(**over):
    row = {
        'token_id': 'tkt_1',
        'owner_user_id': 'user-42',
        'batch_id': 'b1',
        'session_id': 's1',
        'batch_seq': 3,
        'batch_input_file': 'input.xlsx',
        'batch_name': 'B1',
        'title': '第3步',
        'child_status': 'completed',
        'batch_status': 'running',
        'expires_at': datetime.now(timezone.utc) + timedelta(hours=24),
    }
    row.update(over)
    return row


@contextmanager
def _fake_db(mock_conn):
    yield mock_conn


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """每测换新限速桶：用例间互不串桶，确定性无睡眠。"""
    from utils.rate_limit import RateLimiter
    monkeypatch.setattr(ext, '_limiter', RateLimiter())


@pytest.fixture(autouse=True)
def _cursor_enter_self(mock_conn):
    """`with conn.cursor() as cur` 在裸 MagicMock 下绑定的是
    __enter__.return_value（新造的自动 mock），conftest 配置的 fetchone
    根本到不了被测代码——把 __enter__ 指回同一个 mock_cursor。"""
    cur = mock_conn.cursor.return_value
    cur.__enter__.return_value = cur


def _owner_row():
    """_log_takeover 里 SELECT username, role FROM users 的返回。"""
    return ('alice', 'admin')


def test_invalid_token_404_no_store(client, mock_cursor):
    """令牌缺失/空/伪造一律同一 404 体（resolve 桩返回 None 即覆盖
    过期/吊销——蓝图侧不得产生存在性预言机）。"""
    for qs in ('', '?t=', f'?t={VALID_T}'):
        mock_cursor.reset_mock()
        with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
                   return_value=None) as rt:
            resp = client.get(f'{BASE}/session{qs}')
        assert resp.status_code == 404
        assert resp.get_json() == {'error': '链接无效或已过期'}
        assert resp.headers['Cache-Control'] == 'no-store'
        assert rt.call_args.args == ({'': None, '?t=': ''}.get(qs, VALID_T),)


def test_session_info_field_shape(client, mock_conn, mock_cursor):
    tok = _tok()
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=tok), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)):
        resp = client.get(f'{BASE}/session?t={VALID_T}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {'batchName', 'batchSeq', 'inputFile', 'title',
                         'childStatus', 'batchStatus', 'expiresAt'}
    assert body == {
        'batchName': 'B1', 'batchSeq': 3, 'inputFile': 'input.xlsx',
        'title': '第3步', 'childStatus': 'completed', 'batchStatus': 'running',
        'expiresAt': tok['expires_at'].isoformat(),
    }


def test_messages_passthrough_four_fields(client, mock_conn, mock_cursor):
    """行只透传 id/role/content/createdAt（createdAt ISO 化），多余字段丢弃；
    childStatus 现读 DB 而非 resolve 时快照；total/truncated 原样。"""
    dt = datetime(2026, 10, 25, 12, 0, 0)
    msgs = {'messages': [
        {'id': 'm1', 'role': 'user', 'content': 'hi', 'created_at': dt,
         'seq': 7},
        {'id': 'm2', 'role': 'assistant', 'content': 'yo', 'created_at': None,
         'seq': 8},
    ], 'truncated': True, 'total': 9}
    mock_cursor.fetchone.return_value = ('needs_review',)
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=_tok()), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)), \
         patch('routes.external_takeover.batch_repo.get_child_messages',
               return_value=msgs) as gm:
        resp = client.get(f'{BASE}/session/messages?t={VALID_T}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert gm.call_args.args == ('s1',)
    assert body['childStatus'] == 'needs_review'
    assert body['total'] == 9 and body['truncated'] is True
    assert body['messages'][0] == {'id': 'm1', 'role': 'user', 'content': 'hi',
                                   'createdAt': dt.isoformat()}
    assert body['messages'][1]['createdAt'] is None
    for m in body['messages']:
        assert set(m) == {'id', 'role', 'content', 'createdAt'}


def _send_patches(mock_conn, tok, status, owner=None):
    """发送端点的公共打桩：resolve 放行、_child_status_fresh 读到 `status`、
    _log_takeover 的属主查询读到 owner（默认 alice/admin）。"""
    owner = owner or _owner_row()
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.side_effect = [(status,), owner]
    return (
        patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
              return_value=tok),
        patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)),
        patch('routes.external_takeover.batch_repo.continue_child'),
        patch('utils.batch_engine.get_worker', return_value=MagicMock()),
        patch('utils.operation_log._write_log_row'),
    )


def test_send_running_409_continue_not_called(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'running')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    assert resp.get_json() == {
        'error': {'code': 'TASK_RUNNING',
                  'message': '任务执行中，请外部系统先暂停后再接管'}}
    cc.assert_not_called()
    gw.assert_not_called()


def test_send_pending_409(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'pending')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    cc.assert_not_called()


def test_send_paused_repark_true_and_notify(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert resp.get_json() == {'queued': True}
    assert cc.call_args.args == ('user-42', 'b1', 's1', '接着做')
    assert cc.call_args.kwargs == {'repark': True}
    gw.assert_called_once_with()
    gw.return_value.notify.assert_called_once_with()


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled',
                                    'needs_review'])
def test_send_terminal_repark_false(client, mock_conn, mock_cursor, status):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), status)
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert cc.call_args.kwargs == {'repark': False}


def test_send_unknown_status_404(client, mock_conn, mock_cursor):
    """会话已消失（重读为空）→ 与无效令牌同一 404 体。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), '')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 404
    assert resp.get_json() == {'error': '链接无效或已过期'}
    cc.assert_not_called()


def test_send_state_conflict_409(client, mock_conn, mock_cursor):
    """continue_child 内部 CAS 失败（ValueError）→ 409 STATE_CONFLICT。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        cc.side_effect = ValueError('子任务状态已变化，请刷新后重试')
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'STATE_CONFLICT'
    gw.return_value.notify.assert_not_called()


@pytest.mark.parametrize('payload', [{}, {'prompt': ''}, {'prompt': '   '}])
def test_send_prompt_empty_400(client, mock_conn, mock_cursor, payload):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}', json=payload)
    assert resp.status_code == 400
    cc.assert_not_called()


def test_send_prompt_too_long_400_and_boundary_ok(client, mock_conn,
                                                  mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': 'x' * 20001})
        assert resp.status_code == 400
        cc.assert_not_called()
        # 边界：恰好 20000 字符放行
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': 'x' * 20000})
    assert resp.status_code == 202


def test_send_rate_limited_second_hit_429(client, mock_conn, mock_cursor,
                                          monkeypatch):
    """真 RateLimiter：阈值调到 1/min，第二击 429（同令牌同 IP）。"""
    monkeypatch.setattr(ext, 'SEND_PER_MINUTE', 1)
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc, p_gw, p_wl:
        first = client.post(f'{BASE}/session/messages?t={VALID_T}',
                            json={'prompt': '第一击'})
        second = client.post(f'{BASE}/session/messages?t={VALID_T}',
                             json={'prompt': '第二击'})
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.get_json() == {'error': '请求过于频繁，请稍后再试'}
    assert second.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('bad', [123, ['接着做'], {'k': 1}, True])
def test_send_prompt_non_string_400(client, mock_conn, mock_cursor, bad):
    """公开输入不做假设：非字符串 prompt 类型门 400，不得到达 strip/500。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'completed')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': bad})
    assert resp.status_code == 400
    cc.assert_not_called()


@pytest.mark.parametrize('break_what', ['owner_select', 'log_writer'])
def test_send_oplog_failure_never_breaks_202(client, mock_conn, mock_cursor,
                                             caplog, break_what):
    """oplog 链路任何一环炸掉都只留日志：continue_child 已提交、notify 已触发
    之后，users SELECT 或 _write_log_row 抛异常不得把发送变成 500。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl as wl:
        if break_what == 'owner_select':
            mock_cursor.fetchone.side_effect = [('paused',),
                                                RuntimeError('users boom')]
        else:
            wl.side_effect = RuntimeError('writer boom')
        with caplog.at_level(logging.ERROR):
            resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                               json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert resp.get_json() == {'queued': True}
    assert cc.call_args.kwargs == {'repark': True}
    gw.return_value.notify.assert_called_once_with()
    assert any('takeover oplog failed' in rec.getMessage()
               for rec in caplog.records)


def test_read_bucket_independent_of_send_bucket(client, mock_conn, mock_cursor,
                                                monkeypatch):
    """终审 I-1：读/发分桶——轮询连续 GET 撞读桶 429 时不得殃及发送桶
    （前端 3s 轮询 20/min 若共桶会烧光 10/min 发送预算与 200/日桶）。"""
    monkeypatch.setattr(ext, 'READ_PER_MINUTE', 3)
    tok = _tok()
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=tok), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)):
        codes = [client.get(f'{BASE}/session?t={VALID_T}').status_code
                 for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        # 429 后读桶不再放行（固定窗口语义自证）
        assert client.get(f'{BASE}/session?t={VALID_T}').status_code == 429
    # 同窗口内发送不受读桶影响：发送桶独立计数
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(mock_conn, tok, 'completed')
    with p_tok, p_db, p_cc, p_gw, p_wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202


def test_default_read_budget_safe_for_polling(client, mock_conn, monkeypatch):
    """默认 60/min 读预算下连续 20 次读全 200——钉住轮询安全余量
    （3s 间隔 → 20/min），防止未来把读桶又收窄到轮询预算以下。无 sleep。"""
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=_tok()), \
         patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)):
        for _ in range(20):
            resp = client.get(f'{BASE}/session?t={VALID_T}')
            assert resp.status_code == 200, f'unexpected {resp.status_code}'


def test_log_takeover_writes_with_owner_identity(client, mock_conn,
                                                 mock_cursor):
    """发消息以令牌属主身份落 oplog：_write_log_row 收到
    (owner_user_id, username, role, action, 'ai_chat_session', session_id, ...)。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _send_patches(
        mock_conn, _tok(), 'paused')
    with p_tok, p_db, p_cc as cc, p_gw, p_wl as wl:
        resp = client.post(f'{BASE}/session/messages?t={VALID_T}',
                           json={'prompt': '接着做'})
    assert resp.status_code == 202
    assert wl.call_args.args[:7] == (
        'user-42', 'alice', 'admin', 'ai.batch_takeover_message',
        'ai_chat_session', 's1', '第3步')
    assert 'prev_status=paused' in wl.call_args.args[7]
    assert '接管对话' in wl.call_args.args[7]


# ---------------------------------------------------------------------------
# 接管页中断 POST /session/cancel：状态机全权交给 cancel_child（打桩断言
# 转发与收尾），本套件只对蓝图自身的契约负责——guard/限速/notify/oplog/
# 错误映射。cancel_child 内部语义（pending/running 置标志、paused 同步、
# 终态 ValueError）由 test_batch_takeover_repo 覆盖。
# ---------------------------------------------------------------------------
def _cancel_patches(mock_conn, tok, post_status='running', owner=None):
    """中断端点的公共打桩：resolve 放行、cancel_child 后的重读读到
    `post_status`、_log_takeover 的属主查询读到 owner（默认 alice/admin）。"""
    owner = owner or _owner_row()
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.side_effect = [(post_status,), owner]
    return (
        patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
              return_value=tok),
        patch('routes.external_takeover.get_db', lambda: _fake_db(mock_conn)),
        patch('routes.external_takeover.batch_repo.cancel_child'),
        patch('utils.batch_engine.get_worker', return_value=MagicMock()),
        patch('utils.operation_log._write_log_row'),
    )


def test_cancel_running_202_notify_and_oplog(client, mock_conn, mock_cursor):
    p_tok, p_db, p_cc, p_gw, p_wl = _cancel_patches(
        mock_conn, _tok(), post_status='running')
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl as wl:
        resp = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    assert resp.status_code == 202
    assert resp.get_json() == {'queued': True, 'childStatus': 'running'}
    assert cc.call_args.args == ('user-42', 'b1', 's1')
    gw.assert_called_once_with()
    gw.return_value.notify.assert_called_once_with()
    assert wl.call_args.args[3] == 'ai.batch_takeover_cancel'
    assert '接管中断' in wl.call_args.args[7]


def test_cancel_terminal_409_notify_not_called(client, mock_conn, mock_cursor):
    """终态子任务（cancel_child 抛 ValueError）→ 409 STATE_CONFLICT，
    worker 不被打扰、oplog 不落（什么都没发生）。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _cancel_patches(mock_conn, _tok())
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl as wl:
        cc.side_effect = ValueError('子任务已结束（completed），无法取消')
        resp = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    assert resp.status_code == 409
    assert resp.get_json() == {'error': {
        'code': 'STATE_CONFLICT', 'message': '子任务已结束（completed），无法取消'}}
    gw.assert_not_called()
    wl.assert_not_called()


def test_cancel_vanished_404(client, mock_conn, mock_cursor):
    """resolve 与 cancel 之间批次/会话被删（cancel_child 返回 None）→
    与无效令牌同一 404 体，不产生存在性预言机。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _cancel_patches(mock_conn, _tok())
    with p_tok, p_db, p_cc as cc, p_gw as gw, p_wl:
        cc.return_value = None
        resp = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    assert resp.status_code == 404
    assert resp.get_json() == {'error': '链接无效或已过期'}
    gw.assert_not_called()


def test_cancel_invalid_token_404(client, mock_cursor):
    with patch('routes.external_takeover.takeover_repo.resolve_takeover_token',
               return_value=None):
        resp = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    assert resp.status_code == 404
    assert resp.get_json() == {'error': '链接无效或已过期'}


def test_cancel_oplog_failure_never_breaks_202(client, mock_conn, mock_cursor,
                                               caplog):
    """与发送同纪律：oplog 任何一环失败只留日志，中断请求本身 202。"""
    p_tok, p_db, p_cc, p_gw, p_wl = _cancel_patches(mock_conn, _tok())
    with p_tok, p_db, p_cc, p_gw as gw, p_wl:
        mock_cursor.fetchone.side_effect = [('running',),
                                            RuntimeError('users boom')]
        with caplog.at_level(logging.ERROR):
            resp = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    assert resp.status_code == 202
    gw.return_value.notify.assert_called_once_with()
    assert any('takeover oplog failed' in rec.getMessage()
               for rec in caplog.records)


def test_cancel_shares_send_bucket_with_messages(client, mock_conn,
                                                 mock_cursor, monkeypatch):
    """中断是写侧动作，消耗发送桶（10/min 与消息同预算）：一次中断后，
    同窗口内的接管消息被 429 拒——预算共享是文档化行为，钉住防回归。"""
    monkeypatch.setattr(ext, 'SEND_PER_MINUTE', 1)
    tok = _tok()
    p_tok, p_db, p_cc, p_gw, p_wl = _cancel_patches(mock_conn, tok)
    with p_tok, p_db, p_cc, p_gw, p_wl:
        first = client.post(f'{BASE}/session/cancel?t={VALID_T}')
    p_tok2, p_db2, p_cc2, p_gw2, p_wl2 = _send_patches(mock_conn, tok, 'paused')
    with p_tok2, p_db2, p_cc2, p_gw2, p_wl2:
        second = client.post(f'{BASE}/session/messages?t={VALID_T}',
                             json={'prompt': '接着做'})
    assert first.status_code == 202
    assert second.status_code == 429
