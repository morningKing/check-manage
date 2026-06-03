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
        patch('routes.dynamic.get_db', fake_db),
        patch('utils.permissions.get_db', fake_db),
        patch('utils.version.get_db', fake_db),
        patch('utils.branch_lock.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    from app import app
    app.config['TESTING'] = True
    yield app.test_client(), mock_cursor, perms
    for p in patches:
        p.stop()


def _token(role):
    return {'Authorization': f'Bearer {create_token({"id": "u", "username": "u", "role": role})}'}


def test_guest_create_forbidden(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('guest', False, 'read')  # role row for resolution
    cur.fetchall.side_effect = [[], []]                   # admin_keys, page_perms
    resp = client.post('/orders', data=json.dumps({'name': 'x'}),
                       content_type='application/json', headers=_token('guest'))
    assert resp.status_code == 403


def test_developer_create_allowed_passes_gate(setup):
    client, cur, perms = setup
    # role resolution: developer/write; first fetchone resolves the role row, then
    # downstream handler queries (e.g. branch-lock menu lookup) see None so the
    # create_item proceeds (will 4xx later for other reasons, but NOT 403 from the
    # permission gate).
    cur.fetchone.side_effect = [('developer', False, 'write')] + [None] * 20
    cur.fetchall.side_effect = [[], []]
    resp = client.post('/orders', data=json.dumps({}),
                       content_type='application/json', headers=_token('developer'))
    assert resp.status_code != 403


def test_role_with_no_access_cannot_read(setup):
    client, cur, perms = setup
    cur.fetchone.return_value = ('locked', False, 'none')  # default none
    cur.fetchall.side_effect = [[], []]
    resp = client.get('/orders', headers=_token('locked'))
    assert resp.status_code == 403


def test_default_read_allows_list(setup):
    client, cur, perms = setup
    # First fetchone resolves the role row; the get_item data query then sees None
    # and returns 404 — passing the read gate (not 403).
    cur.fetchone.side_effect = [('guest', False, 'read')] + [None] * 20
    cur.fetchall.side_effect = [[], []]
    resp = client.get('/orders/some-id', headers=_token('guest'))
    # passes the read gate (may 404 later, but not 403)
    assert resp.status_code != 403
