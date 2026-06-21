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
