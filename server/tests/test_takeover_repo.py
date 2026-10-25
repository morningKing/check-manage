# -*- coding: utf-8 -*-
"""takeover_repo 真库测试：哈希存证/TTL 钳制/过期吊销失效/三元组越权拒绝。"""
import os, sys, uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import takeover_repo  # noqa: E402


@pytest.fixture
def takeover_ids(db_conn):
    uid = str(uuid.uuid4())
    bid, sid = f'bt_{uuid.uuid4().hex[:12]}', f'sess_{uuid.uuid4().hex[:12]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s,%s,'x','tk user','developer')", (uid, f'tk_{uid[:8]}'))
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total, done, failed) "
            "VALUES (%s,%s,'接管E2E','p','running',1,0,0)", (bid, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status, batch_id, batch_seq) "
            "VALUES (%s,%s,'子任务1','paused',%s,1)", (sid, uid, bid))
    db_conn.commit()
    yield {'uid': uid, 'bid': bid, 'sid': sid}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM batch_takeover_tokens WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    db_conn.commit()


def test_create_and_resolve_roundtrip(db_conn, takeover_ids):
    token, expires = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    assert token.startswith('tk_')
    row = takeover_repo.resolve_takeover_token(token)
    assert row and row['session_id'] == takeover_ids['sid']
    assert row['batch_name'] == '接管E2E' and row['child_status'] == 'paused'
    # 库中只存哈希
    with db_conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM batch_takeover_tokens WHERE session_id=%s",
                    (takeover_ids['sid'],))
        (h,) = cur.fetchone()
    assert token not in h and len(h) == 64


def test_expired_or_revoked_or_bogus_token_returns_none(db_conn, takeover_ids):
    token, _ = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    with db_conn.cursor() as cur:  # 手动改过期
        cur.execute("UPDATE batch_takeover_tokens SET expires_at = NOW() - interval '1 minute' "
                    "WHERE session_id=%s", (takeover_ids['sid'],))
    db_conn.commit()
    assert takeover_repo.resolve_takeover_token(token) is None

    # 吊销：先 resolve 拿 token_id，再按 id 吊销（三元组匹配才生效）
    token2, _ = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    token_id = takeover_repo.resolve_takeover_token(token2)['token_id']
    # 他人三元组吊销不命中
    assert takeover_repo.revoke_takeover_token('other-user', takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is False
    assert takeover_repo.resolve_takeover_token(token2) is not None
    # 属主吊销命中 → 立即失效
    assert takeover_repo.revoke_takeover_token(takeover_ids['uid'], takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is True
    assert takeover_repo.resolve_takeover_token(token2) is None
    # 重复吊销（已吊销）返回 False
    assert takeover_repo.revoke_takeover_token(takeover_ids['uid'], takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is False
    assert takeover_repo.resolve_takeover_token('tk_bogus') is None
    assert takeover_repo.resolve_takeover_token(None) is None


def test_ttl_clamped(db_conn, takeover_ids):
    _, exp = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1', ttl_hours=9999)
    assert exp - datetime.now(timezone.utc) <= timedelta(days=7, minutes=1)
