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


def test_run_listener_persists_each_idle(monkeypatch):
    from utils import chat_persist
    saved = []
    monkeypatch.setattr(chat_persist, 'persist_turn',
                        lambda sid, state: saved.append((sid, chat_persist.build_content(state))))
    events = [
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'one', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm2', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p2', 'messageID': 'm2', 'type': 'text', 'text': 'two', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
    ]
    chat_persist._run_listener('sess1', 'oc', iter(events))
    assert [c for _, c in saved] == [[{'type': 'text', 'text': 'one'}], [{'type': 'text', 'text': 'two'}]]


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
