"""
校验脚本路由单元测试

测试校验脚本 CRUD 和权限控制。
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
        patch('routes.validation_scripts.get_db', fake_db),
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


class TestListValidationScripts:
    def test_returns_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('vs-1', '校验1', '描述', 'return []', now, now),
        ]
        resp = client.get('/validationScripts', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

    def test_developer_can_list(self, setup):
        client, mock_cursor, _, dev_h = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/validationScripts', headers=dev_h)
        assert resp.status_code == 200


class TestCreateValidationScript:
    def test_create_success(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/validationScripts',
                           data=json.dumps({
                               'name': '新校验',
                               'description': '描述',
                               'script': 'return []',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.post('/validationScripts',
                           data=json.dumps({'name': 'x', 'script': 'x'}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403


class TestUpdateValidationScript:
    def test_update_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            'vs-1', '更新校验', '描述', 'return []', now, now,
        )
        resp = client.put('/validationScripts/vs-1',
                          data=json.dumps({'name': '更新校验'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.put('/validationScripts/nonexistent',
                          data=json.dumps({'name': 'x'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 404


class TestDeleteValidationScript:
    def test_delete_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('校验1',)
        resp = client.delete('/validationScripts/vs-1', headers=admin_h)
        assert resp.status_code == 200

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.delete('/validationScripts/vs-1', headers=dev_h)
        assert resp.status_code == 403
