"""Opaque session token: generate, renew, revoke.

Tokens are urlsafe base64, 32 bytes of entropy (~43 chars).
Stored in ai_chat_sessions.session_token with token_expires_at.
"""

import secrets
from datetime import datetime, timedelta, timezone
from db import get_db


def generate_token(session_id: str, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET session_token = %s, token_expires_at = %s "
            "WHERE id = %s",
            (token, expires, session_id),
        )
    return token


def renew_token(session_id: str, ttl_hours: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET token_expires_at = %s, last_active_at = NOW() "
            "WHERE id = %s",
            (expires, session_id),
        )


def revoke_token(session_id: str) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET status = 'revoked', token_expires_at = NOW() "
            "WHERE id = %s",
            (session_id,),
        )
