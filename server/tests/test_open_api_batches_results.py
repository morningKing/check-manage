import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/api/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _detail(status='completed'):
    return {'batch': {'id': 'b-1', 'name': 'n', 'status': status, 'total': 1,
                      'done': 1, 'failed': 0, 'agent': None, 'model': None,
                      'created_at': None, 'completed_at': None},
            'sessions': []}


@contextmanager
def _fake_auth_db(mock_conn):
    """给 `auth.get_db` 打桩用的 fake get_db，与 test_open_api_batches_upload.py::_fake_auth_db
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
    mock_cursor.fetchone.return_value = ('ak-1', 'k', True, owner)


# ---------- results ----------

def test_results_404_when_not_owned(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-1/results', headers=HDR)
    assert resp.status_code == 404


def test_results_returns_per_child_contract(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    rows = [{'name': 'a.pdf', 'status': 'completed', 'output': '完整\n文本', 'error': None},
            {'name': 'b.pdf', 'status': 'failed', 'output': None, 'error': '超时'}]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=_detail()), \
         patch('routes.open_api_batches.get_batch_results', return_value=rows):
        resp = client.get(f'{BASE}/b-1/results', headers=HDR)
    body = resp.get_json()
    assert body['batchId'] == 'b-1'
    assert body['status'] == 'completed'
    assert body['results'] == rows


# ---------- delete ----------
# 注：这两个测试比简报原文多打了一个 get_batch_detail 补丁——实现里删除前要靠它
# 拿到 sessions 列表做工作区清理（cleanup_batch_workspaces），而 _auth_passes 会把
# mock_cursor.fetchone 的返回值固定成一条 4 元组；若不打桩，未打桩的真实
# get_batch_detail 会在鉴权网关之后复用同一个 mock_cursor 再次 fetchone()，
# 拿到这条鉴权用的元组去 dict(batch) 会直接抛异常。补上这个桩既避免了误报，
# 也让测试准确描述“已鉴权 + 批任务是否存在”两件事。

def test_delete_404_when_not_owned(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None), \
         patch('routes.open_api_batches.delete_batch', return_value=False):
        resp = client.delete(f'{BASE}/b-1', headers=HDR)
    assert resp.status_code == 404


def test_delete_passes_api_key_id(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=_detail()), \
         patch('routes.open_api_batches.cleanup_batch_workspaces') as cleanup, \
         patch('routes.open_api_batches.delete_batch', return_value=True) as db:
        resp = client.delete(f'{BASE}/b-1', headers=HDR)
    assert resp.status_code == 200
    assert db.call_args[1]['api_key_id'] == 'ak-1'
    cleanup.assert_called_once()


# ---------- retry-failed ----------

def test_retry_404_when_not_owned(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
    assert resp.status_code == 404


def test_retry_409_when_still_running(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    for st in ('pending', 'running'):
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=_detail(st)):
            resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
        assert resp.status_code == 409, st
        assert '仍在执行' in resp.get_json()['error']


def test_retry_ok_on_terminal_states(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    for st in ('completed', 'partial', 'failed'):
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=_detail(st)), \
             patch('routes.open_api_batches.reset_failed_to_pending', return_value=2), \
             patch('routes.open_api_batches.get_worker'):
            resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
        assert resp.status_code == 200, st
        assert resp.get_json() == {'retried': 2}


def test_retry_zero_is_200_not_error(client, mock_conn, mock_cursor):
    """终态但没有可重试的子任务 → 200 + retried 0，不报错。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=_detail('completed')), \
         patch('routes.open_api_batches.reset_failed_to_pending', return_value=0), \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
    assert resp.status_code == 200
    assert resp.get_json() == {'retried': 0}
