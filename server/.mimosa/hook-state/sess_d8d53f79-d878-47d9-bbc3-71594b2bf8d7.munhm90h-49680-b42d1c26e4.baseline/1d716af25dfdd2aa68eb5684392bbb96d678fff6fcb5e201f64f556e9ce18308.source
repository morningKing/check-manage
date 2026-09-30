"""Open API 批任务文件记录/导入端点测试（/v1/ai-batches/<id>/file-records|import）。"""
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _detail(sessions=None, status='completed'):
    return {'batch': {'id': 'b-1', 'name': 'n', 'status': status, 'total': 2,
                      'done': 2, 'failed': 0, 'agent': None, 'model': None,
                      'created_at': None, 'completed_at': None},
            'sessions': sessions or []}


def _child(sid='sess-1', seq=1, infile='staging/u-1/x/a.pdf', ws='/tmp/ws1',
           status='completed'):
    return {'id': sid, 'status': status, 'batch_seq': seq,
            'batch_input_file': infile, 'workspace_path': ws,
            'opencode_session_id': None, 'error_message': None,
            'last_message_preview': None}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', 'k', True, owner, None, None)


# ---------- file-records ----------

def test_file_records_404_when_not_owned(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-1/file-records', headers=HDR)
    assert resp.status_code == 404


def test_file_records_returns_per_child_files(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    files = [{'path': 'report.md', 'status': 'added', 'dataFileId': None,
              'firstSeenAt': None, 'lastSeenAt': None}]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child()])), \
         patch('utils.workspace_changes.get_session_files', return_value=files) as gsf:
        resp = client.get(f'{BASE}/b-1/file-records', headers=HDR)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['batchId'] == 'b-1'
    # name 与 /results 同口径：batch_input_file 的 basename，不外泄内部路径
    assert body['results'] == [{'name': 'a.pdf', 'seq': 1,
                                'status': 'completed', 'files': files}]
    gsf.assert_called_once_with('sess-1')


# ---------- import ----------

def test_import_requires_name_or_seq(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child()])):
        resp = client.post(f'{BASE}/b-1/import',
                           json={'paths': ['report.md']}, headers=HDR)
    assert resp.status_code == 400


def test_import_bad_paths_400(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child()])):
        resp = client.post(f'{BASE}/b-1/import',
                           json={'name': 'a.pdf'}, headers=HDR)
    assert resp.status_code == 400


def test_import_child_not_found_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child()])):
        resp = client.post(f'{BASE}/b-1/import',
                           json={'name': 'nope.pdf', 'paths': ['x']}, headers=HDR)
    assert resp.status_code == 404


def test_import_no_workspace_400(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child(ws=None)])):
        resp = client.post(f'{BASE}/b-1/import',
                           json={'seq': 1, 'paths': ['x']}, headers=HDR)
    assert resp.status_code == 400


def test_import_by_name_happy_path(client, mock_conn, mock_cursor):
    """API key 来源的导入 uploaded_by 必须是 None（没有登录用户）。"""
    _auth_passes(mock_cursor)
    results = [{'path': 'report.md', 'status': 'imported', 'file': {'id': 'df-1'}}]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail([_child()])), \
         patch('utils.session_file_import.import_recorded_files',
               return_value=results) as imp:
        resp = client.post(f'{BASE}/b-1/import',
                           json={'name': 'a.pdf', 'paths': ['report.md']}, headers=HDR)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['results'] == results
    assert body['name'] == 'a.pdf' and body['seq'] == 1
    assert imp.call_args[0][:3] == ('sess-1', '/tmp/ws1', ['report.md'])
    assert imp.call_args[1]['uploaded_by'] is None


def test_import_by_seq_happy_path(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    children = [_child(seq=1), _child(sid='sess-2', seq=2,
                                      infile='staging/u-1/x/b.pdf', ws='/tmp/ws2')]
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail',
               return_value=_detail(children)), \
         patch('utils.session_file_import.import_recorded_files',
               return_value=[]) as imp:
        resp = client.post(f'{BASE}/b-1/import',
                           json={'seq': 2, 'paths': ['x.py']}, headers=HDR)
    assert resp.status_code == 200
    assert imp.call_args[0][0] == 'sess-2'          # seq 定位到第二个子会话
    assert resp.get_json()['name'] == 'b.pdf'
