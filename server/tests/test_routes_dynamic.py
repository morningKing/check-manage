"""
动态数据路由单元测试

测试 RESERVED 集合拦截和基本 CRUD 路由。
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
    """创建带 mock DB 的 Flask 测试应用"""
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
        patch('utils.operation_log.get_page_info', return_value=('page-test', '测试页面')),
        patch('utils.operation_log.pick_display_name', return_value='记录名'),
        patch('utils.operation_log.get_field_label_map', return_value={}),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    headers = {'Authorization': f'Bearer {token}'}

    yield app.test_client(), mock_cursor, mock_conn, headers

    for p in patches:
        p.stop()


class TestReservedCollections:
    """验证保留的集合名被正确拒绝"""

    def test_menus_reserved(self, setup):
        """menus 有自己的蓝图，GET /menus 不走 dynamic"""
        client, _, _, headers = setup
        resp = client.get('/menus', headers=headers)
        assert resp.status_code != 404

    def test_etlTasks_reserved(self, setup):
        """etlTasks 有自己的蓝图"""
        client, _, _, headers = setup
        resp = client.get('/etlTasks', headers=headers)
        assert resp.status_code != 404


class TestListCollection:
    def test_list_returns_records(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            ('rec-1', 'test-collection', {'name': '记录1'}, now),
        ]
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_list_empty_collection(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/my-data', headers=headers)
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestCreateRecord:
    def test_create_returns_201(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/test-collection',
                           data=json.dumps({'name': '新记录'}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 201


class TestUnauthorized:
    def test_no_token(self, setup):
        client, _, _, _ = setup
        resp = client.get('/test-collection')
        assert resp.status_code == 401
