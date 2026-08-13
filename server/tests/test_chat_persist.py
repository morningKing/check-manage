"""Tests for utils.chat_persist."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _ev(etype, props):
    return {'event': etype, 'data': {'properties': props}}


def test_apply_event_accumulates_text_tool_and_detects_idle():
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    # assistant message announced -> records turn_msg_id
    assert apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc') is None
    assert s['turn_msg_id'] == 'm1'
    # text part
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'hi', 'sessionID': 'oc'}}), 'oc')
    # tool part
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p2', 'messageID': 'm1', 'type': 'tool', 'tool': 'query',
                  'state': {'status': 'completed', 'title': 'q', 'output': 'res'}, 'sessionID': 'oc'}}), 'oc')
    # idle ends the turn
    assert apply_event(s, _ev('session.idle', {'sessionID': 'oc'}), 'oc') == 'idle'
    content = build_content(s)
    assert content[0] == {'type': 'text', 'text': 'hi'}
    assert content[1]['type'] == 'tool_use'
    assert content[1]['name'] == 'query'
    assert content[1]['result'] == 'res'


def test_apply_event_captures_meta_and_tool_duration():
    from utils.chat_persist import new_state, apply_event, build_content
    from utils.ai_message_meta import aggregate_metas, public_meta
    s = new_state()
    # message.updated carrying time/tokens/cost (final completed snapshot)
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc',
                  'time': {'created': 1000, 'completed': 5400},
                  'tokens': {'input': 18886, 'output': 159}, 'cost': 0.002}}), 'oc')
    # tool part with state.time -> durationMs surfaced into content
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p2', 'messageID': 'm1', 'type': 'tool', 'tool': 'bash',
                  'state': {'status': 'completed', 'output': 'ok',
                            'time': {'start': 2000, 'end': 2123}}, 'sessionID': 'oc'}}), 'oc')
    content = build_content(s)
    tool = [p for p in content if p['type'] == 'tool_use'][0]
    assert tool['durationMs'] == 123
    # turn meta aggregates correctly
    meta = public_meta(aggregate_metas(list(s['meta_by_msg'].values())))
    assert meta == {'durationMs': 4400, 'tokensInput': 18886, 'tokensOutput': 159, 'cost': 0.002}


def test_apply_event_ignores_other_session():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'OTHER'}}), 'oc')
    assert s['turn_msg_id'] is None


def test_build_content_drops_empty_text():
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': '   ', 'sessionID': 'oc'}}), 'oc')
    assert build_content(s) == []


import contextlib


class _FakeCur:
    def __init__(self, sink): self._sink = sink
    def execute(self, sql, params=None): self._sink.append((sql, params))


def _fake_db(sink):
    @contextlib.contextmanager
    def _cm():
        class _Conn:
            def cursor(self): return _FakeCur(sink)
        yield _Conn()
    return _cm


def test_persist_turn_upserts_with_turn_msg_id(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    state = chat_persist.new_state()
    state['turn_msg_id'] = 'msg_A'
    state['part_order'] = ['p1']
    state['parts_by_id'] = {'p1': {'type': 'text', 'text': 'hi'}}
    chat_persist.persist_turn('sess1', state)
    assert len(sink) == 1
    sql, params = sink[0]
    assert 'ON CONFLICT (id) DO UPDATE' in sql
    assert params[0] == 'msg_A'      # deterministic row id = turn message id
    assert params[1] == 'sess1'


def test_persist_turn_noop_on_empty_content(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    chat_persist.persist_turn('sess1', chat_persist.new_state())  # no parts
    assert sink == []


def test_persist_turn_falls_back_to_generated_id(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    state = chat_persist.new_state()  # turn_msg_id stays None
    state['part_order'] = ['p1']
    state['parts_by_id'] = {'p1': {'type': 'text', 'text': 'hi'}}
    chat_persist.persist_turn('sess1', state)
    assert sink[0][1][0].startswith('msg_')   # generated fallback id


import threading as _threading
import time as _time


def test_run_listener_persists_then_exits_on_idle(monkeypatch):
    """The listener persists the turn on idle and then RETURNS, releasing the
    OpenCode /event subscription (one listener per turn). A second turn fed into
    the same iterator is left unconsumed — proving the early exit."""
    from utils import chat_persist
    saved = []
    monkeypatch.setattr(chat_persist, 'persist_turn',
                        lambda sid, state: saved.append((sid, chat_persist.build_content(state))))
    it = iter([
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'one', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm2', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p2', 'messageID': 'm2', 'type': 'text', 'text': 'two', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
    ])
    chat_persist._run_listener('sess1', 'oc', it)
    # only the first turn persisted; listener returned at its idle
    assert [c for _, c in saved] == [[{'type': 'text', 'text': 'one'}]]
    # the second turn's events were NOT consumed (stream released on exit)
    assert next(it)['data']['properties']['info']['id'] == 'm2'


def test_run_listener_persists_incrementally_while_streaming(monkeypatch):
    """Mid-turn (before idle) the listener upserts the partial answer once the
    debounce elapses, so switching sessions mid-stream recovers tool calls."""
    from utils import chat_persist
    saved = []
    monkeypatch.setattr(chat_persist, 'persist_turn',
                        lambda sid, state: saved.append(chat_persist.build_content(state)))
    clock = {'t': 0.0}

    def fake_monotonic():
        clock['t'] += 2.0  # each read advances 2s > 1.0s debounce
        return clock['t']

    monkeypatch.setattr(chat_persist.time, 'monotonic', fake_monotonic)
    events = [
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'partial', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p2', 'messageID': 'm1', 'type': 'tool', 'tool': 'q',
                                              'state': {'status': 'completed', 'output': 'r'}, 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
    ]
    chat_persist._run_listener('sess1', 'oc', iter(events))
    # at least one incremental persist before idle + the idle persist
    assert len(saved) >= 2
    # the latest persisted snapshot carries the tool call (not just text)
    assert any(p.get('type') == 'tool_use' for p in saved[-1])


def test_ensure_listener_dedups_per_session(monkeypatch):
    from utils import chat_persist
    started = []
    block = _threading.Event()

    def fake_target(sid, oc, d):
        started.append(sid)
        block.wait(2)

    monkeypatch.setattr(chat_persist, '_listener_thread', fake_target)
    try:
        chat_persist.ensure_listener('s1', 'oc1', '/ws')
        chat_persist.ensure_listener('s1', 'oc1', '/ws')  # already alive -> no second thread
        _time.sleep(0.1)
        assert started.count('s1') == 1
    finally:
        block.set()
        _time.sleep(0.05)
        chat_persist._listeners.pop('s1', None)


def test_apply_event_ignores_unrelated_session_still_works():
    """回归防护：新逻辑必须不影响"真正无关的会话被忽略"这个既有行为。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'totally-unrelated'}}), 'oc')
    assert s['turn_msg_id'] is None
    assert s['subtasks'] == {}


def test_apply_event_subtask_part_appears_in_parent_content():
    """核心断言：父级消息里出现 subtask part 之后，父级自己 flatten 出来的
    content 里必须真的有一条 subtask_use——这是 Task 6 渲染唯一的数据来源，
    只在 state['subtasks'] 里记追踪状态而不放进父级 content 就是白做。
    子代理的真实 sessionID 来自配套的 tool:'task' part。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    # tool:'task' part arrives first with the real child session ID
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    sig = apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    assert sig == 'subtask'
    content = build_content(s)
    stubs = [p for p in content if p['type'] == 'subtask_use']
    assert stubs == [{'type': 'subtask_use', 'subtaskId': 'ses_child1',
                      'agent': 'build', 'description': 'x', 'status': 'running'}]


def test_apply_event_discovers_subtask_with_depth_and_parent():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    assert s['subtasks']['ses_child1']['depth'] == 1
    assert s['subtasks']['ses_child1']['parent_id'] is None
    assert s['subtasks']['ses_child1']['status'] == 'running'


def test_apply_event_routes_child_events_into_subtask_scope():
    """子代理自己的 sessionID 上的事件，一旦它已被发现，要路由进它自己的
    累加器，而不是被当成"无关会话"丢弃。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1'}}), 'oc')
    sig = apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'text', 'text': 'working',
                  'sessionID': 'ses_child1'}}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['parts_by_id']['cp1'] == {'type': 'text', 'text': 'working'}


def test_apply_event_subtask_idle_marks_completed():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    sig = apply_event(s, _ev('session.idle', {'sessionID': 'ses_child1'}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['status'] == 'completed'


def test_apply_event_parent_stub_reflects_child_status_after_it_finishes():
    """状态更新不能只停留在 state['subtasks']——父级自己再次 flatten 时，
    占位气泡里的 status 必须跟着变成最新值，而不是停在发现时刻的 'running'。
    这就是"占位存原始 part、状态现查现填"这个设计要解决的问题，用一条测试
    钉死它。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    stubs = [p for p in build_content(s) if p['type'] == 'subtask_use']
    assert stubs[0]['status'] == 'running'
    apply_event(s, _ev('session.idle', {'sessionID': 'ses_child1'}), 'oc')
    stubs = [p for p in build_content(s) if p['type'] == 'subtask_use']
    assert stubs[0]['status'] == 'completed'


def test_apply_event_subtask_error_marks_failed_immediately():
    """子代理自己的轮次报错时立刻标 failed，不等 idle——透出原因，跟批任务
    那条 _TurnFailed 是同一个道理。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    sig = apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1',
                  'error': {'name': 'ProviderAuthError',
                           'data': {'providerID': 'anthropic', 'message': 'no key'}}}}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['status'] == 'failed'
    assert 'ProviderAuthError' in s['subtasks']['ses_child1']['error']
    assert 'anthropic' in s['subtasks']['ses_child1']['error']


def test_apply_event_nested_subtask_depth_increments():
    """子代理自己委托孙代——depth 从子代理的 depth 累加，不是恒为 1；孙代的
    占位 part 出现在**子代理自己**的 content 里（不是顶层的）。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    # Top-level discovers child via tool:'task' + subtask pair
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_child1'}},
                  'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'oc', 'id': 'prt_sub1',
                  'agent': 'build', 'description': 'x'}}), 'oc')
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1'}}), 'oc')
    # Child discovers grandchild via tool:'task' + subtask pair
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'cpt1', 'messageID': 'cm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_grandchild'}},
                  'sessionID': 'ses_child1'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'id': 'prt_gc1',
                  'agent': 'review', 'description': 'y'}}), 'oc')
    assert s['subtasks']['ses_grandchild']['depth'] == 2
    assert s['subtasks']['ses_grandchild']['parent_id'] == 'ses_child1'
    # 顶层 content 里只有对 ses_child1 的占位，没有孙代
    top_stub_ids = [p['subtaskId'] for p in build_content(s) if p['type'] == 'subtask_use']
    assert top_stub_ids == ['ses_child1']


def test_apply_event_depth_limit_stops_at_five():
    """超过 5 层不再追踪新的子代理——用一条深度已经是 5 的子代理去委托第 6 层，
    第 6 层不应该出现在 state['subtasks'] 里。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    # 手工把一个 depth=5 的子代理塞进去（不必真的建 5 层，直接构造边界条件）
    s['subtasks']['ses_deep'] = {
        'assistant_msg_ids': {'dm1'}, 'parts_by_id': {}, 'part_order': [],
        'parent_id': None, 'depth': 5, 'agent': 'x', 'description': 'y',
        'status': 'running', 'error': None,
        '_tool_sessions_by_msg': {}, '_pending_subtasks_by_msg': {},
    }
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'dpt1', 'messageID': 'dm1', 'type': 'tool', 'tool': 'task',
                  'state': {'status': 'completed',
                            'metadata': {'sessionId': 'ses_too_deep'}},
                  'sessionID': 'ses_deep'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'dp1', 'messageID': 'dm1', 'type': 'subtask',
                  'sessionID': 'ses_deep', 'id': 'prt_deep',
                  'agent': 'z', 'description': 'w'}}), 'oc')
    assert 'ses_too_deep' not in s['subtasks']


def test_persist_subtasks_writes_rows_and_messages():
    """persist_subtasks 要把 state['subtasks'] 里的内容落到
    ai_chat_subtasks / ai_chat_subtask_messages，而不是只停留在内存。"""
    import uuid
    from db import get_db
    from utils.chat_persist import new_state, apply_event, persist_subtasks

    uid = 'u-cp-sub-' + uuid.uuid4().hex[:6]
    sid = 's-cp-sub-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    try:
        s = new_state()
        apply_event(s, _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
        apply_event(s, _ev('message.part.updated',
            {'part': {'id': 'pt1', 'messageID': 'm1', 'type': 'tool', 'tool': 'task',
                      'state': {'status': 'completed',
                                'metadata': {'sessionId': 'ses_child_persist'}},
                      'sessionID': 'oc'}}), 'oc')
        apply_event(s, _ev('message.part.updated',
            {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                      'sessionID': 'oc', 'id': 'prt_sub_persist',
                      'agent': 'build',
                      'description': 'do x'}}), 'oc')
        apply_event(s, _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child_persist'}}), 'oc')
        apply_event(s, _ev('message.part.updated',
            {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'text', 'text': 'hi',
                      'sessionID': 'ses_child_persist'}}), 'oc')

        persist_subtasks(sid, s)

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT root_session_id, agent, description, status FROM "
                           "ai_chat_subtasks WHERE id = %s", ('ses_child_persist',))
                row = cur.fetchone()
                assert row == (sid, 'build', 'do x', 'running')
                cur.execute("SELECT content FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_persist',))
                content = cur.fetchone()[0]
                assert content == [{'type': 'text', 'text': 'hi'}]

        # 子代理跑完之后再落一次——同一行必须被更新为 completed，不是插入
        # 第二行（single-row-per-subtask upsert 的幂等性）。
        apply_event(s, _ev('session.idle', {'sessionID': 'ses_child_persist'}), 'oc')
        persist_subtasks(sid, s)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_chat_subtasks WHERE id = %s",
                           ('ses_child_persist',))
                assert cur.fetchone() == ('completed',)
                cur.execute("SELECT count(*) FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_persist',))
                assert cur.fetchone()[0] == 1   # 还是一行，不是两行
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
