"""
首页区块 API 测试

测试 /home-widgets 路由的 CRUD 和权限控制。
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
        patch('routes.home_widgets.get_db', fake_db),
        patch('db.pool', MagicMock()),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'user-dev', 'username': 'dev', 'role': 'developer'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
    )

    for p in patches:
        p.stop()


now = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestListHomeWidgets:
    def test_get_home_widgets(self, setup):
        """测试获取首页区块列表"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            {
                'id': 'welcome',
                'widget_type': 'welcome',
                'title': '欢迎',
                'content': {},
                'enabled': True,
                'order': 1,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            },
            {
                'id': 'stats',
                'widget_type': 'stats',
                'title': '统计',
                'content': {},
                'enabled': True,
                'order': 2,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            },
            {
                'id': 'quick-links',
                'widget_type': 'quick-links',
                'title': '快捷链接',
                'content': {},
                'enabled': True,
                'order': 3,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            },
            {
                'id': 'system-info',
                'widget_type': 'system-info',
                'title': '系统信息',
                'content': {},
                'enabled': True,
                'order': 4,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            },
        ]
        resp = client.get('/home-widgets', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 4

    def test_get_home_widgets_filters_by_role(self, setup):
        """测试角色过滤"""
        client, mock_cursor, _, dev_h = setup
        mock_cursor.fetchall.return_value = [
            {
                'id': 'welcome',
                'widget_type': 'welcome',
                'title': '欢迎',
                'content': {},
                'enabled': True,
                'order': 1,
                'visible_roles': ['admin'],  # only admin can see
                'created_at': now,
                'updated_at': now,
            },
            {
                'id': 'stats',
                'widget_type': 'stats',
                'title': '统计',
                'content': {},
                'enabled': True,
                'order': 2,
                'visible_roles': ['admin', 'developer'],
                'created_at': now,
                'updated_at': now,
            },
        ]
        resp = client.get('/home-widgets', headers=dev_h)
        assert resp.status_code == 200
        data = resp.get_json()
        # developer 只能看到 stats
        assert len(data) == 1
        assert data[0]['id'] == 'stats'

    def _disabled_and_enabled_rows(self):
        return [
            {
                'id': 'welcome', 'widget_type': 'welcome', 'title': '欢迎', 'content': {},
                'enabled': False, 'order': 1, 'visible_roles': ['admin'],
                'created_at': now, 'updated_at': now,
            },
            {
                'id': 'stats', 'widget_type': 'stats', 'title': '统计', 'content': {},
                'enabled': True, 'order': 2, 'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now, 'updated_at': now,
            },
        ]

    def test_get_all_widgets_admin_includes_disabled(self, setup):
        """?all=true（管理员）返回全部区块，含未启用 —— 取消勾选后仍可在配置页重新启用"""
        client, mock_cursor, admin_h, _ = setup
        import utils.permissions as _perms
        _perms.invalidate_cache()
        _perms._cache['admin'] = {
            'is_superuser': True, 'default_page_access': 'write',
            'admin_keys': set(), 'page_perms': {},
        }
        try:
            mock_cursor.fetchall.return_value = self._disabled_and_enabled_rows()
            resp = client.get('/home-widgets?all=true', headers=admin_h)
            assert resp.status_code == 200
            data = resp.get_json()
            ids = {w['id'] for w in data}
            assert ids == {'welcome', 'stats'}  # 含未启用的 welcome
            # 验证执行的是不带 enabled 过滤的 SQL
            sql = ' '.join(str(c.args[0]) for c in mock_cursor.execute.call_args_list if c.args)
            assert 'WHERE enabled = TRUE' not in sql
        finally:
            _perms.invalidate_cache()

    def test_get_all_widgets_non_admin_still_filtered(self, setup):
        """非管理员即便带 ?all=true 也只拿到启用且角色可见的区块（权限不被绕过）"""
        client, mock_cursor, _, dev_h = setup
        import utils.permissions as _perms
        _perms.invalidate_cache()
        _perms._cache['developer'] = {
            'is_superuser': False, 'default_page_access': 'read',
            'admin_keys': set(), 'page_perms': {},
        }
        try:
            mock_cursor.fetchall.return_value = self._disabled_and_enabled_rows()
            resp = client.get('/home-widgets?all=true', headers=dev_h)
            assert resp.status_code == 200
            data = resp.get_json()
            ids = {w['id'] for w in data}
            assert ids == {'stats'}  # welcome 未启用且仅 admin 可见 -> 被过滤
            sql = ' '.join(str(c.args[0]) for c in mock_cursor.execute.call_args_list if c.args)
            assert 'WHERE enabled = TRUE' in sql
        finally:
            _perms.invalidate_cache()


class TestCreateHomeWidget:
    def test_create_custom_widget(self, setup):
        """测试创建自定义区块"""
        client, mock_cursor, admin_h, _ = setup
        # 模拟获取最大 order
        mock_cursor.fetchone.return_value = {'max_order': 5}
        # 模拟 INSERT RETURNING
        mock_cursor.fetchall.return_value = []
        # 第二次 fetchone 用于返回新创建的记录
        mock_cursor.fetchone.side_effect = [
            {'max_order': 5},  # COALESCE 查询
            {
                'id': 'custom-custom-markdown-abc12345',
                'widget_type': 'custom-markdown',
                'title': '测试区块',
                'content': {'markdown': '# 测试'},
                'enabled': True,
                'order': 5,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            }
        ]
        resp = client.post('/home-widgets',
            data=json.dumps({'widgetType': 'custom-markdown', 'title': '测试区块', 'content': {'markdown': '# 测试'}}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['widgetType'] == 'custom-markdown'
        assert data['title'] == '测试区块'
        assert data['id'].startswith('custom-')

    def test_create_widget_non_admin(self, setup):
        """测试非管理员无法创建区块"""
        client, _, _, dev_h = setup
        resp = client.post('/home-widgets',
            data=json.dumps({'widgetType': 'custom-markdown', 'title': '测试'}),
            content_type='application/json',
            headers=dev_h
        )
        assert resp.status_code == 403

    def test_create_widget_invalid_type(self, setup):
        """测试创建不允许的类型返回错误"""
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/home-widgets',
            data=json.dumps({'widgetType': 'welcome', 'title': '测试'}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_create_data_card_widget(self, setup):
        """测试创建 data-card 类型区块"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.side_effect = [
            {'max_order': 3},
            {
                'id': 'custom-data-card-xyz12345',
                'widget_type': 'data-card',
                'title': '数据卡片',
                'content': {'cardType': 'project-count'},
                'enabled': True,
                'order': 4,
                'visible_roles': ['admin', 'developer', 'guest'],
                'created_at': now,
                'updated_at': now,
            }
        ]
        resp = client.post('/home-widgets',
            data=json.dumps({'widgetType': 'data-card', 'title': '数据卡片', 'content': {'cardType': 'project-count'}}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['widgetType'] == 'data-card'


class TestDeleteHomeWidget:
    def test_delete_custom_widget(self, setup):
        """测试删除自定义区块"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.rowcount = 1
        resp = client.delete('/home-widgets/custom-markdown-abc12345', headers=admin_h)
        assert resp.status_code == 200

    def test_delete_builtin_widget_forbidden(self, setup):
        """测试删除内置区块返回错误"""
        client, _, admin_h, _ = setup
        resp = client.delete('/home-widgets/welcome', headers=admin_h)
        assert resp.status_code == 400

    def test_delete_widget_not_found(self, setup):
        """测试删除不存在的区块返回404"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.rowcount = 0
        resp = client.delete('/home-widgets/custom-markdown-nonexistent', headers=admin_h)
        assert resp.status_code == 404

    def test_delete_widget_non_admin(self, setup):
        """测试非管理员无法删除区块"""
        client, _, _, dev_h = setup
        resp = client.delete('/home-widgets/custom-markdown-abc12345', headers=dev_h)
        assert resp.status_code == 403


class TestUpdateWidgetOrder:
    def test_update_widget_order(self, setup):
        """测试更新排序"""
        client, mock_cursor, admin_h, _ = setup
        resp = client.put('/home-widgets/order',
            data=json.dumps({'orders': [{'id': 'welcome', 'order': 10}, {'id': 'stats', 'order': 5}]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 200

    def test_update_widget_order_empty(self, setup):
        """测试空排序数组返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets/order',
            data=json.dumps({'orders': []}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_update_widget_order_non_admin(self, setup):
        """测试非管理员无法更新排序"""
        client, _, _, dev_h = setup
        resp = client.put('/home-widgets/order',
            data=json.dumps({'orders': [{'id': 'welcome', 'order': 10}]}),
            content_type='application/json',
            headers=dev_h
        )
        assert resp.status_code == 403


class TestBatchUpdateHomeWidgets:
    def test_batch_update_widgets(self, setup):
        """测试批量更新区块"""
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            {
                'id': 'welcome',
                'widget_type': 'welcome',
                'title': '更新后的标题',
                'content': {},
                'enabled': True,
                'order': 1,
                'visible_roles': ['admin', 'developer'],
                'created_at': now,
                'updated_at': now,
            }
        ]
        resp = client.put('/home-widgets',
            data=json.dumps({'widgets': [{'id': 'welcome', 'title': '更新后的标题'}]}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_batch_update_widgets_empty(self, setup):
        """测试空数组返回错误"""
        client, _, admin_h, _ = setup
        resp = client.put('/home-widgets',
            data=json.dumps({'widgets': []}),
            content_type='application/json',
            headers=admin_h
        )
        assert resp.status_code == 400

    def test_batch_update_widgets_non_admin(self, setup):
        """测试非管理员无法批量更新"""
        client, _, _, dev_h = setup
        resp = client.put('/home-widgets',
            data=json.dumps({'widgets': [{'id': 'welcome', 'title': '测试'}]}),
            content_type='application/json',
            headers=dev_h
        )
        assert resp.status_code == 403