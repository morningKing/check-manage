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
