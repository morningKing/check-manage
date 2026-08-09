import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ACTION = {
    'id': 'ra-1', 'label': '推送外部', 'actionType': 'webhook',
    'enabled': True, 'webhookRuleId': 'wh-1',
    'statusField': 'syncStatus', 'runningValue': '同步中',
    'doneValue': '已同步', 'failedValue': '同步失败',
}

URL = '/orders/rec-1/row-actions/ra-1/run'


def _patch_lookup(row_actions=(ACTION,), row_data=None):
    """patch 掉路由内部的配置/行数据读取。"""
    return patch('routes.dynamic._load_row_action_context',
                 return_value=(list(row_actions),
                               {'status': '待审核'} if row_data is None else row_data))


def test_requires_auth(client):
    resp = client.post(URL, json={})
    assert resp.status_code == 401


def test_guest_is_rejected(client, dev_token):
    from auth import create_token
    guest = create_token({'id': 'u-g', 'username': 'g', 'role': 'guest'})
    resp = client.post(URL, headers={'Authorization': f'Bearer {guest}'}, json={})
    assert resp.status_code == 403


def test_unknown_action_404(client, admin_headers):
    with _patch_lookup(row_actions=()):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 404


def test_record_not_found_404(client, admin_headers):
    with patch('routes.dynamic._load_row_action_context', return_value=([ACTION], None)):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 404


def test_happy_path_returns_running(client, admin_headers):
    with _patch_lookup(), \
         patch('routes.dynamic.run_action', return_value='running') as ra, \
         patch('routes.dynamic.log_operation'):
        resp = client.post(URL, headers=admin_headers, json={'params': {'reason': 'x'}})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'status': 'running'}
    assert ra.call_args[0][0] == 'orders'
    assert ra.call_args[0][1] == 'rec-1'
    assert ra.call_args[1]['params'] == {'reason': 'x'}


def test_engine_error_is_translated_to_its_status(client, admin_headers):
    from utils.row_action_engine import RowActionError
    with _patch_lookup(), \
         patch('routes.dynamic.run_action',
               side_effect=RowActionError('该行有正在执行的动作，请稍后再试', 409)):
        resp = client.post(URL, headers=admin_headers, json={})
    assert resp.status_code == 409
    assert '正在执行' in resp.get_json()['error']


def test_operation_log_written_on_success(client, admin_headers):
    with _patch_lookup(), \
         patch('routes.dynamic.run_action', return_value='running'), \
         patch('routes.dynamic.log_operation') as lo:
        client.post(URL, headers=admin_headers, json={})
    assert lo.call_args[0][1] == 'row_action'
