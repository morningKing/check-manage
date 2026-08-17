import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.row_action_engine import RowActionError

BASE = '/v1/collections'
HDR = {'X-API-Key': 'cm_x'}
URL = f'{BASE}/orders/rec-1/row-actions/act-1/run'


@contextmanager
def _fake_auth_db(mock_conn):
    """同 test_open_api_batches_crud.py 的模式：auth.py 模块顶层绑定的 get_db
    需要单独 patch 才能让真实的 api_key_required 网关在单独跑本文件时可控。"""
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42', name='集成密钥'):
    return {'id': 'ak-1', 'name': name, 'ownerUserId': owner}


ACTION = {'id': 'act-1', 'label': '标记完成', 'actionType': 'webhook'}


_UNSET = object()


def _patched(mock_conn, key=None, writable_error=None, branch=('main', None),
            row_actions=None, row_data=_UNSET, role='developer'):
    """统一构造本文件所有测试共用的一组 patch。row_actions 默认含 ACTION。

    row_data 的"未传"用 _UNSET 哨兵区分于"显式传 None"（record not found 的
    测试场景要传 row_data=None），不能像 row_actions 那样用 None 复用默认值——
    两者语义不同：row_data=None 是一个合法的、有意义的测试输入。
    """
    key = key or _key()
    row_actions = [ACTION] if row_actions is None else row_actions
    row_data = {'status': 'pending'} if row_data is _UNSET else row_data
    return [
        patch('auth.get_db', lambda: _fake_auth_db(mock_conn)),
        patch('routes.open_api_row_actions._current_key', return_value=key),
        patch('routes.open_api_row_actions.check_collection_writable',
              return_value=writable_error),
        patch('routes.open_api_row_actions.get_request_branch_id',
              return_value=branch),
        patch('routes.open_api_row_actions._load_row_action_context',
              return_value=(row_actions, row_data)),
        patch('routes.open_api_row_actions._owner_role', return_value=role),
    ]


def _apply(patches):
    from contextlib import ExitStack
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def test_readonly_collection_is_403(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn, writable_error=({'error': 'Collection is read-only'}, 403))):
        resp = client.post(URL, headers=HDR)
    assert resp.status_code == 403


def test_record_not_found_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn, row_data=None)):
        resp = client.post(URL, headers=HDR)
    assert resp.status_code == 404
    assert '记录不存在' in resp.get_json()['error']


def test_unknown_action_id_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn, row_actions=[])):
        resp = client.post(URL, headers=HDR)
    assert resp.status_code == 404
    assert '行操作不存在' in resp.get_json()['error']


def test_run_action_error_is_translated_to_status_code(client, mock_conn, mock_cursor):
    """RowActionError 原样透传（含中文消息）——这是本端点和 open-api.md 的
    "全英文 error" 约定唯一的差异点。"""
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn)), \
         patch('routes.open_api_row_actions.run_action',
               side_effect=RowActionError('角色无权限', 403)):
        resp = client.post(URL, headers=HDR)
    assert resp.status_code == 403
    assert resp.get_json()['error'] == '角色无权限'


def test_success_passes_owner_role_and_params(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn, role='developer')), \
         patch('routes.open_api_row_actions.run_action', return_value='submitted') as ra, \
         patch('routes.open_api_row_actions.resolve_status_gate',
               return_value=(None, None, None, False)):
        resp = client.post(URL, headers=HDR, json={'params': {'note': 'hi'}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'ok': True, 'status': 'submitted', 'statusField': None, 'runningValue': None}
    assert ra.call_args[1]['role'] == 'developer'
    assert ra.call_args[1]['params'] == {'note': 'hi'}
    assert ra.call_args[1]['operator'] == 'api-key:集成密钥'


def test_success_running_includes_status_gate(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with _apply(_patched(mock_conn)), \
         patch('routes.open_api_row_actions.run_action', return_value='running'), \
         patch('routes.open_api_row_actions.resolve_status_gate',
               return_value=('status', '处理中', {'id': 'scan-1'}, True)):
        resp = client.post(URL, headers=HDR)
    body = resp.get_json()
    assert body['status'] == 'running'
    assert body['statusField'] == 'status'
    assert body['runningValue'] == '处理中'
