import datetime
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/ai-sessions'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _row(**overrides):
    r = {
        'id': 'sess-1', 'title': '新会话', 'status': 'pending',
        'agent': None, 'model': None, 'createdAt': None, 'lastActiveAt': None,
        'output': None, 'error': None,
    }
    r.update(overrides)
    return r


def test_create_requires_prompt(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={})
    assert resp.status_code == 400


def test_create_rejects_overlong_prompt(client, mock_conn, mock_cursor):
    from routes.open_api_ai_sessions import MAX_PROMPT_CHARS
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'prompt': 'x' * (MAX_PROMPT_CHARS + 1)})
    assert resp.status_code == 400


def test_create_success_notifies_worker_and_returns_pending(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    created = {'id': 'sess-1', 'status': 'pending'}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.create_session', return_value=created) as cs, \
         patch('routes.open_api_ai_sessions.get_worker') as gw:
        resp = client.post(BASE, headers=HDR, json={
            'prompt': '帮我写一句问候语', 'agent': 'build', 'model': 'm1', 'title': '打招呼',
        })
    assert resp.status_code == 201
    assert resp.get_json() == {'sessionId': 'sess-1', 'status': 'pending'}
    assert cs.call_args[0][0] == 'user-42'
    assert cs.call_args[1] == {
        'prompt': '帮我写一句问候语', 'agent': 'build', 'model': 'm1',
        'title': '打招呼', 'api_key_id': 'ak-1',
    }
    gw.return_value.notify.assert_called_once()


def test_create_omits_optional_fields_when_unset(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.create_session',
               return_value={'id': 'sess-1', 'status': 'pending'}) as cs, \
         patch('routes.open_api_ai_sessions.get_worker'):
        client.post(BASE, headers=HDR, json={'prompt': 'hi'})
    assert cs.call_args[1]['agent'] is None
    assert cs.call_args[1]['model'] is None
    assert cs.call_args[1]['title'] is None


def test_detail_not_found_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.get_session_for_owner', return_value=None):
        resp = client.get(f'{BASE}/missing', headers=HDR)
    assert resp.status_code == 404


def test_detail_pending_has_no_output_or_error(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    row = _row(status='pending')
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.get_session_for_owner', return_value=row):
        resp = client.get(f'{BASE}/sess-1', headers=HDR)
    body = resp.get_json()
    assert body['status'] == 'pending'
    assert body['output'] is None
    assert body['error'] is None


def test_detail_completed_returns_output(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    row = _row(status='completed', output='你好！很高兴见到你。',
              createdAt=datetime.datetime(2026, 1, 1), lastActiveAt=datetime.datetime(2026, 1, 1))
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.get_session_for_owner', return_value=row):
        resp = client.get(f'{BASE}/sess-1', headers=HDR)
    body = resp.get_json()
    assert body['status'] == 'completed'
    assert body['output'] == '你好！很高兴见到你。'
    assert body['createdAt'] == '2026-01-01T00:00:00'


def test_detail_failed_returns_error(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    row = _row(status='failed', error='Agent「nope」不存在（OpenCode 未找到）。')
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key()), \
         patch('routes.open_api_ai_sessions.get_session_for_owner', return_value=row):
        resp = client.get(f'{BASE}/sess-1', headers=HDR)
    body = resp.get_json()
    assert body['status'] == 'failed'
    assert 'Agent' in body['error']
    assert body['output'] is None


def test_detail_scoped_by_owner_and_api_key(client, mock_conn, mock_cursor):
    """get_session_for_owner 必须拿到密钥属主的 user_id 和密钥自己的 id 两个
    维度做归属校验——跟批任务的 get_batch_detail(user_id, ..., api_key_id=) 同
    一个双重收紧模式。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_ai_sessions._current_key', return_value=_key(owner='user-99')), \
         patch('routes.open_api_ai_sessions.get_session_for_owner', return_value=None) as gs:
        client.get(f'{BASE}/sess-1', headers=HDR)
    gs.assert_called_once_with('sess-1', 'user-99', 'ak-1')
