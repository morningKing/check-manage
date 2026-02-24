"""
动态数据路由单元测试

测试 RESERVED 集合拦截、基本 CRUD 路由和乐观锁。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, call
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
        patch('utils.operation_log.get_page_info', return_value=('测试页面', [])),
        patch('routes.dynamic.get_page_info', return_value=('测试页面', [])),
        patch('utils.operation_log.pick_display_name', return_value='记录名'),
        patch('routes.dynamic.pick_display_name', return_value='记录名'),
        patch('utils.operation_log.get_field_label_map', return_value={}),
        patch('routes.dynamic.get_field_label_map', return_value={}),
        patch('routes.dynamic.log_operation'),
        patch('routes.dynamic.get_validation_script', return_value=None),
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
            ('rec-1', 'test-collection', {'name': '记录1'}, now, now, 1),
        ]
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_list_returns_version(self, setup):
        """列表接口返回 _version 字段"""
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            ('rec-1', 'test-collection', {'name': '记录1'}, now, now, 3),
        ]
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data[0]['_version'] == 3

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

    def test_create_returns_version_1(self, setup):
        """新建记录返回 _version=1"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/test-collection',
                           data=json.dumps({'name': '新记录'}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['_version'] == 1


class TestUpdateRecord:
    def test_update_increments_version(self, setup):
        """更新记录后 version 递增"""
        client, mock_cursor, _, headers = setup
        # fetchone 调用序列：
        # 1. get_primary_key_fields → None (无主键配置)
        # 2. SELECT data, version → 返回旧数据和版本号
        mock_cursor.fetchone.side_effect = [
            None,                                  # pk_fields: no page config
            ({'name': '旧记录'}, 2),               # old data + version
        ]
        mock_cursor.rowcount = 1  # UPDATE affected 1 row
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 2}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['_version'] == 3

    def test_update_version_conflict(self, setup):
        """客户端版本不匹配时返回 409"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            None,                                  # pk_fields
            ({'name': '旧记录'}, 5),               # old data + version=5
        ]
        # 客户端携带 _version=3，但数据库已经是 version=5
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 3}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 409
        data = resp.get_json()
        assert data['code'] == 'VERSION_CONFLICT'

    def test_update_without_version_still_works(self, setup):
        """不携带 _version 的更新请求仍然成功（向后兼容）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            None,                                  # pk_fields
            ({'name': '旧记录'}, 1),               # old data + version
        ]
        mock_cursor.rowcount = 1
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名'}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 200

    def test_update_race_condition(self, setup):
        """版本匹配但 UPDATE rowcount=0 时返回 409（竞态条件）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            None,                                  # pk_fields
            ({'name': '旧记录'}, 2),               # old data + version
        ]
        mock_cursor.rowcount = 0  # UPDATE 匹配0行（被其他请求先改了）
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 2}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 409

    def test_update_nonexistent_record(self, setup):
        """更新不存在的记录返回 404"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            None,   # pk_fields
            None,   # old data → record not found
        ]
        resp = client.put('/test-collection/rec-999',
                          data=json.dumps({'name': '新名', '_version': 1}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 404


class TestUnauthorized:
    def test_no_token(self, setup):
        client, _, _, _ = setup
        resp = client.get('/test-collection')
        assert resp.status_code == 401
