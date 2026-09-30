"""Regression test: get_batch_results() must deterministically pick the LAST
assistant message when multiple assistant rows in the same session share the
exact same `created_at`.

Why this can actually happen: `batch_engine._persist_conversation` loops and
INSERTs one row per OpenCode message part *inside a single transaction* (one
`with conn.cursor()` block, one `conn.commit()` at the end). PostgreSQL's
`now()` / column `DEFAULT NOW()` is a **transaction-level constant** — every
row inserted in that transaction gets the identical timestamp. So
`ORDER BY created_at DESC LIMIT 1` has no way to break the tie and Postgres is
free to return whichever tied row it likes — which may be an early/preamble
message rather than the true final answer.

This is a real Postgres integration test (no mocking) — same pattern as
`test_batch_engine.py`'s `user_id`/`db_conn` fixtures — because the bug is a
property of how Postgres breaks (or fails to break) ties, which a mocked
cursor can't exercise.
"""
import os
import sys
import json
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.batch_repo as repo


@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its id, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'bro_user_{uid[:8]}', 'x', f'BRO User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ai_chat_messages WHERE session_id IN "
            "  (SELECT id FROM ai_chat_sessions WHERE user_id = %s)", (uid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_get_batch_results_picks_last_inserted_assistant_message_when_created_at_ties(
    db_conn, user_id,
):
    """两条 assistant 消息 created_at 完全相同（显式字面量，忠实还原"同一事务内
    now() 是事务级常量"的效果），seq 不同、按自然插入顺序递增。get_batch_results
    必须取 seq 更大（后插入、真正的最终答案）那条，不能被 created_at 打平后由
    Postgres 任意决定返回哪条（可能是先插入的铺垫文字）。"""
    bid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'ordering-test', 'p', 1)",
            (bid, user_id),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
            "VALUES (%s, %s, 'completed', %s, 0, 'batch-staging/u/s/a.pdf')",
            (sid, user_id, bid),
        )
        tied_ts = '2026-01-01 00:00:00+00'
        # 先插入"铺垫文字"（模拟 batch_engine 循环里较早的一条 assistant part）。
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, created_at) "
            "VALUES (%s, %s, 'assistant', %s::jsonb, %s)",
            (f'{sid}:a:0', sid,
             json.dumps([{'type': 'text', 'text': '铺垫文字，不是最终答案'}]), tied_ts),
        )
        # 后插入"真正的最终答案"（循环里最后一条 assistant part），created_at 相同。
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, created_at) "
            "VALUES (%s, %s, 'assistant', %s::jsonb, %s)",
            (f'{sid}:a:1', sid,
             json.dumps([{'type': 'text', 'text': '真正的最终答案'}]), tied_ts),
        )
    db_conn.commit()

    try:
        results = repo.get_batch_results(bid)
        assert len(results) == 1
        assert results[0]['output'] == '真正的最终答案', (
            f"expected the last-inserted (higher seq) message, got: {results[0]['output']!r}"
        )
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
        db_conn.commit()
