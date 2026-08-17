import os
import sys
import datetime
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.prompt_template import DuplicateTemplateName

BASE = '/v1/prompt-templates'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _row(**overrides):
    r = {'id': 'tpl-1', 'user_id': 'user-42', 'name': '常用模板', 'content': '请总结...',
         'created_at': datetime.datetime(2026, 1, 1), 'updated_at': datetime.datetime(2026, 1, 1)}
    r.update(overrides)
    return r


def test_create_requires_name_and_content(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'name': 'x'})
    assert resp.status_code == 400


def test_create_success_does_not_leak_user_id(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()), \
         patch('routes.open_api_prompt_templates.create_template', return_value=_row()) as ct:
        resp = client.post(BASE, headers=HDR, json={'name': '常用模板', 'content': '请总结...'})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body == {'id': 'tpl-1', 'name': '常用模板', 'content': '请总结...',
                    'createdAt': '2026-01-01T00:00:00', 'updatedAt': '2026-01-01T00:00:00'}
    assert ct.call_args[0][0] == 'user-42'


def test_create_duplicate_name_is_409(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()), \
         patch('routes.open_api_prompt_templates.create_template',
               side_effect=DuplicateTemplateName('x')):
        resp = client.post(BASE, headers=HDR, json={'name': 'x', 'content': 'y'})
    assert resp.status_code == 409


def test_list_scoped_to_owner(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()), \
         patch('routes.open_api_prompt_templates.list_templates', return_value=[_row()]) as lt:
        resp = client.get(BASE, headers=HDR)
    assert resp.status_code == 200
    assert len(resp.get_json()['templates']) == 1
    lt.assert_called_once_with('user-42')


def test_get_other_users_template_is_404(client, mock_conn, mock_cursor):
    """跨用户 404：get_template(owner, id) 本身按 owner 过滤，owner 对不上返回 None。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key(owner='user-99')), \
         patch('routes.open_api_prompt_templates.get_template', return_value=None) as gt:
        resp = client.get(f'{BASE}/tpl-1', headers=HDR)
    assert resp.status_code == 404
    gt.assert_called_once_with('user-99', 'tpl-1')


def test_update_success(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    updated = _row(name='新名字', content='新内容')
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()), \
         patch('routes.open_api_prompt_templates.update_template', return_value=updated):
        resp = client.put(f'{BASE}/tpl-1', headers=HDR, json={'name': '新名字', 'content': '新内容'})
    assert resp.status_code == 200
    assert resp.get_json()['name'] == '新名字'


def test_update_other_users_template_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key(owner='user-99')), \
         patch('routes.open_api_prompt_templates.update_template', return_value=None):
        resp = client.put(f'{BASE}/tpl-1', headers=HDR, json={'name': 'x', 'content': 'y'})
    assert resp.status_code == 404


def test_delete_success_is_204(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key()), \
         patch('routes.open_api_prompt_templates.delete_template', return_value=True):
        resp = client.delete(f'{BASE}/tpl-1', headers=HDR)
    assert resp.status_code == 204


def test_delete_other_users_template_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_prompt_templates._current_key', return_value=_key(owner='user-99')), \
         patch('routes.open_api_prompt_templates.delete_template', return_value=False) as dt:
        resp = client.delete(f'{BASE}/tpl-1', headers=HDR)
    assert resp.status_code == 404
    dt.assert_called_once_with('user-99', 'tpl-1')
