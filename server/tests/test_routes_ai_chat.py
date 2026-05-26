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
