"""批任务接管令牌仓储（对外接管页 spec D1）。

只存 SHA-256 哈希；resolve 时三元组联查会话与批（任一不存在即视为无效）。
令牌不建外键：api_keys 可被删除，令牌生命周期由 TTL 约束，刻意解耦。
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor

from db import get_db

MIN_TTL_H, MAX_TTL_H = 1, 24 * 7


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_takeover_token(owner_user_id: str, batch_id: str, session_id: str,
                          api_key_id: str, ttl_hours: int = 24) -> tuple[str, datetime]:
    ttl_h = max(MIN_TTL_H, min(MAX_TTL_H, int(ttl_hours or 24)))
    token = 'tk_' + secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_h)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO batch_takeover_tokens "
                "(id, token_hash, api_key_id, owner_user_id, batch_id, session_id, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ('tkt_' + secrets.token_urlsafe(8), _hash(token), api_key_id,
                 owner_user_id, batch_id, session_id, expires))
        conn.commit()
    return token, expires


def resolve_takeover_token(token_plain: str | None) -> dict | None:
    if not token_plain or len(token_plain) > 128:
        return None
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id AS token_id, t.expires_at, t.owner_user_id,"
                " t.batch_id, t.session_id,"
                " s.status AS child_status, s.batch_seq, s.batch_input_file, s.title,"
                " b.name AS batch_name, b.status AS batch_status"
                " FROM batch_takeover_tokens t"
                " JOIN ai_chat_sessions s ON s.id = t.session_id AND s.batch_id = t.batch_id"
                " JOIN ai_chat_batches b ON b.id = t.batch_id"
                " WHERE t.token_hash = %s AND t.revoked_at IS NULL"
                " AND t.expires_at > NOW()",
                (_hash(token_plain),))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE batch_takeover_tokens SET last_used_at = NOW() WHERE id = %s",
                        (row['token_id'],))
        conn.commit()
        return dict(row)


def revoke_takeover_token(owner_user_id: str, batch_id: str,
                          session_id: str, token_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE batch_takeover_tokens SET revoked_at = NOW() "
                "WHERE id = %s AND owner_user_id = %s AND batch_id = %s "
                "AND session_id = %s AND revoked_at IS NULL",
                (token_id, owner_user_id, batch_id, session_id))
            hit = cur.rowcount == 1
        conn.commit()
    return hit
