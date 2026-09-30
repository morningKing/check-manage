"""
操作日志路由单元测试

测试日志列出（分页/筛选）和删除。
"""

import sys
import os
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
        patch('routes.operation_logs.get_db', fake_db),
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


now = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestListOperationLogs:
    def test_returns_paginated(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (5,)  # total count
        mock_cursor.fetchall.return_value = [
            ('log-1', 'create', 'menu', 'menu-1', '首页', '新增菜单',
             'u1', 'admin', 'admin', now, None, None),
        ]
        resp = client.get('/operationLogs?page=1&pageSize=20', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'items' in data
        assert 'total' in data
        assert data['total'] == 5

    def test_with_filters(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/operationLogs?action=create&targetType=menu', headers=admin_h)
        assert resp.status_code == 200

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.get('/operationLogs', headers=dev_h)
        assert resp.status_code == 403


class TestDeleteOperationLog:
    def test_delete_success(self, setup):
        client, _, admin_h, _ = setup
        resp = client.delete('/operationLogs/log-1', headers=admin_h)
        assert resp.status_code == 200

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.delete('/operationLogs/log-1', headers=dev_h)
        assert resp.status_code == 403
