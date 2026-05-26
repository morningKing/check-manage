"""Validate opaque session tokens issued by Flask, by looking them up
in ai_chat_sessions joined to users.

Pure function over DB — no state, no side effects beyond a SELECT.
"""

from datetime import datetime, timezone
from db import get_db


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


def validate_session_token(token: str) -> dict:
    if not token:
        raise TokenInvalid("missing token")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.id, s.user_id, u.role, s.token_expires_at
            FROM ai_chat_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_token = %s
              AND s.status = 'active'
            """,
            (token,),
        )
        row = cur.fetchone()

    if not row:
        raise TokenInvalid("unknown token")

    session_id, user_id, role, expires_at = row
    if expires_at <= datetime.now(timezone.utc):
        raise TokenExpired("token expired")

    return {"session_id": session_id, "user_id": user_id, "role": role}
