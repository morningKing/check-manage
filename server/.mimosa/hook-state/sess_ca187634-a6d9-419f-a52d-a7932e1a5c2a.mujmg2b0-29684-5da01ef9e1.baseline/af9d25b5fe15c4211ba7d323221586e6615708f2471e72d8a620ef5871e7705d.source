"""GET /ai/chat/sessions/:sid/subtasks/:subtaskId/messages 的测试。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/ai/chat/sessions'


def test_subtask_messages_requires_login(client):
    resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages')
    assert resp.status_code == 401


def test_subtask_messages_not_found_is_404(client, dev_headers):
    with patch('routes.ai_chat.get_subtask_messages', return_value=None):
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    assert resp.status_code == 404


def test_subtask_messages_passes_owner_scope(client, dev_headers):
    """必须带当前登录用户的 id 做归属校验，不能不传（那样就是不做校验的
    管理员路径了）。"""
    payload = {'subtask': {'id': 'ses_x', 'agent': 'build', 'description': 'd',
                           'status': 'running'},
              'messages': [], 'truncated': False, 'total': 0}
    with patch('routes.ai_chat.get_subtask_messages', return_value=payload) as gm:
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    assert resp.status_code == 200
    assert gm.call_args[0][0] == 'ses_x'
    assert gm.call_args[1]['owner_user_id'] == 'user-dev'


def test_subtask_messages_returns_contract_fields(client, dev_headers):
    payload = {'subtask': {'id': 'ses_x', 'agent': 'build', 'description': 'd',
                           'status': 'failed', 'error_message': 'boom'},
              'messages': [{'id': 'm1', 'role': 'assistant', 'content': [],
                           'created_at': None, 'meta': None}],
              'truncated': True, 'total': 900}
    with patch('routes.ai_chat.get_subtask_messages', return_value=payload):
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    body = resp.get_json()
    assert body['truncated'] is True and body['total'] == 900
    assert body['subtask']['status'] == 'failed'
    assert body['subtask']['error'] == 'boom'
    assert len(body['messages']) == 1
