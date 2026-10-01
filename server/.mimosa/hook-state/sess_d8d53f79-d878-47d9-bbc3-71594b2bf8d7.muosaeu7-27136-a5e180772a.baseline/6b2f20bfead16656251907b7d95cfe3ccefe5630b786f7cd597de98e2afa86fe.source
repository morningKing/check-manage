"""Tests for server/utils/session_token.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock


def _patch_db(mock_conn):
    from contextlib import contextmanager
    @contextmanager
    def fake():
        yield mock_conn
    return patch("utils.session_token.get_db", fake)


def test_generate_returns_unique_urlsafe(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import generate_token
        a = generate_token("sess_1", ttl_hours=24)
        b = generate_token("sess_2", ttl_hours=24)
    assert a != b
    assert len(a) >= 32
    assert "/" not in a and "+" not in a


def test_renew_extends_expiry(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import renew_token
        renew_token("sess_1", ttl_hours=24)
    args, _ = mock_cursor.execute.call_args
    sql = args[0]
    assert "UPDATE ai_chat_sessions" in sql
    assert "token_expires_at" in sql


def test_revoke_marks_status_revoked(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import revoke_token
        revoke_token("sess_1")
    args, _ = mock_cursor.execute.call_args
    assert "status" in args[0]
    assert "revoked" in args[1] or "revoked" in args[0]
