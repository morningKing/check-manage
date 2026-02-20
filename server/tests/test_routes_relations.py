"""
关联关系路由单元测试

测试关联关系 GET/PUT/DELETE。
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

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
    )

    for p in patches:
        p.stop()


class TestGetRelations:
    def test_returns_grouped_relations(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchall.return_value = [
            ('fieldA', 'id-1'),
            ('fieldA', 'id-2'),
            ('fieldB', 'id-3'),
        ]
        resp = client.get('/relations/testCol/rec-1', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['fieldA'] == ['id-1', 'id-2']
        assert data['fieldB'] == ['id-3']

    def test_empty_relations(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/relations/testCol/rec-1', headers=admin_h)
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_no_token_401(self, setup):
        client, _, _ = setup
        resp = client.get('/relations/testCol/rec-1')
        assert resp.status_code == 401


class TestUpdateRelations:
    def test_update_success(self, setup):
        client, mock_cursor, admin_h = setup
        # get_page_info returns
        mock_cursor.fetchone.side_effect = [
            ('测试页', [{'fieldName': 'refField', 'label': '关联字段'}]),  # src page info
            ({'name': '数据1'},),  # src record data
            ('目标页', []),  # tgt page info
        ]
        mock_cursor.fetchall.return_value = []  # old relations

        resp = client.put('/relations/testCol/rec-1/refField',
                          data=json.dumps({
                              'targetCollection': 'targetCol',
                              'targetField': 'backRef',
                              'ids': ['t-1', 't-2'],
                          }),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data['ids']) == {'t-1', 't-2'}


class TestDeleteAllRelations:
    def test_delete_success(self, setup):
        client, mock_cursor, admin_h = setup
        mock_cursor.fetchone.side_effect = [
            ('测试页', []),  # page info
            ({'name': '数据1'},),  # record data
        ]
        resp = client.delete('/relations/testCol/rec-1', headers=admin_h)
        assert resp.status_code == 200
