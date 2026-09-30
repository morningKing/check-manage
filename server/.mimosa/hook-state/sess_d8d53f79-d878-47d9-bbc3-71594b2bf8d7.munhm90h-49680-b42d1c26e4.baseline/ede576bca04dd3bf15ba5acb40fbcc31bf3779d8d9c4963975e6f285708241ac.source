"""GET /ai/chat/sessions/<sid>/messages 分页（大数据量优化 §1.1）测试。

真实 dev 库播种：初始加载只回最新窗口（limit 截断 + hasMore），
before 游标向前翻页，since 增量保持全量语义。
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
    """真实库播种一个会话 + 25 条消息（seq 递增，id 唯一可排序）。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_pg_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-owner', 'mcp_owner', 'x', 'MCP Owner', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-owner', 'paging-test', 'active')", (sid,))
    ids = []
    for i in range(25):
        mid = f'msg_pg_{i:04d}_{uuid.uuid4().hex[:6]}'
        ids.append(mid)
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
            "VALUES (%s, %s, 'assistant', %s, %s)",
            (mid, sid, f'[{{"type":"text","text":"m{i}"}}]', i))
    conn.commit()
    yield {'sid': sid, 'ids': ids}
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_default_window_limit_and_has_more(seeded):
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10", headers=_h())
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['messages']) == 10
    assert data['hasMore'] is True
    # 窗口是最新的 10 条（m15..m24），且保持时序正序
    texts = [m['content'][0]['text'] for m in data['messages']]
    assert texts == [f'm{i}' for i in range(15, 25)]
    # 窗口第一条 seq = 最老已加载（前端 before 游标）
    assert data['messages'][0]['seq'] == 15


def test_before_cursor_pages_backwards(seeded):
    c = _client()
    first = c.get(f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10",
                  headers=_h()).get_json()
    before = first['messages'][0]['seq']
    second = c.get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10&before={before}",
        headers=_h()).get_json()
    assert [m['content'][0]['text'] for m in second['messages']] == \
        [f'm{i}' for i in range(5, 15)]
    assert second['hasMore'] is True
    # 翻到最老：hasMore 变 False
    third = c.get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10&before={second['messages'][0]['seq']}",
        headers=_h()).get_json()
    assert [m['content'][0]['text'] for m in third['messages']] == \
        [f'm{i}' for i in range(0, 5)]
    assert third['hasMore'] is False


def test_since_keeps_full_increment_semantics(seeded):
    """since 增量通道不受分页影响（轮询语义不变）。"""
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?since={seeded['ids'][20]}",
        headers=_h()).get_json()
    texts = [m['content'][0]['text'] for m in r['messages']]
    assert texts == [f'm{i}' for i in range(21, 25)]
    assert r['hasMore'] is False


def test_session_isolation_unchanged(seeded):
    """他人会话依旧 404（归属校验未被分页改动）。"""
    import requests  # noqa: F401 —— 仅确保导入环境一致
    sid = seeded['sid']
    other = {'Authorization': 'Bearer ' + create_token(
        {'id': 'user-other-x', 'username': 'otherx', 'role': 'developer'})}
    r = _client().get(f"/ai/chat/sessions/{sid}/messages?limit=5", headers=other)
    assert r.status_code == 404
