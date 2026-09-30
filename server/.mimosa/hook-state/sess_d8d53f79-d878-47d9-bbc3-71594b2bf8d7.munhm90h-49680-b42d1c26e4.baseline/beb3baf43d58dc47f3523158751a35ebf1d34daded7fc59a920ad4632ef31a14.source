"""GET /ai/chat/sessions/<sid>/tool-calls 测试（会话工具调用时间线）。

真实 dev 库播种：账本行覆盖 根会话 + 子代理（tree 语义），验证
聚合排序、tool 过滤、归属 404。
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
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_tc_{uuid.uuid4().hex[:10]}'
    oc = f'oc_tc_{uuid.uuid4().hex[:8]}'
    sub_oc = f'oc_sub_{uuid.uuid4().hex[:8]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-owner', 'mcp_owner', 'x', 'MCP Owner', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-other-x', 'other_tc_x', 'x', 'Other', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, opencode_session_id, status) "
        "VALUES (%s, 'user-owner', 'toolcalls-test', %s, 'active')", (sid, oc))
    # 子代理行（agent_tool_calls.subtask_id 的外键目标）
    cur.execute(
        "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description) "
        "VALUES (%s, %s, 'general', '复现用子代理')", (sub_oc, sid))
    rows = [
        # (oc, subtask, agent, part, tool, args, state)
        (oc, None, None, 'p1', 'bash', 'cmd=git clone https://acme/x', 'completed'),
        (sub_oc, sub_oc, 'general', 'p2', 'bash', 'cmd=make test', 'completed'),
        (sub_oc, sub_oc, 'general', 'p3', 'read', 'path=/src/a.py', 'running'),
        (oc, None, None, 'p4', 'task', 'subagent_type=general', 'completed'),
    ]
    for i, (ocs, sub, agent, pid, tool, args, st) in enumerate(rows):
        cur.execute(
            "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, subtask_id, "
            "  agent, part_id, tool, args_text, state, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW() - INTERVAL '%s seconds')",
            (ocs, sid, sub, agent, f'{sid}:{pid}', tool, args, st, i))
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, opencode_session_id, status) "
        "VALUES (%s, 'user-other-x', 'other', 'oc_other_x', 'active')", (f'sess_tc_other_{uuid.uuid4().hex[:6]}',))
    conn.commit()
    yield {'sid': sid, 'oc': oc, 'subOc': sub_oc}
    cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_subtasks WHERE root_session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id LIKE 'sess_tc_%'")
    cur.execute("DELETE FROM users WHERE id = 'user-other-x'")
    conn.commit()
    conn.close()


def test_tool_calls_tree_aggregation(seeded):
    """根 + 子代理的调用全部返回，按时间正序；子代理行带 agent 名。"""
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/tool-calls", headers=_h())
    assert r.status_code == 200
    data = r.get_json()
    assert data['total'] == 4
    # 播种时间偏移 i 秒前（i 越大越早）→ 时间正序 = p4(task)→p1(bash)
    assert [c['tool'] for c in data['calls']] == ['task', 'read', 'bash', 'bash']
    sub_rows = [c for c in data['calls'] if c['agent']]
    assert {c['agent'] for c in sub_rows} == {'general'}
    assert all(c['subtaskId'] == seeded['subOc'] for c in sub_rows)
    # args 摘要带预览（最老一行 = p1 bash git clone）
    assert 'git clone' in data['calls'][-1]['argsPreview']


def test_tool_calls_filter_by_tool(seeded):
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/tool-calls?tool=read", headers=_h())
    data = r.get_json()
    assert data['total'] == 1
    assert data['calls'][0]['tool'] == 'read'


def test_tool_calls_session_not_found_or_foreign(seeded):
    c = _client()
    assert c.get('/ai/chat/sessions/sess_tc_missing/tool-calls',
                 headers=_h()).status_code == 404
    other = {'Authorization': 'Bearer ' + create_token(
        {'id': 'user-other-x', 'username': 'otherx', 'role': 'developer'})}
    # 他人会话：归属不符 → 404（不泄漏存在性）
    r = c.get(f"/ai/chat/sessions/{seeded['sid']}/tool-calls", headers=other)
    assert r.status_code == 404
