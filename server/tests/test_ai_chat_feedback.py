"""ai_chat_message_feedback 反馈表（第一档差距补齐 Feature A）后端测试。

真实 dev 库播种：迁移幂等建表 + ON DELETE CASCADE 随消息删除。
Feature A 后端测试都放这里——后续 POST 反馈端点与 get_messages
LEFT JOIN 的表契约均依赖本文件的表存在性/级联保证。
"""
import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h():
    return {'Authorization': 'Bearer ' + create_token(
        {'id': 'user-owner', 'username': 'mcp_owner', 'role': 'developer'})}


@pytest.fixture
def seeded(db_conn=None):
    """真实库播种一个会话 + 一条 assistant 消息（反馈落点）。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_fb_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-owner', 'mcp_owner', 'x', 'MCP Owner', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-owner', 'feedback-test', 'active')", (sid,))
    mid = f'msg_fb_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
        "VALUES (%s, %s, 'assistant', %s, %s)",
        (mid, sid, '[{"type":"text","text":"fb"}]', 0))
    conn.commit()
    yield {'sid': sid, 'ids': [mid]}
    cur.execute("DELETE FROM ai_chat_message_feedback WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_feedback_table_and_cascade(seeded):
    """迁移幂等建表 + ON DELETE CASCADE 随消息删除。"""
    import importlib
    mig = importlib.import_module('migrations.2026_10_30_message_feedback')
    mig.run()  # 幂等，可重跑
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    mid = seeded['ids'][0]
    cur.execute("INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
                "VALUES (%s, %s, 1)", (mid, seeded['sid']))
    conn.commit()
    cur.execute("DELETE FROM ai_chat_messages WHERE id = %s", (mid,))
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM ai_chat_message_feedback WHERE message_id = %s", (mid,))
    assert cur.fetchone()[0] == 0
    conn.close()
