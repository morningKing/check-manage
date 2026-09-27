"""Tests for the /query/* endpoints."""

import pytest
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_header():
    from auth import create_token
    token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


MOCK_PAGE_CONFIGS = [
    (
        'page-inspection-case',
        '巡检用例',
        [
            {'fieldName': 'caseid', 'label': '用例ID', 'controlType': 'text'},
            {'fieldName': 'caseName', 'label': '用例名称', 'controlType': 'text'},
            {'fieldName': 'caseType', 'label': '用例类型', 'controlType': 'select',
             'options': [{'label': '功能', 'value': 'func'}, {'label': '性能', 'value': 'perf'}]},
            {'fieldName': 'templateids', 'label': '模板', 'controlType': 'relation',
             'relationConfig': {'targetCollection': 'template'}},
        ],
    ),
    (
        'page-template',
        '模板',
        [
            {'fieldName': 'templatename', 'label': '模板名称', 'controlType': 'text'},
        ],
    ),
]


class TestListCollections:
    @patch('routes.query.get_db')
    def test_list_collections(self, mock_db, client, auth_header):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        mock_cur.fetchall.return_value = MOCK_PAGE_CONFIGS

        resp = client.get('/query/collections', headers=auth_header)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        # Each item should have collection, name, fields
        item = next(i for i in data if i['collection'] == 'inspection-case')
        assert item['name'] == '巡检用例'
        assert len(item['fields']) == 4
        # Relation field should have targetCollection
        rel_field = next(f for f in item['fields'] if f['fieldName'] == 'templateids')
        assert rel_field['targetCollection'] == 'template'


class TestExecuteQuery:
    @patch('routes.query.get_db')
    def test_basic_query(self, mock_db, client, auth_header):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        # Mock page_configs
        mock_cur.fetchall.side_effect = [
            MOCK_PAGE_CONFIGS,    # _get_all_page_configs
            [],                   # main query rows
        ]
        mock_cur.fetchone.return_value = (0,)  # count

        body = {
            'collection': 'inspection-case',
            'query': {'用例ID': {'$regex': 'IC'}},
            'limit': 10,
        }

        resp = client.post(
            '/query/execute',
            data=json.dumps(body),
            content_type='application/json',
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'data' in data
        assert 'total' in data
        assert 'columns' in data

    def test_missing_collection(self, client, auth_header):
        body = {'query': {}}
        resp = client.post(
            '/query/execute',
            data=json.dumps(body),
            content_type='application/json',
            headers=auth_header,
        )
        assert resp.status_code == 400

    @patch('routes.query.get_db')
    def test_invalid_query_syntax(self, mock_db, client, auth_header):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        mock_cur.fetchall.return_value = MOCK_PAGE_CONFIGS

        body = {
            'collection': 'inspection-case',
            'query': {'caseid': {'$badop': 1}},
        }

        resp = client.post(
            '/query/execute',
            data=json.dumps(body),
            content_type='application/json',
            headers=auth_header,
        )
        assert resp.status_code == 400
        assert '语法错误' in resp.get_json()['error']

    @patch('routes.query.get_db')
    def test_collection_not_found(self, mock_db, client, auth_header):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        mock_cur.fetchall.return_value = MOCK_PAGE_CONFIGS

        body = {'collection': 'nonexistent', 'query': {}}

        resp = client.post(
            '/query/execute',
            data=json.dumps(body),
            content_type='application/json',
            headers=auth_header,
        )
        assert resp.status_code == 404

    @patch('routes.query.get_db')
    def test_label_remapping(self, mock_db, client, auth_header):
        """Labels in query, sort, and select should be remapped to fieldNames."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        from datetime import datetime
        mock_cur.fetchall.side_effect = [
            MOCK_PAGE_CONFIGS,
            [('id1', {'caseid': 'IC-001', 'caseName': 'Test'}, datetime.now(), None)],
        ]
        mock_cur.fetchone.return_value = (1,)

        body = {
            'collection': 'inspection-case',
            'query': {'用例ID': 'IC-001'},
            'select': ['用例ID', '用例名称'],
            'sort': {'用例ID': 1},
            'limit': 10,
        }

        resp = client.post(
            '/query/execute',
            data=json.dumps(body),
            content_type='application/json',
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        assert len(data['data']) == 1
