"""GET /ai/chat/sessions/<sid>/turns 会话大纲端点测试（spec 2026-10-25-session-outline §4.1）。

用户回合投影：id/seq/createdAt/preview；空壳用户消息（无文本无文件）跳过；
assistant 消息不进大纲。真实库播种，模式对齐 test_ai_chat_messages_paging.py。
"""
import sys
import os
import json
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
        {'id': 'user-owner', 'username': 'turns_owner', 'role': 'developer'})}


@pytest.fixture
def seeded():
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_turns_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-owner', 'turns_owner', 'x', 'T', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-owner', 'turns-test', 'active')", (sid,))
    rows = [
        ('msg_t1', 'user', [{"type": "text", "text": "第一问"}], 1),
        ('msg_t2', 'assistant', [{"type": "text", "text": "回答"}], 2),
        ('msg_t3', 'user', [
            {"type": "file", "name": "a.xlsx", "path": "uploads/a.xlsx"},
            {"type": "file", "name": "b.csv", "path": "uploads/b.csv"}], 3),
        ('msg_t4', 'user', [], 4),                                # 空壳：跳过
        ('msg_t5', 'user', [{"type": "text", "text": "   "}], 5),  # 空白文本：跳过
        ('msg_t6', 'user', [{"type": "text", "text": "长" * 200}], 6),
    ]
    for mid, role, content, seq in rows:
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
            "VALUES (%s, %s, %s, %s, %s)",
            (mid, sid, role, json.dumps(content, ensure_ascii=False), seq))
    conn.commit()
    yield {'sid': sid}
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_turns_projection_shape_and_order(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    assert r.status_code == 200
    turns = r.get_json()['turns']
    # 只含用户回合：t1/t3/t6（t4 空壳、t5 空白文本被跳过，assistant t2 排除）
    assert [t['id'] for t in turns] == ['msg_t1', 'msg_t3', 'msg_t6']
    assert [t['seq'] for t in turns] == [1, 3, 6]
    assert turns[0]['preview'] == '第一问'
    assert turns[0]['createdAt']  # 有值即可（iso 字符串）


def test_turns_file_only_preview(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    turns = r.get_json()['turns']
    assert turns[1]['preview'] == '📎 a.xlsx、b.csv'


def test_turns_preview_truncated_to_160(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    turns = r.get_json()['turns']
    assert len(turns[2]['preview']) == 160


def test_turns_unknown_session_404():
    r = _client().get("/ai/chat/sessions/sess_nope/turns", headers=_h())
    assert r.status_code == 404


def test_turns_requires_auth():
    r = _client().get("/ai/chat/sessions/whatever/turns")
    assert r.status_code in (401, 403)
