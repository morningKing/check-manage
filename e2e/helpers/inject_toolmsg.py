"""E2E helper: 注入一条带 tool_use 的 assistant 消息（确定性渲染工具气泡）。

用法:
  python inject_toolmsg.py seed <title>   # 输出 session_id
  python inject_toolmsg.py cleanup <title>
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'server'))


def seed(title: str) -> str:
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    sid = 'sess_' + uuid.uuid4().hex[:12]
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, title, status, "
                " last_active_at) VALUES (%s,'user-admin',%s,'completed', NOW())",
                (sid, title))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s,%s,'user',%s)",
                ('m_' + uuid.uuid4().hex[:10], sid, json.dumps(
                    [{'type': 'text', 'text': '把 hello-e2e 写入文件'}])))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
                "VALUES (%s,%s,'assistant',%s,%s)",
                ('m_' + uuid.uuid4().hex[:10], sid, json.dumps([
                    {'type': 'tool_use', 'name': 'write', 'status': 'completed',
                     'input': {'file_path': 'e2e-tool.txt', 'content': 'hello-e2e'},
                     'result': 'Wrote file e2e-tool.txt', 'durationMs': 106},
                    {'type': 'text', 'text': '已写入文件 e2e-tool.txt。'},
                ]), json.dumps({'durationMs': 1500, 'tokensInput': 800,
                                'tokensOutput': 60, 'cost': 0.0012})))
        conn.commit()
    finally:
        conn.close()
    return sid


def cleanup(title_like: str) -> int:
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM ai_chat_sessions WHERE title LIKE %s",
                        (title_like + '%',))
            sids = [r[0] for r in cur.fetchall()]
            for sid in sids:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        conn.commit()
        return len(sids)
    finally:
        conn.close()


def main() -> int:
    cmd, title = sys.argv[1], sys.argv[2]
    if cmd == 'seed':
        print(seed(title))
        return 0
    if cmd == 'cleanup':
        print(cleanup(title))
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
