import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/api/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    """同 test_open_api_batches_upload.py 的模式：`auth.py` 在模块顶层
    `from db import get_db` 做了一次性绑定，conftest.py 的 `app` fixture 只
    patch 了 `db.get_db`（不含 `auth.get_db`），所以要让真实的 `api_key_required`
    网关在单独跑本文件时可控，必须单独 patch `auth.get_db`。"""
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    """让真实的 auth.api_key_required 网关放行（返回一条 is_active=True 的密钥行）。

    这里返回的 owner 只是让网关本身的 SELECT 有个合理的行可用，不代表测试断言的
    ownerUserId ——各测试仍通过 patch('routes.open_api_batches._current_key', ...)
    单独控制 g.api_key_info 在视图逻辑里的取值，两者互不干扰。
    """
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _ok_file():
    return {'name': 'a.pdf', 'path': 'batch-staging/user-42/abc/a.pdf'}


def test_create_requires_name_and_prompt(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'files': [_ok_file()]})
    assert resp.status_code == 400


def test_create_rejects_empty_files(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'name': 'n', 'prompt': 'p', 'files': []})
    assert resp.status_code == 400


def test_create_rejects_foreign_path(client, mock_conn, mock_cursor):
    """核心安全断言：不能引用别人暂存目录里的文件。"""
    _auth_passes(mock_cursor)
    bad = {'name': 'a.pdf', 'path': 'batch-staging/user-99/abc/a.pdf'}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': [bad]})
    assert resp.status_code == 400
    assert '文件路径无效' in resp.get_json()['error']


def test_create_rejects_too_many_files(client, mock_conn, mock_cursor):
    from utils.batch_repo import MAX_FILES_PER_BATCH
    _auth_passes(mock_cursor)
    files = [_ok_file() for _ in range(MAX_FILES_PER_BATCH + 1)]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': files})
    assert resp.status_code == 400
    assert str(MAX_FILES_PER_BATCH) in resp.get_json()['error']


def test_create_rejects_overlong_prompt(client, mock_conn, mock_cursor):
    from routes.open_api_batches import MAX_PROMPT_CHARS
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'x' * (MAX_PROMPT_CHARS + 1),
                                 'files': [_ok_file()]})
    assert resp.status_code == 400


def test_create_passes_api_key_id_and_owner(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    created = {'batch': {'id': 'b-1', 'status': 'pending', 'total': 1}, 'sessions': []}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.create_batch', return_value=created) as cb, \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': [_ok_file()],
                                 'agent': 'build', 'model': 'm'})
    assert resp.status_code == 201
    assert resp.get_json() == {'batchId': 'b-1', 'status': 'pending', 'total': 1}
    assert cb.call_args[0][0] == 'user-42'          # 归属用户
    assert cb.call_args[1]['api_key_id'] == 'ak-1'  # 来源密钥
    assert cb.call_args[1]['agent'] == 'build'


def test_list_is_scoped_to_key(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        resp = client.get(BASE, headers=HDR)
    assert resp.status_code == 200
    assert lb.call_args[1]['api_key_id'] == 'ak-1'


def test_list_page_size_capped_at_100(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        client.get(f'{BASE}?pageSize=9999', headers=HDR)
    assert lb.call_args[1]['page_size'] == 100


def test_detail_not_found_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-nope', headers=HDR)
    assert resp.status_code == 404


def test_detail_returns_contract_fields_only(client, mock_conn, mock_cursor):
    """不能把 opencode_session_id / workspace_path 这类内部字段吐出去。"""
    _auth_passes(mock_cursor)
    detail = {'batch': {'id': 'b-1', 'name': 'n', 'status': 'running',
                        'total': 3, 'done': 1, 'failed': 0,
                        'agent': 'build', 'model': None,
                        'created_at': None, 'completed_at': None,
                        'user_id': 'user-42', 'api_key_id': 'ak-1',
                        'prompt': '内部 prompt'},
              'sessions': [{'opencode_session_id': 'oc-1'}]}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=detail):
        resp = client.get(f'{BASE}/b-1', headers=HDR)
    body = resp.get_json()
    assert set(body) == {'batchId', 'name', 'status', 'total', 'done', 'failed',
                         'agent', 'model', 'createdAt', 'completedAt'}
    assert 'opencode_session_id' not in resp.get_data(as_text=True)
    assert 'user_id' not in resp.get_data(as_text=True)
