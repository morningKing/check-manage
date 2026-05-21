"""
列视图路由单元测试

测试列视图 API 的 CRUD 操作、权限控制等。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


# Reusable view row tuple matching SELECT_COLUMNS order
def _view_row(view_id=1, page_id='page-1', name='默认视图',
              is_public=True, creator_id='u1', is_default=False,
              columns=None, sort_config=None, filter_config=None,
              group_config=None, created_at=None, updated_at=None):
    ts = created_at or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (
        view_id, page_id, name, is_public, creator_id, is_default,
        columns or [], sort_config or [], filter_config or [],
        group_config, ts, updated_at or ts,
    )


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.auth.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('routes.column_views.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True

    admin = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'u3', 'username': 'guest', 'role': 'guest'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
    )

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# GET /column-views/<page_id>/views
# ---------------------------------------------------------------------------
class TestListViews:
    def test_returns_views(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        # First fetchall: views list; second fetchone: default view id
        mock_cursor.fetchall.return_value = [
            _view_row(view_id=1, name='公开视图', is_public=True),
        ]
        mock_cursor.fetchone.return_value = (1,)
        resp = client.get('/column-views/page-1/views', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'views' in data
        assert 'defaultViewId' in data
        assert len(data['views']) == 1
        assert data['views'][0]['name'] == '公开视图'
        assert data['defaultViewId'] == 1

    def test_no_token_401(self, setup):
        client, _, _, _, _ = setup
        resp = client.get('/column-views/page-1/views')
        assert resp.status_code == 401

    def test_returns_empty_list(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None
        resp = client.get('/column-views/page-1/views', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['views'] == []
        assert data['defaultViewId'] is None


# ---------------------------------------------------------------------------
# POST /column-views/<page_id>/views
# ---------------------------------------------------------------------------
class TestCreateView:
    def test_admin_create_public_view(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        # First fetchone: page exists check; second: name uniqueness; third (RETURNING): new row
        mock_cursor.fetchone.side_effect = [
            ('page-1',),  # page exists
            None,         # no duplicate name
            _view_row(view_id=10, name='新视图', is_public=True, creator_id='u1'),
        ]
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '新视图', 'isPublic': True, 'columns': ['col1']}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['name'] == '新视图'
        assert data['isPublic'] is True

    def test_developer_create_private_view(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            ('page-1',),  # page exists
            None,         # no duplicate name
            _view_row(view_id=11, name='我的视图', is_public=False, creator_id='u2'),
        ]
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '我的视图', 'isPublic': False}),
            content_type='application/json',
            headers=dev_h,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['isPublic'] is False

    def test_developer_cannot_create_public_view(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        # fetchone: page exists check (might not even be reached)
        mock_cursor.fetchone.return_value = ('page-1',)
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '公开视图', 'isPublic': True}),
            content_type='application/json',
            headers=dev_h,
        )
        assert resp.status_code == 403

    def test_guest_cannot_create_view(self, setup):
        client, _, _, _, guest_h = setup
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '新视图'}),
            content_type='application/json',
            headers=guest_h,
        )
        assert resp.status_code == 403

    def test_empty_name_400(self, setup):
        client, _, admin_h, _, _ = setup
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '  '}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 400

    def test_duplicate_name_400(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.side_effect = [
            ('page-1',),       # page exists
            (5,),              # duplicate name found
        ]
        resp = client.post(
            '/column-views/page-1/views',
            data=json.dumps({'name': '已存在视图', 'isPublic': True}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 400

    def test_page_not_found_404(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None  # page not found
        resp = client.post(
            '/column-views/nonexistent/views',
            data=json.dumps({'name': '新视图'}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /column-views/<page_id>/views/<view_id>
# ---------------------------------------------------------------------------
class TestUpdateView:
    def test_owner_can_update(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        # u2 (developer) owns this view
        view = _view_row(view_id=1, name='旧名称', is_public=False, creator_id='u2')
        mock_cursor.fetchone.side_effect = [
            view,             # fetch existing view
            None,             # no duplicate name
            _view_row(view_id=1, name='新名称', is_public=False, creator_id='u2'),
        ]
        resp = client.put(
            '/column-views/page-1/views/1',
            data=json.dumps({'name': '新名称'}),
            content_type='application/json',
            headers=dev_h,
        )
        assert resp.status_code == 200
        assert resp.get_json()['name'] == '新名称'

    def test_admin_can_update_any_view(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        # View owned by u2, but admin (u1) is updating
        view = _view_row(view_id=1, name='旧名称', is_public=False, creator_id='u2')
        mock_cursor.fetchone.side_effect = [
            view,
            None,
            _view_row(view_id=1, name='管理员改名', is_public=False, creator_id='u2'),
        ]
        resp = client.put(
            '/column-views/page-1/views/1',
            data=json.dumps({'name': '管理员改名'}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 200

    def test_other_user_gets_403(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        # View owned by u1 (admin), developer (u2) trying to update
        view = _view_row(view_id=1, name='管理员视图', is_public=False, creator_id='u1')
        mock_cursor.fetchone.return_value = view
        resp = client.put(
            '/column-views/page-1/views/1',
            data=json.dumps({'name': 'hacked'}),
            content_type='application/json',
            headers=dev_h,
        )
        assert resp.status_code == 403

    def test_view_not_found_404(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.put(
            '/column-views/page-1/views/999',
            data=json.dumps({'name': '新名称'}),
            content_type='application/json',
            headers=admin_h,
        )
        assert resp.status_code == 404

    def test_no_token_401(self, setup):
        client, _, _, _, _ = setup
        resp = client.put(
            '/column-views/page-1/views/1',
            data=json.dumps({'name': 'x'}),
            content_type='application/json',
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /column-views/<page_id>/views/<view_id>
# ---------------------------------------------------------------------------
class TestDeleteView:
    def test_owner_can_delete(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        view = _view_row(view_id=1, name='我的视图', is_public=False,
                         creator_id='u2', is_default=False)
        mock_cursor.fetchone.return_value = view
        resp = client.delete('/column-views/page-1/views/1', headers=dev_h)
        assert resp.status_code == 200

    def test_cannot_delete_default_view(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        view = _view_row(view_id=1, name='默认视图', is_public=True,
                         creator_id='u1', is_default=True)
        mock_cursor.fetchone.return_value = view
        resp = client.delete('/column-views/page-1/views/1', headers=admin_h)
        assert resp.status_code == 400

    def test_view_not_found_404(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.delete('/column-views/page-1/views/999', headers=admin_h)
        assert resp.status_code == 404

    def test_other_user_gets_403(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        # View owned by u1, developer u2 trying to delete
        view = _view_row(view_id=1, name='管理员视图', is_public=False,
                         creator_id='u1', is_default=False)
        mock_cursor.fetchone.return_value = view
        resp = client.delete('/column-views/page-1/views/1', headers=dev_h)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /column-views/<page_id>/views/<view_id>/default
# ---------------------------------------------------------------------------
class TestSetDefault:
    def test_admin_can_set_default(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        view = _view_row(view_id=1, name='公开视图', is_public=True, creator_id='u1')
        mock_cursor.fetchone.return_value = view
        resp = client.put('/column-views/page-1/views/1/default', headers=admin_h)
        assert resp.status_code == 200
        assert '默认视图' in resp.get_json()['message']

    def test_developer_gets_403(self, setup):
        client, _, _, dev_h, _ = setup
        resp = client.put('/column-views/page-1/views/1/default', headers=dev_h)
        assert resp.status_code == 403

    def test_cannot_set_private_view_as_default(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        view = _view_row(view_id=1, name='私有视图', is_public=False, creator_id='u1')
        mock_cursor.fetchone.return_value = view
        resp = client.put('/column-views/page-1/views/1/default', headers=admin_h)
        assert resp.status_code == 400

    def test_view_not_found_404(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.put('/column-views/page-1/views/999/default', headers=admin_h)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /column-views/<page_id>/views/<view_id>/copy
# ---------------------------------------------------------------------------
class TestCopyView:
    def test_creates_copy(self, setup):
        client, mock_cursor, _, dev_h, _ = setup
        source = _view_row(view_id=1, name='原始视图', is_public=True,
                           creator_id='u1', columns=['col1', 'col2'])
        mock_cursor.fetchone.side_effect = [
            source,    # fetch source view
            None,      # name uniqueness check ("原始视图 - 副本" not found)
            _view_row(view_id=20, name='原始视图 - 副本', is_public=False, creator_id='u2'),
        ]
        resp = client.post('/column-views/page-1/views/1/copy', headers=dev_h)
        assert resp.status_code == 201
        data = resp.get_json()
        assert '副本' in data['name']
        assert data['isPublic'] is False

    def test_source_not_found_404(self, setup):
        client, mock_cursor, admin_h, _, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/column-views/page-1/views/999/copy', headers=admin_h)
        assert resp.status_code == 404

    def test_no_token_401(self, setup):
        client, _, _, _, _ = setup
        resp = client.post('/column-views/page-1/views/1/copy')
        assert resp.status_code == 401
