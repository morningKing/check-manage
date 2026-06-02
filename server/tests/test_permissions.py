import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.permissions import PERMISSION_CATALOG, catalog_keys


def test_catalog_has_expected_keys():
    keys = catalog_keys()
    assert 'admin.users' in keys
    assert 'admin.roles' in keys
    assert 'admin.backup' in keys
    # every entry has key + label + group
    for entry in PERMISSION_CATALOG:
        assert entry['key'] and entry['label'] and entry['group']


def test_catalog_keys_are_unique():
    keys = catalog_keys()
    assert len(keys) == len(set(keys))


from unittest.mock import MagicMock, patch
from contextlib import contextmanager
import utils.permissions as perms


def _mock_db(role_row, admin_rows, page_rows):
    """role_row: tuple or None; admin_rows: list[(key,)]; page_rows: list[(page_id,r,c,u,d)]"""
    cur = MagicMock()
    # fetchone for the roles row; fetchall called twice (admin keys, then page perms)
    cur.fetchone.return_value = role_row
    cur.fetchall.side_effect = [admin_rows, page_rows]
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: None

    @contextmanager
    def fake_get_db():
        yield conn
    return fake_get_db


def setup_function():
    perms.invalidate_cache()


def test_superuser_bypasses_everything():
    fake = _mock_db(('admin', True, 'write'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('admin', 'admin.anything') is True
        assert perms.can_page('admin', 'page-orders', 'delete') is True


def test_admin_key_grant():
    fake = _mock_db(('developer', False, 'write'), [('admin.etl_tasks',)], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('developer', 'admin.etl_tasks') is True
        assert perms.can_admin('developer', 'admin.users') is False


def test_page_default_fallback_read():
    fake = _mock_db(('guest', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('guest', 'page-orders', 'read') is True
        assert perms.can_page('guest', 'page-orders', 'update') is False


def test_page_default_fallback_write():
    fake = _mock_db(('developer', False, 'write'), [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('developer', 'page-orders', 'create') is True
        assert perms.can_page('developer', 'page-orders', 'delete') is True


def test_page_explicit_row_overrides_default():
    fake = _mock_db(('developer', False, 'write'), [],
                    [('page-orders', True, False, True, False)])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_page('developer', 'page-orders', 'update') is True
        assert perms.can_page('developer', 'page-orders', 'create') is False
        # other pages still use default
        assert perms.can_page('developer', 'page-other', 'create') is True


def test_unknown_role_denies():
    fake = _mock_db(None, [], [])
    with patch('utils.permissions.get_db', fake):
        assert perms.can_admin('ghost', 'admin.users') is False
        assert perms.can_page('ghost', 'page-orders', 'read') is False


def test_cache_invalidation_reloads():
    fake1 = _mock_db(('developer', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake1):
        assert perms.can_page('developer', 'page-x', 'create') is False
    # change underlying data to 'write', but cache still holds old value
    fake2 = _mock_db(('developer', False, 'write'), [], [])
    with patch('utils.permissions.get_db', fake2):
        assert perms.can_page('developer', 'page-x', 'create') is False  # cached
        perms.invalidate_cache('developer')
        assert perms.can_page('developer', 'page-x', 'create') is True   # reloaded


from flask import Flask, jsonify
from auth import require_permission, create_token


def _app_with_protected_route():
    app = Flask(__name__)

    @app.route('/protected')
    @require_permission('admin.users')
    def protected():
        return jsonify({'ok': True})

    app.config['TESTING'] = True
    return app


def test_require_permission_allows_granted():
    app = _app_with_protected_route()
    token = create_token({'id': 'u1', 'username': 'dev', 'role': 'developer'})
    fake = _mock_db(('developer', False, 'read'), [('admin.users',)], [])
    with patch('utils.permissions.get_db', fake):
        perms.invalidate_cache()
        resp = app.test_client().get('/protected', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200


def test_require_permission_denies_ungranted():
    app = _app_with_protected_route()
    token = create_token({'id': 'u1', 'username': 'guest', 'role': 'guest'})
    fake = _mock_db(('guest', False, 'read'), [], [])
    with patch('utils.permissions.get_db', fake):
        perms.invalidate_cache()
        resp = app.test_client().get('/protected', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403


def test_require_permission_requires_login():
    app = _app_with_protected_route()
    resp = app.test_client().get('/protected')
    assert resp.status_code == 401
