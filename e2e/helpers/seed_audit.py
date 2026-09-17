"""E2E helper: seed an audited execution for the 执行审计 drawer.

Creates (idempotent, unique title):
  - ai_chat_sessions row (title 'audit-seed-<ts>', owner = admin)
  - user + assistant messages: read 成功 → (无 run_python) → write 成功
    （即 validate_schema / save_result 只有 Todo 声称完成、无工具证据）
  - a todowrite tool_use claiming all three steps completed
  - ai_execution_attempts (completed) + skill manifest 'demo-skill'
  - ai_execution_contracts: explicit 3-step contract for 'demo-skill'

用法:
  python seed_audit.py seed <title>     # 返回 session_id
  python seed_audit.py cleanup <title>  # 按 title 清理
"""

import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'server'))

CONTRACT = {
    'steps': [
        {'id': 'read_input', 'name': '读取输入文件', 'required': True,
         'depends_on': [], 'expected_tools': ['read']},
        {'id': 'validate_schema', 'name': '校验字段结构', 'required': True,
         'depends_on': ['read_input'], 'expected_tools': ['run_python']},
        {'id': 'save_result', 'name': '保存结果文件', 'required': True,
         'depends_on': ['validate_schema'], 'expected_tools': ['save_artifact']},
    ],
    'forbidden_tools': [],
    'success_conditions': [],
}


def seed(title: str) -> str:
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    sid = 'sess_' + uuid.uuid4().hex[:12]
    uid = 'user-admin'
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, title, status, "
                " last_active_at) VALUES (%s,%s,%s,'completed', NOW())",
                (sid, uid, title))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s,%s,'user',%s)",
                ('m_' + uuid.uuid4().hex[:10], sid, json.dumps(
                    [{'type': 'text', 'text': '处理数据'}])))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
                "VALUES (%s,%s,'assistant',%s,%s)",
                ('m_' + uuid.uuid4().hex[:10], sid, json.dumps([
                    {'type': 'tool_use', 'name': 'read', 'status': 'completed',
                     'input': {'file_path': 'uploads/a.csv'},
                     'result': 'col1,col2\n1,2', 'durationMs': 120},
                    {'type': 'tool_use', 'name': 'write', 'status': 'completed',
                     'input': {'file_path': 'out.md'}, 'result': 'written',
                     'durationMs': 80},
                    {'type': 'tool_use', 'name': 'todowrite', 'status': 'completed',
                     'input': {'todos': [
                         {'id': '1', 'content': 'read_input', 'status': 'completed'},
                         {'id': '3', 'content': 'save_result', 'status': 'completed'},
                     ]}, 'result': 'ok', 'durationMs': 5},
                ]), json.dumps({'durationMs': 8000, 'tokensInput': 900,
                                'tokensOutput': 120, 'cost': 0.0021})))
            attempt_id = 'att_' + uuid.uuid4().hex[:10]
            cur.execute(
                "INSERT INTO ai_execution_attempts "
                "(id, session_id, source_type, attempt_no, operation, "
                " requested_agent, effective_agent, agent_resolution, "
                " effective_model, model_resolution, raw_prompt_hash, "
                " effective_prompt_hash, effective_prompt_len, status, "
                " started_at, finished_at) "
                "VALUES (%s,%s,'interactive',1,'send',NULL,'build',"
                "'runtime_default','prov/model-x','session_default',%s,%s,9,"
                "'completed', NOW() - INTERVAL '1 minute', NOW())",
                (attempt_id, sid,
                 hashlib.sha256(b'q').hexdigest(),
                 hashlib.sha256(b'aug q').hexdigest()))
            cur.execute(
                "INSERT INTO ai_execution_manifests "
                "(id, attempt_id, kind, name, source, injected, injection_status, "
                " selected) VALUES (%s,%s,'skill','demo-skill','session',TRUE,"
                "'success','requested')",
                ('man_' + uuid.uuid4().hex[:10], attempt_id))
            cur.execute(
                "INSERT INTO ai_execution_contracts "
                "(id, name, owner_type, owner_id, source, schema_version, "
                " contract, confidence, active) "
                "VALUES (%s,'demo-skill','skill','demo-skill','explicit','v1',"
                "%s, 1.0, TRUE) "
                "ON CONFLICT DO NOTHING",
                ('con_' + uuid.uuid4().hex[:10],
                 json.dumps(CONTRACT, ensure_ascii=False)))
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
    cmd = sys.argv[1]
    title = sys.argv[2]
    if cmd == 'seed':
        print(seed(title))
        return 0
    if cmd == 'cleanup':
        print(cleanup(title))
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
