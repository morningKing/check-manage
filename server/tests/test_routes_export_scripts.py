"""
导出脚本路由单元测试

测试导出脚本 CRUD 和权限控制。
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
        patch('routes.export_scripts.get_db', fake_db),
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


class TestListExportScripts:
    def test_returns_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('s1', '脚本1', '描述', 'python', 'print(1)', 'json', now, now, 'page', None, None),
        ]
        resp = client.get('/exportScripts', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

    def test_developer_can_list(self, setup):
        client, mock_cursor, _, dev_h = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/exportScripts', headers=dev_h)
        assert resp.status_code == 200


class TestCreateExportScript:
    def test_create_success(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/exportScripts',
                           data=json.dumps({
                               'name': '新脚本',
                               'script': 'print(1)',
                               'language': 'python',
                               'outputFormat': 'json',
                               'boundCollection': 'inspection-case',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.post('/exportScripts',
                           data=json.dumps({'name': 'x', 'script': 'x'}),
                           content_type='application/json',
                           headers=dev_h)
        assert resp.status_code == 403

    def test_menu_scope_with_page_code_rejected(self, setup):
        # scope=menu 却用 fields（页面级写法）→ 保存时拒绝，避免运行期 NameError
        client, _, admin_h, _ = setup
        resp = client.post('/exportScripts',
                           data=json.dumps({
                               'name': '坏菜单脚本',
                               'script': "headers = [f['label'] for f in fields]\nresult = ''",
                               'outputFormat': 'csv',
                               'scope': 'menu',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 400
        assert 'menu_data' in resp.get_json()['error']

    def test_menu_scope_with_menu_data_ok(self, setup):
        client, _, admin_h, _ = setup
        resp = client.post('/exportScripts',
                           data=json.dumps({
                               'name': '好菜单脚本',
                               'script': "result = [{'filename': t['pageName'] + '.csv', 'content': ''} for t in menu_data]",
                               'outputFormat': 'csv',
                               'scope': 'menu',
                               'boundMenuId': 'menu-test',
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201


class TestUpdateExportScript:
    def test_update_menu_scope_with_page_code_rejected(self, setup):
        client, _, admin_h, _ = setup
        resp = client.put('/exportScripts/s1',
                          data=json.dumps({
                              'scope': 'menu',
                              'script': "headers = [f['label'] for f in fields]\nresult = ''",
                          }),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 400
        assert 'menu_data' in resp.get_json()['error']

    def test_update_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            's1', '更新脚本', '描述', 'python', 'print(2)', 'json', now, now, 'page', None, None,
        )
        resp = client.put('/exportScripts/s1',
                          data=json.dumps({'name': '更新脚本'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.put('/exportScripts/nonexistent',
                          data=json.dumps({'name': 'x'}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 404


class TestExportScriptBinding:
    def test_create_page_script_requires_binding(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result="1"', 'scope': 'page'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 400
        assert '绑定' in resp.get_json()['error']

    def test_create_page_script_with_binding_ok(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result="1"', 'scope': 'page',
                             'boundCollection': 'inspection-case'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 201
        assert resp.get_json()['boundCollection'] == 'inspection-case'

    def test_create_menu_script_with_collection_binding_rejected(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result=""\nfor t in menu_data:\n    pass',
                             'scope': 'menu', 'boundCollection': 'inspection-case'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 400


class TestDeleteExportScript:
    def test_delete_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('脚本1',)
        mock_cursor.fetchall.return_value = []
        resp = client.delete('/exportScripts/s1', headers=admin_h)
        assert resp.status_code == 200

    def test_developer_forbidden(self, setup):
        client, _, _, dev_h = setup
        resp = client.delete('/exportScripts/s1', headers=dev_h)
        assert resp.status_code == 403
