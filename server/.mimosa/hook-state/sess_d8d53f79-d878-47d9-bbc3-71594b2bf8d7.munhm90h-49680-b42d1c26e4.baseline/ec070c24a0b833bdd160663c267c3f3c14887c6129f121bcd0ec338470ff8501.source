"""Tests for POST /ai/chat/batches/staging/upload."""
import io
import os
import sys
import pytest
import uuid
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
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
    )

    for p in patches:
        p.stop()


def test_upload_returns_name_and_path(setup, tmp_path, monkeypatch):
    client, cursor, auth_headers = setup
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))

    data = {
        'file': (io.BytesIO(b'hello bytes'), 'test.txt'),
        'upload_session_id': 'sess-abc',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 201
    j = r.get_json()
    assert j['name'] == 'test.txt'
    # path is workspace-relative, normalized
    assert j['path'].startswith('batch-staging/')
    assert j['path'].endswith('test.txt')


def test_upload_requires_upload_session_id(setup, tmp_path, monkeypatch):
    client, cursor, auth_headers = setup
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))

    data = {'file': (io.BytesIO(b'x'), 'a.txt')}
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 400


def test_upload_rejects_traversal_filename(setup, tmp_path, monkeypatch):
    client, cursor, auth_headers = setup
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))

    data = {
        'file': (io.BytesIO(b'x'), '../../escape.txt'),
        'upload_session_id': 'sess-trav',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=auth_headers)
    assert r.status_code == 400 or r.status_code == 201
    if r.status_code == 201:
        # If accepted, the path must NOT have escaped batch-staging/
        assert '..' not in r.get_json()['path']


def test_upload_rejects_unauthenticated(setup, tmp_path, monkeypatch):
    client, cursor, auth_headers = setup
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))

    data = {
        'file': (io.BytesIO(b'x'), 'a.txt'),
        'upload_session_id': 'sess',
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data')
    assert r.status_code == 401
