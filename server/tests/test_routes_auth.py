"""
认证路由单元测试

测试登录、获取当前用户、修改密码。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from werkzeug.security import generate_password_hash

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

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
    )

    for p in patches:
        p.stop()


class TestLogin:
    def test_missing_username(self, setup):
        client, _, _ = setup
        resp = client.post('/auth/login',
                           data=json.dumps({'username': '', 'password': '123456'}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_missing_password(self, setup):
        client, _, _ = setup
        resp = client.post('/auth/login',
                           data=json.dumps({'username': 'admin', 'password': ''}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_user_not_found(self, setup):
        client, mock_cursor, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/auth/login',
                           data=json.dumps({'username': 'nobody', 'password': '123'}),
                           content_type='application/json')
        assert resp.status_code == 401

    def test_wrong_password(self, setup):
        client, mock_cursor, _ = setup
        mock_cursor.fetchone.return_value = (
            'user-admin', 'admin', generate_password_hash('correct'), '管理员', 'admin',
        )
        resp = client.post('/auth/login',
                           data=json.dumps({'username': 'admin', 'password': 'wrong'}),
                           content_type='application/json')
        assert resp.status_code == 401

    def test_login_success(self, setup):
        client, mock_cursor, _ = setup
        mock_cursor.fetchone.return_value = (
            'user-admin', 'admin', generate_password_hash('123456'), '管理员', 'admin',
        )
        resp = client.post('/auth/login',
                           data=json.dumps({'username': 'admin', 'password': '123456'}),
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert data['user']['username'] == 'admin'


class TestGetCurrentUser:
    def test_returns_user_info(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchone.return_value = ('user-admin', 'admin', '管理员', 'admin')
        resp = client.get('/auth/me', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['username'] == 'admin'

    def test_user_not_found(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/auth/me', headers=admin_h)
        assert resp.status_code == 404

    def test_no_token_401(self, setup):
        client, _, _ = setup
        resp = client.get('/auth/me')
        assert resp.status_code == 401


class TestChangePassword:
    def test_missing_fields(self, setup):
        client, _, admin_h = setup
        resp = client.put('/auth/password',
                          data=json.dumps({'oldPassword': '', 'newPassword': ''}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 400

    def test_new_password_too_short(self, setup):
        client, _, admin_h = setup
        resp = client.put('/auth/password',
                          data=json.dumps({'oldPassword': 'old123', 'newPassword': '12'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 400

    def test_wrong_old_password(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchone.return_value = (generate_password_hash('correct'),)
        resp = client.put('/auth/password',
                          data=json.dumps({'oldPassword': 'wrong', 'newPassword': 'newpwd123'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 400

    def test_change_password_success(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchone.return_value = (generate_password_hash('old123'),)
        resp = client.put('/auth/password',
                          data=json.dumps({'oldPassword': 'old123', 'newPassword': 'new123'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200
