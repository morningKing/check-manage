"""
系统配置 API 测试

测试 GET/PUT /system-config 路由的权限控制和功能。
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
        patch('routes.system_config.get_db', fake_db),
        patch('db.pool', MagicMock()),
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


class TestGetSystemConfig:
    def test_get_system_config(self, setup):
        """测试获取系统配置"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = {
            'system_name': '测试系统',
            'system_short_name': '测试',
            'logo_url': None,
        }
        resp = client.get('/system-config', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'systemName' in data
        assert 'systemShortName' in data
        assert data['systemName'] == '测试系统'
        assert data['systemShortName'] == '测试'

    def test_get_system_config_not_found(self, setup):
        """测试系统配置不存在返回404"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/system-config', headers=admin_h)
        assert resp.status_code == 404


class TestUpdateSystemConfig:
    def test_update_system_config_admin(self, setup):
        """测试管理员更新系统配置"""
        client, mock_cursor, admin_h, _ = setup
        # 模拟配置存在检查
        mock_cursor.fetchone.return_value = (1,)  # SELECT id FROM system_config WHERE id = 1
        resp = client.put('/system-config',
            data=json.dumps({'systemName': '新系统名称', 'systemShortName': '新简称'}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['systemName'] == '新系统名称'
        assert data['systemShortName'] == '新简称'

    def test_update_system_config_non_admin(self, setup):
        """测试非管理员无法更新系统配置"""
        client, _, _, dev_h = setup
        resp = client.put('/system-config',
            data=json.dumps({'systemName': '测试', 'systemShortName': '测试'}),
            content_type='application/json',
            headers=dev_h
        )
        assert resp.status_code == 403

    def test_update_system_config_empty_name(self, setup):
        """测试空名称返回错误"""
        client, mock_cursor, admin_h, _ = setup
        resp = client.put('/system-config',
            data=json.dumps({'systemName': '', 'systemShortName': '简称'}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_system_config_empty_short_name(self, setup):
        """测试空简称返回错误"""
        client, mock_cursor, admin_h, _ = setup
        resp = client.put('/system-config',
            data=json.dumps({'systemName': '系统名称', 'systemShortName': ''}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_system_config_not_found(self, setup):
        """测试更新时配置不存在返回404"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None  # 配置不存在
        resp = client.put('/system-config',
            data=json.dumps({'systemName': '新系统', 'systemShortName': '新简称'}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 404