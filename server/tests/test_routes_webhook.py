"""
Webhook 规则路由单元测试

测试规则 CRUD、测试调用、日志查询等功能。
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
        patch('routes.webhooks.get_db', fake_db),
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


class TestListWebhookRules:
    def test_admin_can_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('whrule-1', '规则1', None, True, [], 'merge', {}, 'https://example.com', '', 30, 3, 0, now, now, 'admin', None),
        ]
        resp = client.get('/webhook/rules', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['name'] == '规则1'
        assert data[0]['sourceCollections'] == []

    def test_developer_can_list(self, setup):
        client, mock_cursor, _, dev_h = setup
        mock_cursor.fetchall.return_value = []
        resp = client.get('/webhook/rules', headers=dev_h)
        assert resp.status_code == 200


class TestCreateWebhookRule:
    def test_create_without_name_succeeds(self, setup):
        # API doesn't have strict validation, creates with empty name
        client, _, admin_h, _ = setup
        resp = client.post('/webhook/rules',
                           data=json.dumps({'webhookUrl': 'https://example.com', 'triggerEvent': 'create'}),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201

    def test_create_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/webhook/rules',
                           data=json.dumps({
                               'name': '新规则',
                               'triggerEvent': 'create',
                               'webhookUrl': 'https://example.com/webhook',
                               'sourceCollections': ['orders', 'products'],
                           }),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['name'] == '新规则'
        assert data['triggerEvent'] == 'create'
        assert data['sourceCollections'] == ['orders', 'products']


class TestGetWebhookRule:
    def test_get_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            'whrule-1', '规则1', '描述', True, ['orders'], 'create', {},
            'https://example.com', '', 30, 3, 0, now, now, 'admin', None
        )
        resp = client.get('/webhook/rules/whrule-1', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == 'whrule-1'
        assert data['name'] == '规则1'
        assert data['sourceCollections'] == ['orders']

    def test_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/webhook/rules/nonexistent', headers=admin_h)
        assert resp.status_code == 404


class TestUpdateWebhookRule:
    def test_update_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.put('/webhook/rules/whrule-1',
                          data=json.dumps({'name': '更新规则', 'enabled': False, 'sourceCollections': ['orders', 'tasks']}),
                          content_type='application/json',
                          headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['name'] == '更新规则'
        assert data['sourceCollections'] == ['orders', 'tasks']


class TestDeleteWebhookRule:
    def test_delete_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.delete('/webhook/rules/whrule-1', headers=admin_h)
        assert resp.status_code == 200


class TestTestWebhookRule:
    def test_rule_not_found(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/webhook/rules/nonexistent/test',
                           data=json.dumps({}),
                           content_type='application/json',
                           headers=admin_h)
        assert resp.status_code == 404

    def test_test_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            '规则1', 'https://example.com', '', 30, 3, ['orders']
        )
        # Mock the webhook call
        with patch('utils.webhook_engine._fire_single_webhook') as mock_fire:
            mock_fire.return_value = {
                'success': True,
                'logId': 'wh-test1',
                'responseStatus': 200,
                'errorMessage': None,
                'retryCount': 0,
            }
            resp = client.post('/webhook/rules/whrule-1/test',
                               data=json.dumps({}),
                               content_type='application/json',
                               headers=admin_h)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['success'] is True


class TestGetRuleLogs:
    def test_get_logs_success(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('log-1', 'whrule-1', '规则1', 'https://example.com', 'create',
             {'event': 'create'}, 200, 'OK', None, 150, 0, True, now),
        ]
        resp = client.get('/webhook/rules/whrule-1/logs', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['logs']) == 1


class TestLegacySettings:
    """测试旧版兼容接口"""

    def test_get_settings_no_rule(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/webhook/settings', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['enabled'] is False

    def test_get_settings_with_rule(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (
            'whrule-1', '合并通知', 'https://example.com', '', 30, 3, True
        )
        resp = client.get('/webhook/settings', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['enabled'] is True
        assert data['name'] == '合并通知'

    def test_get_logs(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = (0,)  # total count
        resp = client.get('/webhook/logs', headers=admin_h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['logs'] == []
        assert data['total'] == 0


class TestWebhookEngine:
    """测试 webhook_engine 模块"""

    def test_fire_webhooks_no_rules(self, setup):
        client, mock_cursor, _, _ = setup
        mock_cursor.fetchall.return_value = []

        from utils.webhook_engine import fire_webhooks
        errors = fire_webhooks('create', 'orders', 'order-1', None, {'name': '订单1'}, 'admin')
        assert errors == []

    def test_fire_webhooks_with_rules(self, setup):
        client, mock_cursor, _, _ = setup
        # source_collections is a JSONB array
        mock_cursor.fetchall.return_value = [
            ('whrule-1', '规则1', 'create', {}, 'https://example.com', '', 30, 3, ['orders']),
        ]

        with patch('utils.webhook_engine._fire_single_webhook') as mock_fire:
            mock_fire.return_value = {'success': True}
            from utils.webhook_engine import fire_webhooks
            errors = fire_webhooks('create', 'orders', 'order-1', None, {'name': '订单1'}, 'admin')
            assert errors == []
            mock_fire.assert_called_once()

    def test_fire_webhooks_with_failure(self, setup):
        client, mock_cursor, _, _ = setup
        mock_cursor.fetchall.return_value = [
            ('whrule-1', '规则1', 'create', {}, 'https://example.com', '', 30, 3, ['orders']),
        ]

        with patch('utils.webhook_engine._fire_single_webhook') as mock_fire:
            mock_fire.return_value = {'success': False, 'errorMessage': 'Connection failed'}
            from utils.webhook_engine import fire_webhooks
            errors = fire_webhooks('create', 'orders', 'order-1', None, {'name': '订单1'}, 'admin')
            assert len(errors) == 1
            assert errors[0]['rule_id'] == 'whrule-1'

    def test_fire_webhooks_global_rule(self, setup):
        """测试全局规则（空数组）匹配所有 collection"""
        client, mock_cursor, _, _ = setup
        mock_cursor.fetchall.return_value = [
            ('whrule-1', '全局规则', 'merge', {}, 'https://example.com', '', 30, 3, []),
        ]

        with patch('utils.webhook_engine._fire_single_webhook') as mock_fire:
            mock_fire.return_value = {'success': True}
            from utils.webhook_engine import fire_webhooks
            # merge event with None collection should match global rule
            errors = fire_webhooks('merge', None, 'merge-1', None, {'summary': {}}, 'admin')
            assert errors == []

    def test_check_condition_field_change(self, setup):
        from utils.webhook_engine import _check_condition

        # Update event with field change
        condition = {'field': 'status', 'value': 'completed'}
        old_data = {'status': 'pending'}
        new_data = {'status': 'completed'}

        result = _check_condition(condition, old_data, new_data, 'update')
        assert result is True

        # No change
        result = _check_condition(condition, {'status': 'completed'}, {'status': 'completed'}, 'update')
        assert result is False

    def test_build_merge_payload(self, setup):
        from utils.webhook_engine import build_merge_webhook_payload

        merge_result = {
            'mergeId': 'merge-1',
            'collections': [
                {'collection': 'orders', 'pageName': '订单', 'recordsCreated': 5, 'recordsUpdated': 3, 'recordsDeleted': 0}
            ]
        }

        payload = build_merge_webhook_payload(
            'merge-1', merge_result, 'menu-1', '项目A', 'v-1', 'v1.0', 'main', 'main', 'admin'
        )

        assert payload['event'] == 'merge'
        assert payload['mergeId'] == 'merge-1'
        assert payload['project']['name'] == '项目A'
        assert payload['summary']['recordsCreated'] == 5