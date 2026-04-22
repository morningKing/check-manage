"""
菜单路由单元测试

测试菜单 CRUD 和内置菜单保护逻辑。
"""

import sys
import os
import json
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
    """创建带 mock DB 的 Flask 测试应用"""
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
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
    admin = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
    )

    for p in patches:
        p.stop()


class TestListMenus:
    def test_returns_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('menu-1', '首页', 'House', None, None, 0, '/home', '["admin"]', None, 'system', None),
        ]
        resp = client.get('/menus', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_developer_can_list(self, setup):
        client, mock_cursor, _, dev_h = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/menus', headers=dev_h)
        assert resp.status_code == 200


class TestCreateMenu:
    def test_create_menu(self, setup):
        client, mock_cursor, admin_h, _ = setup
        # 创建工作空间菜单（一级菜单）
        resp = client.post('/menus',
                           data=json.dumps({
                               'id': 'menu-new',
                               'name': '新工作空间',
                               'icon': 'Star',
                               'path': '/new',
                               'menuType': 'workspace',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_create_under_system_config_rejected(self, setup):
        """不允许在系统菜单下添加子菜单"""
        client, mock_cursor, admin_h, _ = setup
        # 系统菜单不允许添加子菜单
        mock_cursor.fetchone.return_value = ('menu-3-a', 'system')

        resp = client.post('/menus',
                           data=json.dumps({
                               'id': 'menu-forbidden',
                               'name': '禁止菜单',
                               'parentId': 'menu-3-a',
                               'menuType': 'data',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        # 验证父级类型失败返回 400
        assert resp.status_code == 400

    def test_developer_cannot_create(self, setup):
        client, _, _, dev_h = setup
        resp = client.post('/menus',
                           data=json.dumps({'id': 'x', 'name': 'x'}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403


class TestUpdateMenu:
    def test_update_builtin_rejected(self, setup):
        """系统菜单不允许编辑"""
        client, _, admin_h, _ = setup

        with patch('routes.menus._is_system_menu', return_value=True):
            resp = client.put('/menus/menu-1',
                              data=json.dumps({'name': '改名'}),
                              content_type='application/json',
                              headers=admin_h)
        assert resp.status_code == 403

    def test_update_normal_menu(self, setup):
        """普通菜单可以编辑"""
        client, _, admin_h, _ = setup

        with patch('routes.menus._is_system_menu', return_value=False):
            resp = client.put('/menus/menu-custom',
                              data=json.dumps({'name': '改名'}),
                              content_type='application/json',
                              headers=admin_h)
        assert resp.status_code == 200


class TestDeleteMenu:
    def test_delete_builtin_rejected(self, setup):
        """系统菜单不允许删除"""
        client, _, admin_h, _ = setup

        with patch('routes.menus._is_system_menu', return_value=True):
            resp = client.delete('/menus/menu-1', headers=admin_h)
        assert resp.status_code == 403

    def test_delete_normal_menu(self, setup):
        """普通菜单可以删除"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('普通菜单',)

        with patch('routes.menus._is_system_menu', return_value=False):
            resp = client.delete('/menus/menu-custom', headers=admin_h)
        assert resp.status_code == 200
