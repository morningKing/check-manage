"""HTTP tests for /ai/chat/prompt-templates."""
import os
import sys
import json
import uuid
import pytest
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager
from datetime import datetime, timezone

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
        patch('utils.prompt_template.get_db', fake_db),
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


def test_create_and_list(setup):
    client, cursor, auth_headers = setup

    # Mock: create returns a template row
    template_id = str(uuid.uuid4())
    template_row = {
        'id': template_id,
        'user_id': 'user-admin',
        'name': 't1',
        'content': 'hello',
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
    }

    # Setup cursor to work with get_db context manager
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None

    # POST (create) - fetchone returns the inserted row
    cursor.fetchone.return_value = template_row
    cursor.rowcount = 1

    r = client.post('/ai/chat/prompt-templates',
                    json={'name': 't1', 'content': 'hello'},
                    headers=auth_headers)
    assert r.status_code == 201
    tpl = r.get_json()
    assert tpl['name'] == 't1' and tpl['content'] == 'hello'

    # GET (list) - fetchall returns all templates
    cursor.fetchall.return_value = [template_row]

    r = client.get('/ai/chat/prompt-templates', headers=auth_headers)
    assert r.status_code == 200
    items = r.get_json()
    assert any(t['id'] == template_id for t in items)


def test_duplicate_name_returns_409(setup):
    client, cursor, auth_headers = setup

    # Setup cursor
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None

    # First create succeeds
    template_id = str(uuid.uuid4())
    template_row = {
        'id': template_id,
        'user_id': 'user-admin',
        'name': 'dup',
        'content': 'a',
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
    }

    from psycopg2.errors import UniqueViolation

    # First POST succeeds
    cursor.execute.side_effect = None
    cursor.fetchone.return_value = template_row

    r1 = client.post('/ai/chat/prompt-templates',
                     json={'name': 'dup', 'content': 'a'}, headers=auth_headers)
    assert r1.status_code == 201

    # Second POST with same name raises UniqueViolation
    cursor.execute.side_effect = UniqueViolation()
    r2 = client.post('/ai/chat/prompt-templates',
                     json={'name': 'dup', 'content': 'b'}, headers=auth_headers)
    assert r2.status_code == 409


def test_update_round_trip(setup):
    client, cursor, auth_headers = setup

    template_id = str(uuid.uuid4())
    new_row = {
        'id': template_id,
        'user_id': 'user-admin',
        'name': 'u',
        'content': 'new',
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
    }

    # Setup cursor
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    cursor.fetchone.return_value = new_row

    r = client.put(f'/ai/chat/prompt-templates/{template_id}',
                   json={'name': 'u', 'content': 'new'}, headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['content'] == 'new'


def test_delete(setup):
    client, cursor, auth_headers = setup

    template_id = str(uuid.uuid4())

    # Setup cursor
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None

    # For delete: rowcount > 0 indicates success
    cursor.rowcount = 1

    r = client.delete(f'/ai/chat/prompt-templates/{template_id}', headers=auth_headers)
    assert r.status_code == 204

    # For get (after delete): returns None
    cursor.rowcount = 0
    cursor.fetchone.return_value = None

    r2 = client.get(f'/ai/chat/prompt-templates/{template_id}', headers=auth_headers)
    assert r2.status_code == 404


def test_rejects_unauthenticated(setup):
    client, _, _ = setup
    r = client.get('/ai/chat/prompt-templates')
    assert r.status_code == 401
