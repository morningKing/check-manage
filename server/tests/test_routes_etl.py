"""
ETL 任务路由单元测试

使用 Flask test client + mock DB 测试 ETL 端点。
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
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
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
    token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    headers = {'Authorization': f'Bearer {token}'}

    yield app.test_client(), mock_cursor, mock_conn, headers

    for p in patches:
        p.stop()


class TestListTasks:
    def test_returns_list(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            ('etl-1', '任务1', '描述', [], True, now, 'success', now, now),
        ]
        resp = client.get('/etlTasks', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['id'] == 'etl-1'

    def test_unauthorized(self, setup):
        client, _, _, _ = setup
        resp = client.get('/etlTasks')
        assert resp.status_code == 401


class TestCreateTask:
    def test_creates_task(self, setup):
        client, _, _, headers = setup
        resp = client.post('/etlTasks',
                           data=json.dumps({'name': '新任务', 'description': '测试'}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['name'] == '新任务'
        assert data['id'].startswith('etl-')


class TestUpdateTask:
    def test_updates_task(self, setup):
        client, _, _, headers = setup
        resp = client.put('/etlTasks/etl-1',
                          data=json.dumps({'name': '更新名称'}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['name'] == '更新名称'


class TestDeleteTask:
    def test_deletes_task(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = ('任务名',)
        resp = client.delete('/etlTasks/etl-1', headers=headers)
        assert resp.status_code == 200


class TestRunTask:
    def test_task_not_found(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/etlTasks/etl-999/run',
                           data=json.dumps({'dryRun': True}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 404

    def test_dry_run(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (
            'etl-1', '任务', '描述',
            [{'id': 's1', 'name': 'input', 'type': 'json_input',
              'config': {'data': '[{"a":1}]'}, 'onError': 'stop'}],
            True, now, None, now, now,
        )

        exec_conn = MagicMock()
        exec_conn.cursor.return_value = mock_cursor
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = exec_conn

        with patch('db.pool', mock_pool):
            resp = client.post('/etlTasks/etl-1/run',
                               data=json.dumps({'dryRun': True}),
                               content_type='application/json',
                               headers=headers)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert len(data['stepResults']) == 1


class TestGetLogs:
    def test_returns_logs(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            ('log-1', 'etl-1', '任务', 'success', now, now, 10, 10, 0, [], None),
        ]
        resp = client.get('/etlTasks/etl-1/logs', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['taskId'] == 'etl-1'


class TestDevRoleAccess:
    def test_developer_cannot_access(self, setup):
        client, _, _, _ = setup
        dev_token = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
        headers = {'Authorization': f'Bearer {dev_token}'}
        resp = client.get('/etlTasks', headers=headers)
        assert resp.status_code == 403
