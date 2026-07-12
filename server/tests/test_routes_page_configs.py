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

    def test_delete_marks_field_indexes_dropping(self, setup):
        """删除页面配置要连带清理该 collection 下的字段索引，否则物理索引永久残留。"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('测试页',)
        resp = client.delete('/pageConfigs/page-1', headers=admin_h)
        assert resp.status_code == 200
        drop_calls = [
            c for c in mock_cursor.execute.call_args_list
            if c.args and "status = 'dropping'" in str(c.args[0])
        ]
        assert len(drop_calls) == 1
        assert drop_calls[0].args[1] == ('1',)


class TestFieldIndexSyncOnSave:
    """保存字段配置时，indexed 标记要同步进 field_indexes（见 utils/field_indexes.py）。"""

    def test_newly_indexed_field_inserts_pending_row(self, setup):
        client, mock_cursor, admin_h, _ = setup
        new_fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': True}]
        mock_cursor.fetchone.side_effect = [
            None,  # has_data check -> no data yet, fields not locked
            ('page-1', '测试页', '描述', '/testData', new_fields, now, now, [], [], False, None),  # final SELECT
        ]
        mock_cursor.fetchall.return_value = []  # sync_field_indexes: no existing tracked fields
        resp = client.put('/pageConfigs/page-1',
                          data=json.dumps({'fields': new_fields}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'INSERT INTO field_indexes' in str(c.args[0])
        ]
        assert len(insert_calls) == 1
        args = insert_calls[0].args[1]
        assert args[1] == 'status'
        assert args[3] == 'pending'

    def test_toggling_indexed_only_is_allowed_even_when_fields_locked(self, setup):
        """indexed 是纯性能开关，不影响已存数据的形状，应该在已有数据的页面上也能改。"""
        client, mock_cursor, admin_h, _ = setup
        old_fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': False}]
        new_fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': True}]
        mock_cursor.fetchone.side_effect = [
            (1,),                     # has_data check -> truthy, data exists
            (old_fields,),            # current fields (for lock comparison)
            ('page-1', '测试页', '描述', '/testData', new_fields, now, now, [], [], False, None),  # final SELECT
        ]
        mock_cursor.fetchall.return_value = []
        resp = client.put('/pageConfigs/page-1',
                          data=json.dumps({'fields': new_fields}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200

    def test_changing_other_attribute_alongside_indexed_still_locked(self, setup):
        """indexed 的豁免只覆盖它自己；同一字段的其他属性变化仍然要拒绝。"""
        client, mock_cursor, admin_h, _ = setup
        old_fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': False, 'label': '状态'}]
        new_fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': True, 'label': '新状态'}]
        mock_cursor.fetchone.side_effect = [
            (1,),
            (old_fields,),
        ]
        resp = client.put('/pageConfigs/page-1',
                          data=json.dumps({'fields': new_fields}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 409
        assert resp.get_json()['code'] == 'FIELDS_LOCKED'


class TestFieldIndexStatusEndpoint:
    def test_returns_status_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('status', 'ready', None, now, now),
        ]
        resp = client.get('/pageConfigs/page-1/field-indexes', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data == [{
            'fieldName': 'status',
            'status': 'ready',
            'error': None,
            'requestedAt': '2024-01-01T00:00:00.000Z',
            'readyAt': '2024-01-01T00:00:00.000Z',
        }]
