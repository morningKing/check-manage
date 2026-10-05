"""StubClient 事件总线与门面 subscribe_events 的形状/语义锁。

铁律对齐 StubRuntime 消息形状锁先例：事件形状漂移 = sse_events 路由
静默丢事件，压测全链路作废。本文件同时是门面接线的判别力锚：
在 base（无门面方法）上 Step1 的 test_facade_delegates 必然 AttributeError。
"""
import queue
import threading
import time

import pytest


@pytest.fixture()
def stub(monkeypatch):
    monkeypatch.setenv('AI_STUB_ALLOW', '1')
    from utils.runtime.stub import StubRuntime
    rt = StubRuntime(profile={'delay_ms': [10, 20], 'tool_parts': 2})
    yield rt


def _collect(gen, want_types, timeout=5.0):
    """从 subscribe_events 生成器收集到指定事件类型为止。线程内跑，
    主线程 join——生成器阻塞在 q.get 时 GC 不了，必须显式驱动。"""
    out = []
    got = threading.Event()

    def run():
        for evt in gen:
            out.append(evt)
            if evt['event'] in want_types:
                got.set()
                break

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert got.wait(timeout), f'超时未等到 {want_types}，已收: {[e["event"] for e in out]}'
    return out


def test_timeline_order_and_shapes(stub):
    oc = stub.create_session(directory='/ws/t1')
    gen = stub.get_client().subscribe_events(directory='/ws/t1')
    stub.dispatch(oc, 'hello', directory='/ws/t1')
    evts = _collect(gen, {'session.idle'})
    types = [e['event'] for e in evts]
    # 顺序契约：message.updated 先于 part 事件（apply_event 只有见到
    # assistant message id 后才捕获 part），idle 收尾
    assert types[0] == 'message.updated', types
    assert types[-1] == 'session.idle', types
    assert types.count('message.part.updated') == 3, types   # 2 tool + 1 text
    for e in evts:
        assert e['data']['type'] == e['event']               # OpenCode 形状
        assert e['data']['properties']['sessionID'] == oc    # 路由键：event_session_id 首选读它
    msg_id = evts[0]['data']['properties']['info']['id']
    for e in evts[1:-1]:
        assert e['data']['properties']['part']['messageID'] == msg_id
    # REST 视图与事件视图 parts 一致（worker 轮询与 SSE 双路径消费同一事实）
    rest = stub.get_messages(oc)
    assert rest[-1]['info']['finish'] == 'stop'
    assert len(rest[-1]['parts']) == 3


def test_directory_isolation_and_fanout(stub):
    oc_a = stub.create_session(directory='/ws/a')
    stub.create_session(directory='/ws/b')
    got_a, got_b1, got_b2 = [], [], []
    stop = threading.Event()

    def pump(gen, sink):
        for evt in gen:
            sink.append(evt)
            if stop.is_set():
                return

    threads = [threading.Thread(target=pump, args=(g, s), daemon=True) for g, s in
               ((stub.get_client().subscribe_events(directory='/ws/a'), got_a),
                (stub.get_client().subscribe_events(directory='/ws/b'), got_b1),
                (stub.get_client().subscribe_events(directory='/ws/b'), got_b2))]
    for t in threads:
        t.start()
    stub.dispatch(oc_a, 'hello', directory='/ws/a')
    deadline = threading.Event()
    # 等 a 收齐一轮
    for _ in range(100):
        if any(e['event'] == 'session.idle' for e in got_a):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    stop.set()
    assert any(e['event'] == 'session.idle' for e in got_a)
    assert got_b1 == [] and got_b2 == [], 'b 目录订阅者不该收到 a 的事件'
    assert len(got_a) == 5, f'a 流帧数 {len(got_a)} != 5（fanout 缺失或多发）'


def test_no_replay_for_late_subscriber(stub):
    oc = stub.create_session(directory='/ws/late')
    stub.dispatch(oc, 'hello', directory='/ws/late')
    time.sleep(0.3)                                   # 第一轮已完整发完
    gen = stub.get_client().subscribe_events(directory='/ws/late')
    stub.dispatch(oc, 'second', directory='/ws/late')
    evts = _collect(gen, {'session.idle'})
    assert all(e['data']['properties'].get('info', {}).get('id') !=
               evts[0]['data']['properties']['info']['id'] or e is evts[0]
               for e in evts)                          # 只含第二轮（简断言：帧数=5）
    assert len(evts) == 5


def test_facade_delegates_to_client(monkeypatch):
    from utils.runtime.base import AgentRuntime

    class FakeClient:
        def subscribe_events(self, directory='', read_timeout=None):
            return iter([('sentinel', directory, read_timeout)])

    class FakeRT(AgentRuntime):
        def get_client(self):
            return FakeClient()

    assert list(FakeRT().subscribe_events('/ws/x', read_timeout=3)) == \
        [('sentinel', '/ws/x', 3)]
