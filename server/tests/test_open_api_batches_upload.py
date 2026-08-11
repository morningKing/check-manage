import io
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

UPLOAD_URL = '/v1/ai-batches/uploads'


def _key_info(owner='user-42'):
    return {'id': 'ak-1', 'name': '集成密钥', 'ownerUserId': owner}


@contextmanager
def _fake_auth_db(mock_conn):
    """给 `auth.get_db` 打桩用的 fake get_db，与 test_routes_open_api.py::_make_mock_db
    同一模式：`auth.py` 在模块顶层 `from db import get_db` 做了一次性绑定，
    conftest.py 的 `app` fixture 只 patch 了 `db.get_db`（不含 `auth.get_db`），
    所以要让真实的 `api_key_required` 网关在测试里可控，必须单独 patch `auth.get_db`。"""
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    """让真实的 auth.api_key_required 网关放行（返回一条 is_active=True 的密钥行）。

    这里返回的 owner 只是让网关本身的 SELECT 有个合理的行可用，不代表测试断言的
    ownerUserId ——各测试仍通过 patch('routes.open_api_batches._current_key', ...)
    单独控制 g.api_key_info 在视图逻辑里的取值，两者互不干扰。
    """
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def test_missing_api_key_is_401(client):
    resp = client.post(UPLOAD_URL)
    assert resp.status_code == 401


def test_key_without_owner_is_403(client, mock_conn, mock_cursor):
    """存量密钥（owner_user_id 为 NULL）不能用 AI 批任务接口。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key_info(None)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'})
    assert resp.status_code == 403
    assert '未绑定用户' in resp.get_json()['error']


def test_no_files_is_400(client, mock_conn, mock_cursor, tmp_path):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data', data={})
    assert resp.status_code == 400


def test_uploads_multiple_files_and_returns_paths(client, mock_conn, mock_cursor, tmp_path):
    _auth_passes(mock_cursor)
    data = {'files': [(io.BytesIO(b'a' * 10), '订单A.pdf'),
                      (io.BytesIO(b'b' * 10), '订单B.pdf')]}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data', data=data)
    assert resp.status_code == 201
    files = resp.get_json()['files']
    assert [f['name'] for f in files] == ['订单A.pdf', '订单B.pdf']
    for f in files:
        assert f['path'].startswith('batch-staging/user-42/')


def test_single_file_over_limit_is_400(client, mock_conn, mock_cursor, tmp_path):
    from routes.open_api_batches import MAX_FILE_BYTES
    _auth_passes(mock_cursor)
    big = io.BytesIO(b'x' * (MAX_FILE_BYTES + 1))
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data',
                           data={'files': (big, 'big.bin')})
    assert resp.status_code == 400
    assert '20' in resp.get_json()['error']


# ---------- 路径校验（这是对外后唯一新增的横向越权面）----------

def test_validate_staged_path_accepts_own_dir():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-42/abc/a.pdf', 'user-42') is True


def test_validate_staged_path_rejects_other_user():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-99/abc/a.pdf', 'user-42') is False


def test_validate_staged_path_rejects_traversal():
    from routes.open_api_batches import _validate_staged_path
    for bad in ['batch-staging/user-42/../user-99/a.pdf',
                'batch-staging/user-42/abc/../../user-99/a.pdf',
                '../../../etc/passwd',
                '/etc/passwd']:
        assert _validate_staged_path(bad, 'user-42') is False, bad


def test_validate_staged_path_rejects_prefix_lookalike():
    """user-4 不能借 user-42 的前缀混进来，反之亦然。"""
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-420/abc/a.pdf', 'user-42') is False


def test_validate_staged_path_rejects_backslash_traversal():
    """Windows 分隔符也要挡住。"""
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging\\user-99\\a.pdf', 'user-42') is False


def test_validate_staged_path_rejects_empty_string():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('', 'user-42') is False


def test_validate_staged_path_rejects_none_owner():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-42/a.pdf', None) is False
    assert _validate_staged_path('batch-staging/user-42/a.pdf', '') is False


def test_validate_staged_path_rejects_too_short():
    """只有 batch-staging/<user> 两段、没有文件名这一段，同样要拒绝。"""
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-42', 'user-42') is False
