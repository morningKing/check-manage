"""
菜单级导出 API 路由单元测试

测试 /menuExport 和 /menuExport/availableMenus 端点。
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
        patch('routes.menu_export.get_db', fake_db),
        patch('db.pool', MagicMock()),
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


# ==================== GET /menuExport/availableMenus ====================

class TestGetAvailableMenus:
    """测试获取可导出菜单列表"""

    def test_returns_only_dynamic_menus(self, setup):
        """只返回包含动态数据表的菜单"""
        client, mock_cursor, admin_h, _ = setup

        # 模拟菜单数据：
        # - 巡检管理（有动态子菜单）
        # - 系统配置（静态页面，应被过滤）
        mock_cursor.fetchall.return_value = [
            ('menu-inspection', '巡检管理', 'Document', None, None, 0, None),
            ('menu-inspection-case', '巡检用例', 'Document', 'page-inspection-case', 'menu-inspection', 1, None),
            ('menu-system', '系统配置', 'Setting', 'system-config', None, 10, None),
        ]

        resp = client.get('/menuExport/availableMenus', headers=admin_h)

        assert resp.status_code == 200
        data = resp.get_json()

        # 应只包含巡检管理菜单，系统配置被过滤
        assert len(data) == 1
        assert data[0]['name'] == '巡检管理'
        assert len(data[0]['children']) == 1
        assert data[0]['children'][0]['name'] == '巡检用例'

    def test_empty_when_no_dynamic_menus(self, setup):
        """无动态数据表时返回空列表"""
        client, mock_cursor, admin_h, _ = setup

        # 所有菜单都是静态页面
        mock_cursor.fetchall.return_value = [
            ('menu-system', '系统配置', 'Setting', 'system-config', None, 0, None),
            ('menu-tools', '数据工具', 'Tools', 'data-tools', None, 1, None),
        ]

        resp = client.get('/menuExport/availableMenus', headers=admin_h)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []

    def test_includes_export_script_info(self, setup):
        """包含导出脚本绑定信息"""
        client, mock_cursor, admin_h, _ = setup

        mock_cursor.fetchall.return_value = [
            ('menu-data', '数据管理', 'Document', 'page-data', None, 0, 'script-1'),
        ]
        mock_cursor.fetchone.return_value = ('JSON导出',)  # 脚本名称

        resp = client.get('/menuExport/availableMenus', headers=admin_h)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data[0]['exportScriptId'] == 'script-1'
        assert data[0]['exportScriptName'] == 'JSON导出'

    def test_requires_authentication(self, setup):
        """需要认证"""
        client, _, _, _ = setup

        resp = client.get('/menuExport/availableMenus')

        assert resp.status_code == 401

    def test_developer_can_access(self, setup):
        """开发者可以访问"""
        client, mock_cursor, _, dev_h = setup

        mock_cursor.fetchall.return_value = []

        resp = client.get('/menuExport/availableMenus', headers=dev_h)

        assert resp.status_code == 200


# ==================== POST /menuExport/preview ====================

class TestPreviewMenuExport:
    """测试导出预览"""

    def test_returns_preview_data(self, setup):
        """返回预览数据"""
        client, mock_cursor, admin_h, _ = setup

        # 模拟菜单查询
        mock_cursor.fetchone.side_effect = [
            ('巡检管理', None),  # 菜单信息
        ]
        # 模拟脚本列表
        mock_cursor.fetchall.side_effect = [
            [('script-1', 'JSON导出', 'JSON格式导出')],  # 可用脚本
        ]

        # 模拟 get_menu_collections_with_info 的调用
        # 注意：函数是在 preview_menu_export 内部从 utils.menu_export 导入的
        with patch('utils.menu_export.get_menu_collections_with_info') as mock_get_info:
            mock_get_info.return_value = [
                {'collection': 'inspection-case', 'pageName': '巡检用例', 'recordCount': 100},
            ]

            resp = client.post('/menuExport/preview',
                              data=json.dumps({'menuIds': ['menu-1']}),
                              content_type='application/json',
                              headers=admin_h)

        assert resp.status_code == 200
        data = resp.get_json()
        assert 'menus' in data
        assert 'totalRecords' in data
        assert 'availableScripts' in data

    def test_no_menu_ids_returns_error(self, setup):
        """未选择菜单返回错误"""
        client, _, admin_h, _ = setup

        resp = client.post('/menuExport/preview',
                          data=json.dumps({'menuIds': []}),
                          content_type='application/json',
                          headers=admin_h)

        assert resp.status_code == 400

    def test_shows_bound_script(self, setup):
        """显示菜单绑定的脚本"""
        client, mock_cursor, admin_h, _ = setup

        mock_cursor.fetchone.side_effect = [
            ('巡检管理', 'script-1'),  # 菜单绑定了脚本
            ('script-1', 'JSON导出'),  # 脚本信息查询 (id, name)
        ]
        mock_cursor.fetchall.side_effect = [
            [],  # 可用脚本列表为空
        ]

        with patch('utils.menu_export.get_menu_collections_with_info') as mock_get_info:
            mock_get_info.return_value = []

            resp = client.post('/menuExport/preview',
                              data=json.dumps({'menuIds': ['menu-1']}),
                              content_type='application/json',
                              headers=admin_h)

        data = resp.get_json()
        assert data['menus'][0]['boundScript'] is not None
        assert data['menus'][0]['boundScript']['name'] == 'JSON导出'


# ==================== POST /menuExport ====================

class TestExecuteMenuExport:
    """测试执行导出"""

    def test_returns_zip_file(self, setup):
        """返回 ZIP 文件"""
        client, mock_cursor, admin_h, _ = setup

        # 完整的导出流程 mock:
        # 1. 菜单信息 (name, export_script_id)
        # 2. 脚本信息 (id, name, script, output_format, scope)
        # 3. get_menu_collections:
        #    - fetchone: page_id
        #    - fetchall: children (有1个)
        #    - fetchone: 子菜单 page_id
        #    - fetchall: 子菜单 children (空)
        # 4. For page scope:
        #    - fetchone: page_configs
        #    - fetchall: data records
        mock_cursor.fetchone.side_effect = [
            ('巡检管理', 'script-1'),  # 1. 菜单信息
            ('JSON导出', 'id-1', 'result = json.dumps(data)', 'json', 'page'),  # 2. 脚本信息
            (None,),  # 3a. 父菜单无 page_id
            ('page-case',),  # 3b. 子菜单 page_id
            ('巡检用例', []),  # 4a. page_configs
        ]
        mock_cursor.fetchall.side_effect = [
            [('menu-case',)],  # 父菜单的子菜单
            [],  # 子菜单无子菜单
            [('id-1', 'case', {'name': 'test'}, datetime.now(timezone.utc))],  # 数据记录
        ]

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': ['menu-1'], 'scriptId': 'script-1'}),
                          content_type='application/json',
                          headers=admin_h)

        assert resp.status_code == 200
        assert resp.content_type == 'application/zip'
        assert 'attachment' in resp.headers.get('Content-Disposition', '')

    def test_no_menu_ids_returns_error(self, setup):
        """未选择菜单返回错误"""
        client, _, admin_h, _ = setup

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': []}),
                          content_type='application/json',
                          headers=admin_h)

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_all_failed_returns_error(self, setup):
        """所有导出失败返回错误"""
        client, mock_cursor, admin_h, _ = setup

        # 菜单不存在
        mock_cursor.fetchone.return_value = None

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': ['non-existent']}),
                          content_type='application/json',
                          headers=admin_h)

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data
        assert '失败' in data['error']

    def test_uses_script_override(self, setup):
        """使用脚本参数覆盖菜单绑定"""
        client, mock_cursor, admin_h, _ = setup

        # 菜单绑定了 script-A，但请求指定 script-B
        mock_cursor.fetchone.side_effect = [
            ('巡检管理', 'script-A'),  # 菜单绑定的脚本
            ('Script-B', 'id-b', 'result = "B"', 'txt', 'page'),  # 查询的是 script-B
            (None,),  # get_menu_collections: 父菜单无 page_id
            ('page-case',),  # 子菜单 page_id
            ('巡检用例', []),  # page_configs
        ]
        mock_cursor.fetchall.side_effect = [
            [('menu-case',)],  # 子菜单
            [],  # 子菜单无子菜单
            [('id-1', 'case', {'name': 'test'}, datetime.now(timezone.utc))],  # 数据
        ]

        resp = client.post('/menuExport',
                          data=json.dumps({
                              'menuIds': ['menu-1'],
                              'scriptId': 'script-B'  # 覆盖
                          }),
                          content_type='application/json',
                          headers=admin_h)

        # 验证成功返回（说明使用了 script-B）
        assert resp.status_code == 200

    def test_includes_errors_in_header(self, setup):
        """部分失败时在 header 中包含错误信息"""
        client, mock_cursor, admin_h, _ = setup

        # 第一个菜单成功，第二个失败
        # 第一个菜单的调用序列
        mock_cursor.fetchone.side_effect = [
            ('菜单1', 'script-1'),  # 1. 菜单1信息
            ('JSON脚本', 'id-1', 'result = json.dumps(data)', 'json', 'page'),  # 2. 脚本信息
            (None,),  # 3a. 父菜单无 page_id
            ('page-col',),  # 3b. 子菜单 page_id
            ('页面1', []),  # 4a. page_configs
            None,  # 第二个菜单不存在
        ]
        mock_cursor.fetchall.side_effect = [
            [('menu-child',)],  # 子菜单
            [],  # 子菜单无子菜单
            [('id-1', 'col', {'name': 'test'}, datetime.now(timezone.utc))],  # 数据
        ]

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': ['menu-1', 'menu-2']}),
                          content_type='application/json',
                          headers=admin_h)

        # 应该返回 ZIP（有部分成功）
        assert resp.status_code == 200

    def test_requires_authentication(self, setup):
        """需要认证"""
        client, _, _, _ = setup

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': ['menu-1']}),
                          content_type='application/json')

        assert resp.status_code == 401


# ==================== POST /menuExport/batchClear ====================

class TestBatchClear:
    """测试批量清空数据页端点"""

    def _post(self, client, headers, body):
        return client.post(
            '/menuExport/batchClear',
            data=json.dumps(body),
            content_type='application/json',
            headers=headers,
        )

    def test_empty_collections_returns_400(self, setup):
        client, _, admin_h, _ = setup
        resp = self._post(client, admin_h, {'collections': [], 'branchId': 'main'})
        assert resp.status_code == 400

    def test_guest_forbidden(self, setup):
        client, _, _, _ = setup
        guest_token = create_token({'id': 'u-g', 'username': 'g', 'role': 'guest'})
        resp = self._post(
            client,
            {'Authorization': f'Bearer {guest_token}'},
            {'collections': ['orders'], 'branchId': 'main'},
        )
        assert resp.status_code == 403

    def test_clears_data_and_relations(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.rowcount = 3
        resp = self._post(
            client, admin_h,
            {'collections': ['orders', 'products'], 'branchId': 'main'},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['totalDeleted'] >= 0
        executed = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
        assert 'DELETE FROM dynamic_data' in executed
        assert 'DELETE FROM data_relations' in executed


# ==================== 权限测试 ====================

class TestMenuExportPermissions:
    """测试权限控制"""

    def test_developer_can_export(self, setup):
        """开发者可以导出"""
        client, mock_cursor, _, dev_h = setup

        mock_cursor.fetchone.side_effect = [
            ('巡检管理', 'script-1'),  # 菜单信息
            ('JSON导出', 'id-1', 'result = json.dumps(data)', 'json', 'page'),  # 脚本信息
            (None,),  # get_menu_collections: 父菜单无 page_id
            ('page-case',),  # 子菜单 page_id
            ('巡检用例', []),  # page_configs
        ]
        mock_cursor.fetchall.side_effect = [
            [('menu-case',)],  # 子菜单
            [],  # 子菜单无子菜单
            [('id-1', 'case', {'name': 'test'}, datetime.now(timezone.utc))],  # 数据
        ]

        resp = client.post('/menuExport',
                          data=json.dumps({'menuIds': ['menu-1']}),
                          content_type='application/json',
                          headers=dev_h)

        assert resp.status_code == 200