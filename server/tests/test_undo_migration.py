"""undo/redo 迁移：两表一列存在且幂等（spec §2.1）。"""
import importlib.util
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MIG = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                    '2026_11_10_ai_chat_undo_redo.py')


def _load():
    spec = importlib.util.spec_from_file_location('_undo_migration', _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_undo_tables_and_column_exist():
    mod = _load()
    mod.run()
    mod.run()  # 幂等可重跑
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('ai_chat_message_stash', 'ai_chat_session_reverts')")
        assert {r[0] for r in cur.fetchall()} == {
            'ai_chat_message_stash', 'ai_chat_session_reverts'}
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_chat_sessions' "
            "  AND column_name = 'context_replay_pending'")
        assert cur.fetchone()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_chat_message_stash' ORDER BY ordinal_position")
        cols = [r[0] for r in cur.fetchall()]
        assert cols == ['message_id', 'session_id', 'role', 'content', 'meta',
                        'created_at', 'seq', 'rating', 'stashed_at']


def test_stash_rows_survive_message_hard_delete():
    """stash 行不随消息删除丢失（审计口径），但随会话硬删级联。"""
    mod = _load()
    from db import get_db
    sid = f'sess_umig_{uuid.uuid4().hex[:8]}'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            # 规范用户由 conftest bootstrap 播种；直接引用其 id
            cur.execute("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
            uid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                "  session_token, token_expires_at) "
                "VALUES (%s, %s, '/tmp/undo-mig', 'tok-' || %s, NOW() + interval '1 day')",
                (sid, uid, sid))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'user', %s)",
                (sid + ':u', sid, json.dumps([{'type': 'text', 'text': 'x'}])))
            cur.execute(
                "INSERT INTO ai_chat_message_stash (message_id, session_id, role, "
                "  content, created_at, seq) "
                "SELECT id, session_id, role, content, created_at, seq "
                "FROM ai_chat_messages WHERE id = %s", (sid + ':u',))
            cur.execute("DELETE FROM ai_chat_messages WHERE id = %s", (sid + ':u',))
            cur.execute("SELECT count(*) FROM ai_chat_message_stash WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 1
    finally:
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
