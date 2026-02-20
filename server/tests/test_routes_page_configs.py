"""
页面配置路由单元测试

测试页面配置 CRUD。
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


class TestListPageConfigs:
    def test_returns_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('page-1', '测试页', '描述', '/testData', [], now, now, [], [], False, None),
        ]
        resp = client.get('/pageConfigs', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_no_token_401(self, setup):
        client, _, _, _ = setup
        resp = client.get('/pageConfigs')
        assert resp.status_code == 401


class TestGetPageConfig:
    def test_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            'page-1', '测试页', '描述', '/testData', [], now, now, [], [], False, None,
        )
        resp = client.get('/pageConfigs/page-1', headers=admin_h)
        assert resp.status_code == 200
        assert resp.get_json()['id'] == 'page-1'

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/pageConfigs/nonexistent', headers=admin_h)
        assert resp.status_code == 404


class TestCreatePageConfig:
    def test_create_success(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/pageConfigs',
                           data=json.dumps({
                               'id': 'page-new',
                               'name': '新页面',
                               'description': '描述',
                               'apiEndpoint': '/newData',
                               'fields': [],
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.post('/pageConfigs',
                           data=json.dumps({'id': 'x', 'name': 'x'}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403


class TestUpdatePageConfig:
    def test_update_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            'page-1', '更新页', '描述', '/testData', [], now, now, [], [], False, None,
        )
        resp = client.put('/pageConfigs/page-1',
                          data=json.dumps({'name': '更新页'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.put('/pageConfigs/nonexistent',
                          data=json.dumps({'name': 'x'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 404


class TestDeletePageConfig:
    def test_delete_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('测试页',)
        resp = client.delete('/pageConfigs/page-1', headers=admin_h)
        assert resp.status_code == 200
