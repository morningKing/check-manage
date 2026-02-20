"""
用户管理路由单元测试

测试用户 CRUD 和角色权限控制。
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
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'user-dev', 'username': 'dev', 'role': 'developer'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
    )

    for p in patches:
        p.stop()


now = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestListUsers:
    def test_admin_can_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('u1', 'admin', '管理员', 'admin', now),
        ]
        resp = client.get('/users', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.get('/users', headers=dev_h)
        assert resp.status_code == 403


class TestCreateUser:
    def test_missing_fields(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/users',
                           data=json.dumps({'username': '', 'password': '', 'displayName': ''}),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 400

    def test_invalid_role(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/users',
                           data=json.dumps({
                               'username': 'new', 'password': '123456',
                               'displayName': '新用户', 'role': 'superadmin',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 400

    def test_password_too_short(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/users',
                           data=json.dumps({
                               'username': 'new', 'password': '12',
                               'displayName': '新用户', 'role': 'guest',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 400

    def test_duplicate_username(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.side_effect = [('existing-id',), None]
        resp = client.post('/users',
                           data=json.dumps({
                               'username': 'admin', 'password': '123456',
                               'displayName': '新用户', 'role': 'guest',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 409

    def test_create_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            None,  # no duplicate
            ('u-new', 'newuser', '新用户', 'guest', now),  # created row
        ]
        resp = client.post('/users',
                           data=json.dumps({
                               'username': 'newuser', 'password': '123456',
                               'displayName': '新用户', 'role': 'guest',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.post('/users',
                           data=json.dumps({
                               'username': 'test', 'password': '123456',
                               'displayName': 'test', 'role': 'guest',
                           }),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403


class TestUpdateUser:
    def test_invalid_role(self, setup):
        client, _, admin_h, _ = setup
        resp = client.put('/users/u1',
                          data=json.dumps({'role': 'invalid'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 400

    def test_update_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('u1', 'admin', '更新名', 'admin', now)
        resp = client.put('/users/u1',
                          data=json.dumps({'displayName': '更新名'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200


class TestDeleteUser:
    def test_cannot_delete_self(self, setup):
        client, _, admin_h, _ = setup
        resp = client.delete('/users/user-admin', headers=admin_h)
        assert resp.status_code == 400

    def test_user_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.delete('/users/u-nonexistent', headers=admin_h)
        assert resp.status_code == 404

    def test_delete_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('otheruser', '其他用户')
        resp = client.delete('/users/u-other', headers=admin_h)
        assert resp.status_code == 200
