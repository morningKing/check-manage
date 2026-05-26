"""Tests for server/routes/ai_chat.py."""

import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor, tmp_path):
    fake_db = _make_mock_db(mock_conn)
    fake_client = MagicMock()
    fake_client.create_session.return_value = "oc_sess_42"
    fake_client.register_mcp.return_value = None

    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.ai_chat.get_db', fake_db),
        patch('utils.session_token.get_db', fake_db),
        patch('routes.ai_chat.OpenCodeClient', return_value=fake_client),
        patch('config.AI_WORKSPACE_ROOT', str(tmp_path)),
        patch('routes.ai_chat.AI_WORKSPACE_ROOT', str(tmp_path)),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    dev = create_token({'id': 'user-1', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'user-2', 'username': 'g', 'role': 'guest'})

    yield (
        app.test_client(), mock_cursor, fake_client,
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
        tmp_path,
    )

    for p in patches:
        p.stop()


def test_create_session_201_returns_id_title_workspace(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=dev_h)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['id'].startswith('sess_')
    assert body['title'] == '新会话'
    assert 'workspacePath' in body
    oc.create_session.assert_called_once()
    oc.register_mcp.assert_called_once()


def test_create_session_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=guest_h)
    assert resp.status_code == 403


def test_send_message_persists_user_and_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # Make the session lookup succeed
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hello agent'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    oc.send_prompt_async.assert_called_once_with('oc_sess_42', 'hello agent')

    # An INSERT into ai_chat_messages must have happened
    inserts = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("INSERT INTO ai_chat_messages" in s for s in inserts)


def test_send_message_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None  # not found for this user
    resp = client.post(
        '/ai/chat/sessions/sess_other/messages',
        json={'content': 'hi'},
        headers=dev_h,
    )
    assert resp.status_code == 404


def test_get_messages_returns_history(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # owner check + history fetch
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')
    cursor.fetchall.return_value = [
        ('msg_1', 'user',      [{'type': 'text', 'text': 'hi'}],   None),
        ('msg_2', 'assistant', [{'type': 'text', 'text': 'hey'}],  None),
    ]
    resp = client.get('/ai/chat/sessions/sess_x/messages', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['messages']) == 2
    assert body['messages'][0]['role'] == 'user'


def test_sse_events_returns_event_stream_headers(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')

    # OpenCode iterator yields one event then stops
    oc.subscribe_events.return_value = iter([
        {'event': 'message.part.delta', 'data': {'text': 'hi'}},
    ])

    resp = client.get('/ai/chat/sessions/sess_x/events', headers=dev_h)
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('text/event-stream')
    body = b''.join(resp.response).decode('utf-8')
    assert 'event: message.part.delta' in body
    assert '"text": "hi"' in body
