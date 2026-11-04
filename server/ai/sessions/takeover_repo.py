"""批任务接管令牌仓储（对外接管页 spec D1）。

只存 SHA-256 哈希；resolve 时三元组联查会话与批（任一不存在即视为无效）。
令牌不建外键：api_keys 可被删除，令牌生命周期由 TTL 约束，刻意解耦。

TTL 部署级可配置（2026-10-25 需求）：TAKEOVER_TTL_MAX_HOURS（上限，默认
168）/ TAKEOVER_TTL_DEFAULT_HOURS（未显式指定时的默认，默认 24），调用时
读 env——部署改 env 重启生效，测试 monkeypatch 免重启。1h 为硬下限（防
误配 0/负值），默认值恒钳入 [1, max]。
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor

from db import get_db

TTL_MIN_H = 1
BUILTIN_TTL_MAX_H = 24 * 7
BUILTIN_TTL_DEFAULT_H = 24

# TTL 上限的绝对天花板：防误配出年级别令牌的粗护栏（要更长请改代码并过安全评审）
TTL_ABS_CAP_H = 24 * 365


def _env_int(name: str, fallback: int) -> int:
    raw = (os.getenv(name) or '').strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def ttl_max_hours() -> int:
    return max(TTL_MIN_H, min(_env_int('TAKEOVER_TTL_MAX_HOURS', BUILTIN_TTL_MAX_H),
                              TTL_ABS_CAP_H))


def ttl_default_hours() -> int:
    return max(TTL_MIN_H, min(_env_int('TAKEOVER_TTL_DEFAULT_HOURS', BUILTIN_TTL_DEFAULT_H),
                              ttl_max_hours()))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_takeover_token(owner_user_id: str, batch_id: str, session_id: str,
                          api_key_id: str, ttl_hours: int | None = None) -> tuple[str, datetime]:
    """ttl_hours=None 表示调用方未指定 → 应用部署级默认（TAKEOVER_TTL_DEFAULT_HOURS）；
    显式指定则钳入 [TTL_MIN_H, TAKEOVER_TTL_MAX_HOURS]。"""
    max_h = ttl_max_hours()
    if ttl_hours is None:
        ttl_h = ttl_default_hours()
    else:
        ttl_h = max(TTL_MIN_H, min(int(ttl_hours), max_h))
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
