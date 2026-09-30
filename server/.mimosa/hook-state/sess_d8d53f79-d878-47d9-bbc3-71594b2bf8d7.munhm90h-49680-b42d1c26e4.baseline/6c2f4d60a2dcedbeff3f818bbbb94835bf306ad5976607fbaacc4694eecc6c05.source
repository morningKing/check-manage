import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/memories'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner, None, None)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def test_list_returns_owner_memories(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()), \
         patch('routes.open_api_memories.list_memories',
               return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as lm:
        resp = client.get(BASE, headers=HDR)
    assert resp.status_code == 200
    assert resp.get_json()['memories'][0]['memory'] == '喜欢 Python'
    lm.assert_called_once_with('user-42')


def test_add_rejects_empty_text(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'text': '  '})
    assert resp.status_code == 400


def test_add_rejects_overlong_text(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'text': 'x' * 2001})
    assert resp.status_code == 400


def test_add_when_memory_unavailable_is_409(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()), \
         patch('routes.open_api_memories.get_memory', return_value=None):
        resp = client.post(BASE, headers=HDR, json={'text': '喜欢简洁的代码'})
    assert resp.status_code == 409
    assert resp.get_json()['code'] == 'MEMORY_UNAVAILABLE'


def test_add_success(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()), \
         patch('routes.open_api_memories.get_memory', return_value=object()), \
         patch('routes.open_api_memories.add_memory_text', return_value=True) as amt, \
         patch('routes.open_api_memories.list_memories',
               return_value=[{'id': '1', 'memory': '喜欢简洁的代码'}]):
        resp = client.post(BASE, headers=HDR, json={'text': '喜欢简洁的代码', 'verbatim': True})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    # verbatim=True -> infer=False
    assert amt.call_args[0] == ('user-42', '喜欢简洁的代码')
    assert amt.call_args[1]['infer'] is False


def test_delete_checks_ownership(client, mock_conn, mock_cursor):
    """核心安全用例：目标 id 不在 list_memories(owner) 返回的集合里 -> 404，
    且 delete_memory 完全不能被调用——防止外部调用方靠猜 memory_id 删除
    别的用户的记忆（delete_memory 本身不按 user_id 过滤，见实现文件头注释）。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_memories._current_key', return_value=_key()), \
         patch('routes.open_api_memories.list_memories',
               return_value=[{'id': 'mine', 'memory': 'x'}]), \
         patch('routes.open_api_memories.delete_memory') as dm:
        ok = client.delete(f'{BASE}/mine', headers=HDR)
        nope = client.delete(f'{BASE}/someone-elses-id', headers=HDR)
    assert ok.status_code == 200
    dm.assert_called_once_with('mine')
    assert nope.status_code == 404
