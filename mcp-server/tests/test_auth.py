"""Tests for mcp-server auth.validate_session_token."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def test_valid_token_returns_user(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = (
        "sess_123", "user-1", "developer",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token
        result = validate_session_token("tok_valid")
    assert result == {
        "session_id": "sess_123",
        "user_id": "user-1",
        "role": "developer",
    }


def test_token_query_allows_non_active_batch_sessions(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = (
        "batch-child", "user-1", "developer",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token
        validate_session_token("tok_batch")

    query = mock_cursor.execute.call_args.args[0]
    assert "s.batch_id IS NOT NULL" in query
    assert "'pending', 'running', 'completed', 'failed', 'cancelled'" in query
    assert "s.status = 'active'" in query


def test_expired_token_raises(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = (
        "sess_123", "user-1", "developer",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token, TokenExpired
        with pytest.raises(TokenExpired):
            validate_session_token("tok_expired")


def test_unknown_token_raises(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = None
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token, TokenInvalid
        with pytest.raises(TokenInvalid):
            validate_session_token("tok_missing")
