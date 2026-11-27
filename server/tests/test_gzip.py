"""JSON 响应 gzip 压缩（app.py::_gzip_json_response）测试。

真实 dev 测试库播种大消息，验证：
- Accept-Encoding: gzip 时大响应压缩（magic bytes + 解压回原 JSON）
- 未声明 gzip 时不压（身份编码）
- 错误响应（>=300）不压——proxy.py 的 HTTPError 路径只透传 Content-Type
- 小响应（<1KB）不压（CPU 换不来几字节）
"""
import gzip as gzip_mod
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(extra=None):
    h = {'Authorization': 'Bearer ' + create_token(
        {'id': 'user-gzip', 'username': 'gzip_owner', 'role': 'developer'})}
    if extra:
        h.update(extra)
    return h


@pytest.fixture
def seeded():
    """真实库播种一个会话 + 一条大消息（~20KB 文本，高压缩比）。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_gz_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-gzip', 'gzip_owner', 'x', 'Gzip Owner', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-gzip', 'gzip-test', 'active')", (sid,))
    big_text = '[{"type":"text","text":"' + '压缩率测试段落。' * 2000 + '"}]'
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
        "VALUES (%s, %s, 'assistant', %s, %s)",
        (f'msg_gz_{uuid.uuid4().hex[:8]}', sid, big_text, 1))
    conn.commit()
    yield {'sid': sid}
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_big_json_compressed_when_accepted(seeded):
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10",
        headers=_h({'Accept-Encoding': 'gzip'}))
    assert r.status_code == 200
    assert r.headers.get('Content-Encoding') == 'gzip'
    assert r.headers.get('Vary') is not None
    # gzip magic bytes + 解压回原 JSON（werkzeug 测试客户端不自动解压）
    assert r.data[:2] == b'\x1f\x8b'
    body = json.loads(gzip_mod.decompress(r.data).decode('utf-8'))
    assert body['messages'][0]['content'][0]['text'].startswith('压缩率测试段落')
    # 压缩是真的（高冗余文本应显著变小）
    assert len(r.data) < len(gzip_mod.decompress(r.data)) // 2


def test_identity_when_not_accepted(seeded):
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/messages?limit=10", headers=_h())
    assert r.status_code == 200
    assert r.headers.get('Content-Encoding') is None
    assert r.get_json()['messages'][0]['content'][0]['text'].startswith('压缩率测试段落')


def test_error_response_not_compressed(seeded):
    # 未认证 → 4xx JSON；错误响应不压（>=300 门），即使声明了 gzip
    r = _client().get(
        f"/ai/chat/sessions/{seeded['sid']}/messages",
        headers={'Accept-Encoding': 'gzip'})
    assert r.status_code >= 300
    assert r.headers.get('Content-Encoding') is None


def test_small_json_not_compressed():
    """独立播种小会话：单条小消息响应 <1KB → 不压缩。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_gz_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-gzip', 'gzip_owner', 'x', 'Gzip Owner', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-gzip', 'gzip-test-small', 'active')", (sid,))
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
        "VALUES (%s, %s, 'assistant', '[{\"type\":\"text\",\"text\":\"m0\"}]', %s)",
        (f'msg_gz_{uuid.uuid4().hex[:8]}', sid, 1))
    conn.commit()
    try:
        r = _client().get(
            f"/ai/chat/sessions/{sid}/messages?limit=10",
            headers=_h({'Accept-Encoding': 'gzip'}))
        assert r.status_code == 200
        # 响应体 <1KB → 不压缩
        assert r.headers.get('Content-Encoding') is None
        assert len(r.get_json()['messages']) == 1
    finally:
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        conn.commit()
        conn.close()
