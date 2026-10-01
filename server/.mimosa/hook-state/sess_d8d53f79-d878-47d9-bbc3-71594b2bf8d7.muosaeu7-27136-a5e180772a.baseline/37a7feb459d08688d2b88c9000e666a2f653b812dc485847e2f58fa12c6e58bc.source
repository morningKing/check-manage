"""对外 API 子会话文件下载的路径校验（D2 修复回归）。

历史缺陷：download / download-all 用 normpath+commonpath 弱校验，不解析
符号链接（工作区内指向外部的 symlink 可被读出）；现统一走 safe_resolve
（resolve 后必须仍落在工作区内）。

沿用 test_open_api_batches_sessions.py 的 mock 鉴权模式；工作区用真实
临时目录，保证 safe_resolve 的 resolve 语义被真实执行。
"""
import io
import os
import sys
import zipfile
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

BASE = '/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_db(mock_conn):
    yield mock_conn


def _auth(mock_conn, mock_cursor, detail):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, 'user-42', None, None)
    return patch('auth.get_db', lambda: _fake_db(mock_conn)), patch(
        'routes.open_api_batches._current_key',
        return_value={'id': 'ak-1', 'name': 'k', 'ownerUserId': 'user-42'}), patch(
        'routes.open_api_batches.get_batch_detail', return_value=detail)


def _detail(ws):
    child = {'id': 's-1', 'status': 'completed', 'batch_seq': 0,
             'batch_input_file': 'report.pdf', 'workspace_path': str(ws),
             'opencode_session_id': 'oc-1', 'error_message': None,
             'last_message_preview': None}
    return {'batch': {'id': 'b-1', 'user_id': 'user-42', 'name': 'n',
                      'status': 'completed', 'total': 1, 'done': 1,
                      'failed': 0, 'agent': None, 'model': None,
                      'api_key_id': 'ak-1', 'created_at': None,
                      'completed_at': None},
            'sessions': [child]}


@pytest.fixture
def ws_env(tmp_path):
    ws = tmp_path / 'ws-child'
    (ws / 'outputs').mkdir(parents=True)
    (ws / 'outputs' / 'report.txt').write_text('hello', encoding='utf-8')
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOP SECRET', encoding='utf-8')
    return ws, secret


def _symlink_or_skip(link, target):
    try:
        os.symlink(str(target), str(link))
    except OSError:
        pytest.skip('当前环境无 symlink 权限')


def test_download_happy_path(client, mock_conn, mock_cursor, ws_env):
    ws, secret = ws_env
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2]:
        r = client.get(f'{BASE}/b-1/sessions/0/files/download?path=outputs/report.txt',
                       headers=HDR)
    assert r.status_code == 200
    assert b'hello' in r.data


def test_download_rejects_traversal(client, mock_conn, mock_cursor, ws_env):
    ws, secret = ws_env
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2]:
        r = client.get(f'{BASE}/b-1/sessions/0/files/download?path=../secret.txt',
                       headers=HDR)
    assert r.status_code == 400
    assert r.get_json()['error'] == '路径非法'


def test_download_rejects_absolute_path(client, mock_conn, mock_cursor, ws_env):
    ws, secret = ws_env
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2]:
        r = client.get(
            f'{BASE}/b-1/sessions/0/files/download?path={os.path.abspath(str(secret))}',
            headers=HDR)
    assert r.status_code == 400


def test_download_rejects_symlink_escape(client, mock_conn, mock_cursor, ws_env):
    """工作区内的 symlink 指向外部文件：修复后必须拒绝（此前可读出）。"""
    ws, secret = ws_env
    _symlink_or_skip(ws / 'leak.txt', secret)
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2]:
        r = client.get(f'{BASE}/b-1/sessions/0/files/download?path=leak.txt',
                       headers=HDR)
    assert r.status_code == 400


def test_download_missing_file_404(client, mock_conn, mock_cursor, ws_env):
    ws, secret = ws_env
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2]:
        r = client.get(f'{BASE}/b-1/sessions/0/files/download?path=outputs/nope.txt',
                       headers=HDR)
    assert r.status_code == 404


def test_download_all_skips_escape_records(client, mock_conn, mock_cursor,
                                           ws_env):
    """变更记录里的越界路径（含 ..、symlink）打包时必须被跳过而非写出。"""
    ws, secret = ws_env
    _symlink_or_skip(ws / 'leak2.txt', secret)
    records = [{'path': 'outputs/report.txt', 'status': 'added'},
               {'path': '../secret.txt', 'status': 'added'},
               {'path': 'leak2.txt', 'status': 'added'}]
    patches = _auth(mock_conn, mock_cursor, _detail(ws))
    with patches[0], patches[1], patches[2], \
         patch('utils.workspace_changes.get_session_files',
               lambda cid: records):
        r = client.get(f'{BASE}/b-1/sessions/0/files/download-all', headers=HDR)
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    names = zf.namelist()
    assert any(n.endswith('report.txt') for n in names)
    assert not any('secret' in n for n in names)
    assert not any('leak' in n for n in names)
