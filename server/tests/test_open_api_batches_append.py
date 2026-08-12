"""对外接口 POST /v1/ai-batches/<id>/append 的测试。

与 create 共用 `_validate_files`，所以这里**不重复**逐条校验路径穿越/存在性的
细节（那些在 test_open_api_batches_crud.py 里），只钉住 append 自己的契约：
密钥作用域、不限终态、上限、404、以及 worker 被唤醒。
"""
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    """`auth.py` 在模块顶层 `from db import get_db` 做了一次性绑定，conftest 的
    `app` fixture 只 patch 了 `db.get_db`，所以要让真实的 `api_key_required`
    网关在单独跑本文件时可控，必须单独 patch `auth.get_db`。"""
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _ok_file():
    return {'name': 'b.pdf', 'path': 'batch-staging/user-42/abc/b.pdf'}


def _stage(tmp_path, rel='batch-staging/user-42/abc/b.pdf'):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'pdf')
    return p


def _detail(total=5, status='running'):
    return {'batch': {'id': 'b-1', 'status': status, 'total': total}, 'sessions': []}


def test_append_rejects_empty_files(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR, json={'files': []})
    assert resp.status_code == 400


def test_append_rejects_foreign_path(client, mock_conn, mock_cursor):
    """核心安全断言：不能把别人暂存目录里的文件追加进自己的批任务。

    内部接口 (routes/ai_chat_batches.py::append) 不做这个校验 —— JWT 之后天然
    受限；对外这层不补就是横向越权。
    """
    _auth_passes(mock_cursor)
    bad = {'name': 'b.pdf', 'path': 'batch-staging/user-99/abc/b.pdf'}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.append_to_batch') as ab:
        resp = client.post(f'{BASE}/b-1/append', headers=HDR, json={'files': [bad]})
    assert resp.status_code == 400
    assert '文件路径无效' in resp.get_json()['error']
    ab.assert_not_called()


def test_append_rejects_missing_staged_file(client, mock_conn, mock_cursor, tmp_path):
    """path 形状合法但文件已被 TTL 清理 —— 不能留下注定失败的子任务。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch') as ab:
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.status_code == 400
    assert '已过期或不存在' in resp.get_json()['error']
    ab.assert_not_called()


def test_append_rejects_too_many_files_in_one_call(client, mock_conn, mock_cursor):
    from utils.batch_repo import MAX_FILES_PER_BATCH
    _auth_passes(mock_cursor)
    files = [_ok_file() for _ in range(MAX_FILES_PER_BATCH + 1)]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR, json={'files': files})
    assert resp.status_code == 400
    assert str(MAX_FILES_PER_BATCH) in resp.get_json()['error']


def test_append_is_scoped_to_source_key(client, mock_conn, mock_cursor, tmp_path):
    """必须带 api_key_id —— 否则一把密钥能撑大另一把密钥（或界面）建的批任务。"""
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch',
               return_value=_detail(total=6)) as ab, \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.status_code == 200
    assert ab.call_args[0][0] == 'user-42'          # 归属用户
    assert ab.call_args[0][1] == 'b-1'
    assert ab.call_args[1]['api_key_id'] == 'ak-1'  # 来源密钥


def test_append_not_found_is_404(client, mock_conn, mock_cursor, tmp_path):
    """别的密钥建的批任务 → repo 查不到 → 404（不泄漏存在性，与其余端点一致）。"""
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch', return_value=None), \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.status_code == 404


def test_append_allowed_on_completed_batch(client, mock_conn, mock_cursor, tmp_path):
    """与 retry-failed 不同，append **刻意不限终态** —— 往已完成的批任务再加几个
    文件是合法用法，追加后批任务重回 running。"""
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch',
               return_value=_detail(total=4, status='running')) as ab, \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'running'
    ab.assert_called_once()


def test_append_returns_contract_fields_only(client, mock_conn, mock_cursor, tmp_path):
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    leaky = {'batch': {'id': 'b-1', 'status': 'running', 'total': 6,
                       'user_id': 'user-42', 'prompt': '内部 prompt'},
             'sessions': [{'opencode_session_id': 'oc-1'}]}
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch', return_value=leaky), \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.get_json() == {'batchId': 'b-1', 'status': 'running',
                               'total': 6, 'appended': 1}
    assert 'opencode_session_id' not in resp.get_data(as_text=True)
    assert 'prompt' not in resp.get_data(as_text=True)


def test_append_notifies_worker(client, mock_conn, mock_cursor, tmp_path):
    """不唤醒 worker 的话，追加的子任务要等下一轮轮询才起跑。"""
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch', return_value=_detail()), \
         patch('routes.open_api_batches.get_worker') as gw:
        client.post(f'{BASE}/b-1/append', headers=HDR, json={'files': [_ok_file()]})
    gw.return_value.notify.assert_called_once()


def test_append_over_batch_cap_is_400_not_500(client, mock_conn, mock_cursor, tmp_path):
    """repo 在「追加后超过单批上限」时抛 ValueError，必须落成 400 而不是裸 500。"""
    _auth_passes(mock_cursor)
    _stage(tmp_path)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)), \
         patch('routes.open_api_batches.append_to_batch',
               side_effect=ValueError('max 50 files per batch')), \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/append', headers=HDR,
                           json={'files': [_ok_file()]})
    assert resp.status_code == 400
    assert 'max 50 files per batch' in resp.get_json()['error']


def test_append_body_is_under_the_json_size_gate(client, mock_conn, mock_cursor):
    """新路由落在 /v1/ai-batches 前缀下，所以自动受 1 MB JSON 门限制（不是 uploads
    的 101 MB）—— 钉住这一点，免得日后有人把 append 挪出前缀而失去防护。"""
    from utils.upload_limits import MAX_JSON_BODY_BYTES, body_limit_for_path
    assert body_limit_for_path('/api/v1/ai-batches/b-1/append') == MAX_JSON_BODY_BYTES
    assert body_limit_for_path('/v1/ai-batches/b-1/append') == MAX_JSON_BODY_BYTES
