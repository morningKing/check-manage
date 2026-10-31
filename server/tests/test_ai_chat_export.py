# -*- coding: utf-8 -*-
"""GET /ai/chat/sessions/<sid>/export.md 会话导出（Feature B）测试。"""
import sys, os, uuid, json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes.ai_chat import _render_export_markdown, _export_filename
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
    """真实库播种一个会话 + 助手消息（导出取数落点）。"""
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
    ids = []
    for i in range(3):
        mid = f'msg_fb_{uuid.uuid4().hex[:12]}'
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
            "VALUES (%s, %s, 'assistant', %s, %s)",
            (mid, sid, '[{"type":"text","text":"fb"}]', i))
        ids.append(mid)
    conn.commit()
    yield {'sid': sid, 'ids': ids}
    cur.execute("DELETE FROM ai_chat_message_feedback WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_render_markdown_all_part_types():
    rows = [
        ('user', [{'type': 'text', 'text': '看下 @data/a.csv'},
                  {'type': 'file', 'name': 'a.csv', 'path': 'uploads/a.csv'}]),
        ('assistant', [{'type': 'text', 'text': '好的，见下表'},
                       {'type': 'tool_use', 'name': 'read_csv', 'input': {'path': 'a.csv'}},
                       {'type': 'image', 'name': 'chart.png', 'path': 'uploads/chart.png'},
                       # 三类已持久化 part 不许静默丢（spec B.1）；字段形状取自
                       # 落库点：subtask_use/error 出自 chat_persist._flatten_scope
                       # （opencode_parts.map_part 映射后剥 'time'），run_result
                       # 出自 routes/ai_chat.run_script。
                       {'type': 'subtask_use', 'subtaskId': 'ses_child1', 'agent': 'explore',
                        'description': '查找引用点', 'status': 'completed', 'segmentCount': 2},
                       {'type': 'error', 'text': 'ProviderAuthError: 未配置 API key'},
                       {'type': 'run_result', 'filename': 'script.py', 'exitCode': 1,
                        'timedOut': True, 'stdout': '', 'stderr': 'boom', 'outputFiles': []}]),
    ]
    md = _render_export_markdown('测试会话', rows)
    assert md.startswith('# 测试会话')
    assert '## 🧑 用户' in md and '## 🤖 助手' in md
    assert '看下 @data/a.csv' in md
    assert '<details><summary>🔧 read_csv</summary>' in md
    assert '- 📎 a.csv' in md and '- 🖼️ chart.png' in md
    # subtask_use → 折叠块（agent 名 + 状态进 summary，description 折叠内容）
    assert '<details><summary>🧩 子代理 explore（completed）</summary>' in md
    assert '查找引用点' in md
    # error → 引用行；run_result → 单行（timedOut 追加「超时」）
    assert '> ⚠️ ProviderAuthError: 未配置 API key' in md
    assert '- 🧾 script.py（exit 1，超时）' in md


def test_export_filename_safety():
    fname = _export_filename('a/b:c*d?"<>|')
    assert '\\' not in fname and ':' not in fname
    assert _export_filename('   ').startswith('session-')
    long = _export_filename('很' * 100)
    assert len(long.split('-')[0]) <= 60        # safe 段截 60（时间戳含一个 '-'）


def test_export_route_auth_and_content(seeded):
    c, h = _client(), _h()
    r = c.get(f"/ai/chat/sessions/{seeded['sid']}/export.md", headers=h)
    assert r.status_code == 200
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    # 下载头完整性：mimetype text/markdown + RFC 5987 UTF-8 文件名（前端
    # <a download> 与浏览器命名都吃这两个头）
    assert 'text/markdown' in r.headers.get('Content-Type', '')
    assert "filename*=UTF-8''" in r.headers['Content-Disposition']
    body = r.get_data(as_text=True)
    assert body.startswith('# ')
    # 正文含 seeded 消息文本（fixture 播 3 条 assistant，content parts
    # text='fb'——_render_export_parts 原样落行）
    assert 'fb' in body
    # 越权
    assert c.get("/ai/chat/sessions/other-sess/export.md", headers=h).status_code == 404


def test_export_empty_session_header_only():
    """空会话：只输出 header 块，200 不报错（spec B.1）。"""
    md = _render_export_markdown('空会话', [])
    assert md.startswith('# 空会话')
    assert '消息数：0' in md
