"""ai_chat_sessions.permission_mode 列迁移 + 会话 bypass 开关测试。"""
import sys, os, uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_permission_mode_column_exists():
    """迁移幂等 + 新会话默认 'ask'。"""
    import importlib, psycopg2
    from config import DB_CONFIG
    mig = importlib.import_module('migrations.2026_10_31_session_permission_mode')
    mig.run()
    mig.run()  # 幂等可重跑
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name='ai_chat_sessions' AND column_name='permission_mode'")
    name, default = cur.fetchone()
    assert name == 'permission_mode' and "'ask'" in (default or '')
    sid = f'sess_pm_{uuid.uuid4().hex[:12]}'
    cur.execute("INSERT INTO ai_chat_sessions (id, user_id, title, status) "
                "VALUES (%s, 'user-owner', 'pm-test', 'active')", (sid,))
    conn.commit()
    cur.execute("SELECT permission_mode FROM ai_chat_sessions WHERE id=%s", (sid,))
    assert cur.fetchone()[0] == 'ask'
    cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
    conn.commit()
    conn.close()
