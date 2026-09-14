"""e2e 辅助：向指定会话注入/清理一条带 `error` part 的 assistant 消息。

用于测试「回合失败也要有错误提示」的前端渲染链路（.msg__turn-error）。
真实触发 session.error 需要上游 provider 出错，无法在 e2e 里确定性复现，
所以直接落库一条持久化后的消息（与 chat_persist.persist_turn 同构），
验证渲染层；事件→持久化的服务端逻辑由 tests/test_chat_persist.py 覆盖。

用法:
  python inject_error.py insert <session_id> <msg_id> <text>
  python inject_error.py cleanup <session_id> <msg_id>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'server'))


def main() -> int:
    cmd = sys.argv[1]
    session_id, msg_id = sys.argv[2], sys.argv[3]
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            if cmd == 'insert':
                text = sys.argv[4]
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    (msg_id, session_id,
                     json.dumps([{'type': 'error', 'text': text}])),
                )
            elif cmd == 'cleanup':
                cur.execute(
                    "DELETE FROM ai_chat_messages WHERE id = %s AND session_id = %s",
                    (msg_id, session_id),
                )
            else:
                print(f'unknown cmd: {cmd}')
                return 2
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
