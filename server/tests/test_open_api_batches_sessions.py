"""Tests for the 5 new sub-session endpoints on /v1/ai-batches.

Endpoints covered:
  GET  /<batch_id>/sessions/<child_id>/messages
  GET  /<batch_id>/sessions/<child_id>/files
  GET  /<batch_id>/sessions/<child_id>/files/download
  GET  /<batch_id>/sessions/<child_id>/files/download-all
  POST /<batch_id>/sessions/<child_id>/continue

Uses the same mock-based pattern as test_open_api_batches_crud.py.
Route handlers import helper modules locally (inside the function body),
so we patch at the SOURCE module (e.g. utils.batch_repo) rather than
routes.open_api_batches.
"""
import io
import os
import sys
import zipfile
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

BASE = '/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _batch_detail(batch_id='b-1', sessions=None):
    """Fake get_batch_detail return value."""
    if sessions is None:
        sessions = [{
            'id': 's-1',
            'status': 'completed',
            'batch_seq': 0,
            'batch_input_file': 'batch-staging/user-42/up/report.pdf',
            'workspace_path': '/tmp/ws/s-1',
            'opencode_session_id': 'oc-s-1',
            'error_message': None,
            'last_message_preview': 'done',
        }]
    return {
        'batch': {
            'id': batch_id,
            'user_id': 'user-42',
            'name': 'test-batch',
            'status': 'completed',
            'total': 1,
            'done': 1,
            'failed': 0,
            'agent': None,
            'model': None,
            'api_key_id': 'ak-1',
            'created_at': None,
            'completed_at': None,
        },
        'sessions': sessions,
    }


def _child_session(**overrides):
    """Minimal child session dict with sensible defaults."""
    base = {
        'id': 's-1', 'status': 'completed', 'batch_seq': 0,
        'batch_input_file': 'report.pdf', 'workspace_path': '/tmp/ws',
        'opencode_session_id': 'oc-1', 'error_message': None,
        'last_message_preview': None,
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────
# 4.11 GET /sessions/<child_id>/messages
# ──────────────────────────────────────────────────────────────────

class TestSessionMessages:
    def test_404_when_batch_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=None):
            resp = client.get(f'{BASE}/b-x/sessions/s-1/messages', headers=HDR)
        assert resp.status_code == 404

    def test_404_when_child_not_found(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(f'{BASE}/b-1/sessions/nonexistent.pdf/messages', headers=HDR)
        assert resp.status_code == 404

    def test_returns_messages_by_name(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        fake_msgs = {
            'messages': [
                {'id': 'm1', 'role': 'user', 'content': [{'type': 'text', 'text': 'hi'}],
                 'created_at': '2026-01-01T00:00:00Z'},
                {'id': 'm2', 'role': 'assistant', 'content': [{'type': 'text', 'text': 'hello'}],
                 'created_at': '2026-01-01T00:01:00Z'},
            ],
            'total': 2,
            'truncated': False,
        }
        # get_child_messages is imported locally in the route handler, so
        # patch it at the source module.
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.batch_repo.get_child_messages', return_value=fake_msgs):
            resp = client.get(f'{BASE}/b-1/sessions/report.pdf/messages', headers=HDR)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['batchId'] == 'b-1'
        assert body['child']['name'] == 'report.pdf'
        assert body['child']['seq'] == 0
        assert len(body['messages']) == 2
        assert body['total'] == 2
        assert body['truncated'] is False

    def test_returns_messages_by_seq(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        fake_msgs = {'messages': [], 'total': 0, 'truncated': False}
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.batch_repo.get_child_messages', return_value=fake_msgs):
            resp = client.get(f'{BASE}/b-1/sessions/0/messages', headers=HDR)
        assert resp.status_code == 200
        assert resp.get_json()['child']['seq'] == 0


# ──────────────────────────────────────────────────────────────────
# 4.12 GET /sessions/<child_id>/files
# ──────────────────────────────────────────────────────────────────

class TestSessionFiles:
    def test_404_when_batch_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=None):
            resp = client.get(f'{BASE}/b-x/sessions/s-1/files', headers=HDR)
        assert resp.status_code == 404

    def test_empty_when_no_workspace(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail(sessions=[_child_session(workspace_path=None)])
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(f'{BASE}/b-1/sessions/0/files', headers=HDR)
        assert resp.status_code == 200
        assert resp.get_json()['files'] == []

    def test_returns_file_list(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        fake_files = [
            {'name': 'result.md', 'path': 'output/result.md', 'dir': 'outputs', 'size': 100},
        ]
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.workspace_outputs.list_session_files',
                   return_value=(fake_files, False)):
            resp = client.get(f'{BASE}/b-1/sessions/report.pdf/files', headers=HDR)
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body['files']) == 1
        assert body['files'][0]['name'] == 'result.md'


# ──────────────────────────────────────────────────────────────────
# 4.13 GET /sessions/<child_id>/files/download
# ──────────────────────────────────────────────────────────────────

class TestSessionFileDownload:
    def test_404_when_batch_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=None):
            resp = client.get(f'{BASE}/b-x/sessions/s-1/files/download?path=x', headers=HDR)
        assert resp.status_code == 404

    def test_400_when_no_workspace(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail(sessions=[_child_session(workspace_path=None)])
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(f'{BASE}/b-1/sessions/0/files/download?path=x', headers=HDR)
        assert resp.status_code == 400

    def test_400_when_path_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(f'{BASE}/b-1/sessions/report.pdf/files/download', headers=HDR)
        assert resp.status_code == 400

    def test_400_on_path_traversal(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(
                f'{BASE}/b-1/sessions/report.pdf/files/download?path=../../../etc/passwd',
                headers=HDR)
        assert resp.status_code == 400

    def test_404_when_file_missing(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        detail = _batch_detail(sessions=[_child_session(workspace_path=str(ws))])
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(
                f'{BASE}/b-1/sessions/0/files/download?path=missing.txt',
                headers=HDR)
        assert resp.status_code == 404

    def test_downloads_file(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        (ws / 'result.txt').write_text('hello')
        detail = _batch_detail(sessions=[_child_session(workspace_path=str(ws))])
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(
                f'{BASE}/b-1/sessions/0/files/download?path=result.txt',
                headers=HDR)
        assert resp.status_code == 200
        assert resp.data == b'hello'


# ──────────────────────────────────────────────────────────────────
# 4.14 GET /sessions/<child_id>/files/download-all
# ──────────────────────────────────────────────────────────────────

class TestSessionFilesDownloadAll:
    def test_404_when_batch_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=None):
            resp = client.get(f'{BASE}/b-x/sessions/s-1/files/download-all', headers=HDR)
        assert resp.status_code == 404

    def test_400_when_no_workspace(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail(sessions=[_child_session(workspace_path=None)])
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.get(f'{BASE}/b-1/sessions/0/files/download-all', headers=HDR)
        assert resp.status_code == 400

    def test_returns_zip(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        (ws / 'output').mkdir()
        (ws / 'output' / 'result.md').write_text('# Result')
        detail = _batch_detail(sessions=[_child_session(
            batch_input_file='report.pdf', workspace_path=str(ws))])
        fake_records = [
            {'path': 'output/result.md', 'status': 'added',
             'dataFileId': None, 'firstSeenAt': None, 'lastSeenAt': None},
        ]
        # get_session_files is imported locally — patch at source
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.workspace_changes.get_session_files', return_value=fake_records):
            resp = client.get(f'{BASE}/b-1/sessions/0/files/download-all', headers=HDR)
        assert resp.status_code == 200
        assert resp.content_type == 'application/zip'
        assert 'attachment' in resp.headers.get('Content-Disposition', '')

    def test_zip_contains_file(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        (ws / 'output').mkdir()
        (ws / 'output' / 'result.md').write_text('# Result')
        detail = _batch_detail(sessions=[_child_session(
            batch_input_file='report.pdf', workspace_path=str(ws))])
        fake_records = [
            {'path': 'output/result.md', 'status': 'added',
             'dataFileId': None, 'firstSeenAt': None, 'lastSeenAt': None},
        ]
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.workspace_changes.get_session_files', return_value=fake_records):
            resp = client.get(f'{BASE}/b-1/sessions/0/files/download-all', headers=HDR)
        zf = zipfile.ZipFile(io.BytesIO(resp.data))
        names = zf.namelist()
        assert any('result.md' in n for n in names)

    def test_skips_missing_files(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        detail = _batch_detail(sessions=[_child_session(
            batch_input_file='report.pdf', workspace_path=str(ws))])
        fake_records = [
            {'path': 'output/missing.md', 'status': 'added',
             'dataFileId': None, 'firstSeenAt': None, 'lastSeenAt': None},
        ]
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.workspace_changes.get_session_files', return_value=fake_records):
            resp = client.get(f'{BASE}/b-1/sessions/0/files/download-all', headers=HDR)
        assert resp.status_code == 200

    def test_respects_include_filter(self, client, mock_conn, mock_cursor, tmp_path):
        _auth_passes(mock_cursor)
        ws = tmp_path / 'ws'
        ws.mkdir()
        (ws / 'added.txt').write_text('added')
        (ws / 'modified.txt').write_text('modified')
        detail = _batch_detail(sessions=[_child_session(
            batch_input_file='report.pdf', workspace_path=str(ws))])
        fake_records = [
            {'path': 'added.txt', 'status': 'added',
             'dataFileId': None, 'firstSeenAt': None, 'lastSeenAt': None},
            {'path': 'modified.txt', 'status': 'modified',
             'dataFileId': None, 'firstSeenAt': None, 'lastSeenAt': None},
        ]
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.workspace_changes.get_session_files', return_value=fake_records):
            resp = client.get(
                f'{BASE}/b-1/sessions/0/files/download-all?include=added',
                headers=HDR)
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.data))
        names = zf.namelist()
        assert any('added.txt' in n for n in names)
        assert not any('modified.txt' in n for n in names)


# ──────────────────────────────────────────────────────────────────
# 4.15 POST /sessions/<child_id>/continue
# ──────────────────────────────────────────────────────────────────

class TestSessionContinue:
    def test_404_when_batch_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=None):
            resp = client.post(f'{BASE}/b-x/sessions/s-1/continue',
                               headers=HDR, json={'prompt': 'go'})
        assert resp.status_code == 404

    def test_404_when_child_not_found(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.post(f'{BASE}/b-1/sessions/nonexistent/continue',
                               headers=HDR, json={'prompt': 'go'})
        assert resp.status_code == 404

    def test_400_when_prompt_missing(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.post(f'{BASE}/b-1/sessions/report.pdf/continue',
                               headers=HDR, json={})
        assert resp.status_code == 400

    def test_400_when_prompt_too_long(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        from routes.open_api_batches import MAX_PROMPT_CHARS
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail):
            resp = client.post(
                f'{BASE}/b-1/sessions/report.pdf/continue',
                headers=HDR,
                json={'prompt': 'x' * (MAX_PROMPT_CHARS + 1)})
        assert resp.status_code == 400

    def test_409_when_child_not_terminal(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail(sessions=[_child_session(status='running')])
        # continue_child is imported locally — patch at source
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.batch_repo.continue_child',
                   side_effect=ValueError('only completed/failed children can be continued')):
            resp = client.post(f'{BASE}/b-1/sessions/0/continue',
                               headers=HDR, json={'prompt': 'go'})
        assert resp.status_code == 409

    def test_202_on_success(self, client, mock_conn, mock_cursor):
        _auth_passes(mock_cursor)
        detail = _batch_detail()
        with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
             patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=detail), \
             patch('utils.batch_repo.continue_child', return_value=detail), \
             patch('routes.open_api_batches.get_worker') as mock_worker:
            mock_worker.return_value.notify = MagicMock()
            resp = client.post(f'{BASE}/b-1/sessions/report.pdf/continue',
                               headers=HDR, json={'prompt': 'analyze further'})
        assert resp.status_code == 202
        body = resp.get_json()
        assert body['batchId'] == 'b-1'
        assert body['child']['name'] == 'report.pdf'
        assert body['status'] == 'running'
        mock_worker.return_value.notify.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# _resolve_child helper (tested via HTTP endpoints above, but also
# unit-tested directly — needs Flask app context for jsonify)
# ──────────────────────────────────────────────────────────────────

class TestResolveChild:
    def test_resolve_by_name(self, app):
        from routes.open_api_batches import _resolve_child
        d = _batch_detail(sessions=[
            {'id': 's-1', 'batch_seq': 0,
             'batch_input_file': 'batch-staging/u/o/a.pdf'},
            {'id': 's-2', 'batch_seq': 1,
             'batch_input_file': 'batch-staging/u/o/b.pdf'},
        ])
        with app.app_context():
            child, err = _resolve_child(d, 'a.pdf')
        assert child['id'] == 's-1'
        assert err is None

    def test_resolve_by_seq(self, app):
        from routes.open_api_batches import _resolve_child
        d = _batch_detail(sessions=[
            {'id': 's-1', 'batch_seq': 0,
             'batch_input_file': 'batch-staging/u/o/a.pdf'},
            {'id': 's-2', 'batch_seq': 1,
             'batch_input_file': 'batch-staging/u/o/b.pdf'},
        ])
        with app.app_context():
            child, err = _resolve_child(d, '1')
        assert child['id'] == 's-2'
        assert err is None

    def test_resolve_not_found(self, app):
        from routes.open_api_batches import _resolve_child
        d = _batch_detail()
        with app.app_context():
            child, err = _resolve_child(d, 'nonexistent')
        assert child is None
        assert err is not None


# ──────────────────────────────────────────────────────────────────
# batch_repo.continue_child (real DB integration)
# ──────────────────────────────────────────────────────────────────

class TestContinueChild:
    def test_raises_when_not_terminal(self, db_conn):
        import uuid
        from utils.batch_repo import continue_child
        uid = '_t_continue_' + uuid.uuid4().hex[:8]
        bid = '_t_b_' + uuid.uuid4().hex[:8]
        sid = '_t_s_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name) "
                "VALUES (%s, %s, 'x', 'admin', %s) ON CONFLICT DO NOTHING",
                (uid, uid, uid))
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, status) "
                "VALUES (%s, %s, 't', 'p', 1, 'running')", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq) "
                "VALUES (%s, %s, 'running', %s, 0)", (sid, uid, bid))
        db_conn.commit()
        try:
            with pytest.raises(ValueError, match='only completed/failed'):
                continue_child(uid, bid, sid, 'new prompt')
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            db_conn.commit()

    def test_completed_child_resets_to_pending(self, db_conn):
        import uuid
        from utils.batch_repo import continue_child
        uid = '_t_cont2_' + uuid.uuid4().hex[:8]
        bid = '_t_b2_' + uuid.uuid4().hex[:8]
        sid = '_t_s2_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name) "
                "VALUES (%s, %s, 'x', 'admin', %s) ON CONFLICT DO NOTHING",
                (uid, uid, uid))
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "(id, user_id, name, prompt, total, status, done) "
                "VALUES (%s, %s, 't', 'p', 1, 'completed', 1)", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "(id, user_id, status, batch_id, batch_seq, opencode_session_id) "
                "VALUES (%s, %s, 'completed', %s, 0, 'oc-keep')", (sid, uid, bid))
        db_conn.commit()
        try:
            result = continue_child(uid, bid, sid, 'follow-up prompt')
            assert result is not None
            with db_conn.cursor() as cur:
                cur.execute(
                    "SELECT status, opencode_session_id, continue_prompt "
                    "FROM ai_chat_sessions WHERE id = %s", (sid,))
                row = cur.fetchone()
                assert row[0] == 'pending'
                assert row[1] == 'oc-keep'
                assert row[2] == 'follow-up prompt'
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            db_conn.commit()

    def test_returns_none_for_wrong_user(self, db_conn):
        import uuid
        from utils.batch_repo import continue_child
        uid = '_t_cont3_' + uuid.uuid4().hex[:8]
        bid = '_t_b3_' + uuid.uuid4().hex[:8]
        sid = '_t_s3_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name) "
                "VALUES (%s, %s, 'x', 'admin', %s) ON CONFLICT DO NOTHING",
                (uid, uid, uid))
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "(id, user_id, name, prompt, total, status, done) "
                "VALUES (%s, %s, 't', 'p', 1, 'completed', 1)", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "(id, user_id, status, batch_id, batch_seq, opencode_session_id) "
                "VALUES (%s, %s, 'completed', %s, 0, 'oc-x')", (sid, uid, bid))
        db_conn.commit()
        try:
            result = continue_child('wrong-user', bid, sid, 'prompt')
            assert result is None
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            db_conn.commit()


# ──────────────────────────────────────────────────────────────────
# batch_repo.get_child_messages (real DB integration)
# ──────────────────────────────────────────────────────────────────

class TestGetChildMessages:
    def test_returns_messages(self, db_conn):
        import uuid
        from utils.batch_repo import get_child_messages
        uid = '_t_gcm_' + uuid.uuid4().hex[:8]
        bid = '_t_bcm_' + uuid.uuid4().hex[:8]
        sid = '_t_scm_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name) "
                "VALUES (%s, %s, 'x', 'admin', %s) ON CONFLICT DO NOTHING",
                (uid, uid, uid))
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, status) "
                "VALUES (%s, %s, 't', 'p', 1, 'completed')", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq) "
                "VALUES (%s, %s, 'completed', %s, 0)", (sid, uid, bid))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
                "VALUES (%s, %s, 'user', %s, 0)",
                (sid + ':user', sid, '[{"type":"text","text":"hi"}]'))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
                "VALUES (%s, %s, 'assistant', %s, 1)",
                (sid + ':a1', sid, '[{"type":"text","text":"hello"}]'))
        db_conn.commit()
        try:
            result = get_child_messages(sid)
            assert result['total'] == 2
            assert len(result['messages']) == 2
            assert result['messages'][0]['role'] == 'user'
            assert result['messages'][1]['role'] == 'assistant'
            assert result['truncated'] is False
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            db_conn.commit()

    def test_empty_when_no_messages(self, db_conn):
        import uuid
        from utils.batch_repo import get_child_messages
        uid = '_t_gcm2_' + uuid.uuid4().hex[:8]
        bid = '_t_bcm2_' + uuid.uuid4().hex[:8]
        sid = '_t_scm2_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, display_name) "
                "VALUES (%s, %s, 'x', 'admin', %s) ON CONFLICT DO NOTHING",
                (uid, uid, uid))
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, status) "
                "VALUES (%s, %s, 't', 'p', 1, 'completed')", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq) "
                "VALUES (%s, %s, 'completed', %s, 0)", (sid, uid, bid))
        db_conn.commit()
        try:
            result = get_child_messages(sid)
            assert result['total'] == 0
            assert result['messages'] == []
            assert result['truncated'] is False
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            db_conn.commit()
