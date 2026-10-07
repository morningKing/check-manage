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


def test_batch_running_session_token_accepted(fake_db, mock_cursor):
    """批子会话（status='running'）的 token 必须通过鉴权——批子会话状态机
    pending→running→completed 永远不会是 'active'，只认 active 会把平台
    MCP 工具对批任务整体关闭（2026-10-07 R5 实测：配置到位、token 在库，
    仍 unknown token）。SQL 闸门必须是 IN ('active', 'running')。"""
    mock_cursor.fetchone.return_value = (
        "sess_batch_child", "user-1", "developer",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token
        result = validate_session_token("tok_running_child")
    assert result["session_id"] == "sess_batch_child"
    sql = mock_cursor.execute.call_args[0][0]
    assert "'active'" in sql and "'running'" in sql
