import sys, os, json
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
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.roles.get_db', fake_db),
        patch('utils.permissions.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    # superuser admin so require_permission('admin.roles') passes
    mock_cursor.fetchone.return_value = ('admin', True, 'write')
    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    yield app.test_client(), mock_cursor, {'Authorization': f'Bearer {admin}'}
    for p in patches:
        p.stop()


def test_get_catalog(setup):
    client, _, headers = setup
    resp = client.get('/roles/catalog', headers=headers)
    assert resp.status_code == 200
    keys = [e['key'] for e in resp.get_json()]
    assert 'admin.users' in keys


def test_list_roles(setup):
    client, cur, headers = setup
    # superuser check uses fetchone; list query uses fetchall
    cur.fetchall.return_value = [
        ('admin', '管理员', '', True, True, 'write'),
        ('guest', '访客', '', True, False, 'read'),
    ]
    resp = client.get('/roles', headers=headers)
    assert resp.status_code == 200
    assert len(resp.get_json()) == 2


def test_delete_system_role_blocked(setup):
    client, cur, headers = setup
    # superuser check (fetchone #1) then role lookup (fetchone #2)
    cur.fetchone.side_effect = [('admin', True, 'write'), ('管理员', True)]
    resp = client.delete('/roles/admin', headers=headers)
    assert resp.status_code == 400


def test_delete_role_in_use_blocked(setup):
    client, cur, headers = setup
    cur.fetchone.side_effect = [('admin', True, 'write'), ('质检员', False)]
    cur.fetchall.return_value = [('zhang',), ('li',)]
    resp = client.delete('/roles/role-abc', headers=headers)
    assert resp.status_code == 409
