"""Tests for AI chat session governance: close + reopen endpoints."""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _db(rows):
    """Create a fake get_db context manager returning a connection whose cursor
    yields the given rows from fetchone (in order)."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = list(rows)
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    return fake, cur


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}


def test_close_sets_status_and_audits():
    import routes.ai_chat as ac
    # _load_session_for_user does: fetchone (SELECT row), then UPDATE last_active_at (no fetch)
    # close_session does: UPDATE status (no fetch)
    fake, cur = _db([('s1', 'u1', 'oc1', 'active', '/ws')])
    with patch.object(ac, 'get_db', fake), \
         patch.object(ac, 'stop_listener') as stop, \
         patch.object(ac, 'log_operation') as logop:
        r = _client().post('/ai/chat/sessions/s1/close', headers=_h())
    assert r.status_code == 200
    # Verify the UPDATE set status to closed
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list if c.args]
    assert any("status='closed'" in s or "status = 'closed'" in s for s in sql_calls)
    stop.assert_called_once_with('s1')
    logop.assert_called_once()
    assert logop.call_args.args[1] == 'ai_chat_session'  # target_type


def test_close_others_session_404():
    import routes.ai_chat as ac
    # _load_session_for_user returns None for non-owner
    fake, cur = _db([None])
    with patch.object(ac, 'get_db', fake):
        r = _client().post('/ai/chat/sessions/sX/close', headers=_h())
    assert r.status_code == 404


def test_reopen_closed_to_active():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'closed', '/ws')])
    with patch.object(ac, 'get_db', fake), patch.object(ac, 'log_operation'):
        r = _client().post('/ai/chat/sessions/s1/reopen', headers=_h())
    assert r.status_code == 200
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list if c.args]
    assert any("status = 'active'" in s for s in sql_calls)


def test_reopen_archived_forbidden():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'archived', '/ws')])
    with patch.object(ac, 'get_db', fake):
        r = _client().post('/ai/chat/sessions/s1/reopen', headers=_h())
    assert r.status_code == 403


def test_close_archived_forbidden():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'archived', '/ws')])
    with patch.object(ac, 'get_db', fake), patch.object(ac, 'stop_listener'):
        r = _client().post('/ai/chat/sessions/s1/close', headers=_h())
    assert r.status_code == 409


def test_reopen_deleted_forbidden():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'deleted', '/ws')])
    with patch.object(ac, 'get_db', fake):
        r = _client().post('/ai/chat/sessions/s1/reopen', headers=_h())
    assert r.status_code == 409


def test_physical_delete_endpoint_removed():
    r = _client().delete('/ai/chat/sessions/s1', headers=_h())
    assert r.status_code == 405  # 个人物理删已移除（路由仍存在于其它方法 → 405）


def test_list_includes_closed():
    import routes.ai_chat as ac
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = [
        ('s1', '会话1', None, None, None, 'active'),
        ('s2', '会话2', None, None, None, 'closed'),
    ]
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    with patch.object(ac, 'get_db', fake):
        r = _client().get('/ai/chat/sessions', headers=_h())
    assert r.status_code == 200
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 'closed' in sql
    data = r.get_json()
    statuses = [s['status'] for s in data['sessions']]
    assert 'active' in statuses and 'closed' in statuses


# --- Task 3: admin governance endpoints (RBAC gated) ---
# The conftest autouse fixture `_reset_and_prime_permission_cache` seeds the
# 'developer' role with `admin_keys: set()` (no admin capabilities), so any
# endpoint decorated with `@require_permission('admin.ai_chat_admin')` will
# reject a developer token with 403. No DB needed for permission resolution.


def test_admin_archive_requires_permission():
    """developer (no admin.ai_chat_admin) -> 403"""
    r = _client().post('/ai/chat/sessions/s1/archive', headers=_h(role='developer'))
    assert r.status_code == 403


def test_admin_list_requires_permission():
    """developer (no admin.ai_chat_admin) -> 403"""
    r = _client().get('/ai/chat/admin/sessions', headers=_h(role='developer'))
    assert r.status_code == 403


# --- Admin happy-path tests (admin role = superuser, bypasses permission gate) ---


def test_admin_list_sessions_ok():
    import routes.ai_chat as ac
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = [('s1', 'u1', '会话1', 'active', None),
                                 ('s2', 'u2', None, 'archived', None)]
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    with patch.object(ac, 'get_db', fake):
        r = _client().get('/ai/chat/admin/sessions', headers=_h(role='admin'))
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['sessions']) == 2
    assert data['sessions'][0]['status'] == 'active'
    assert data['sessions'][1]['title'] == '新会话'  # None title defaults


def test_admin_archive_ok():
    import routes.ai_chat as ac
    conn = MagicMock(); cur = MagicMock()
    cur.rowcount = 1
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    with patch.object(ac, 'get_db', fake), \
         patch.object(ac, 'stop_listener'), patch.object(ac, 'log_operation'):
        r = _client().post('/ai/chat/sessions/s1/archive', headers=_h(role='admin'))
    assert r.status_code == 200
    assert r.get_json()['status'] == 'archived'
    assert any("status='archived'" in str(c.args[0]) for c in cur.execute.call_args_list)
