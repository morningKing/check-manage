# -*- coding: utf-8 -*-
"""subagent skill 调用采集修复（spec §5c）：OC 事件里 subagent part 的
sessionID 是子代理自己的 id，_platform_session_id 必须回退 ai_chat_subtasks
映射到根会话，并以 subtask_id 标注归属。"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_platform_session_id_falls_back_to_subtask(db_conn):
    import uuid as _uuid
    from db import get_db
    from utils.skillopt import _platform_session_id
    uid, bid = str(_uuid.uuid4()), str(_uuid.uuid4())
    root, sub_oc = str(_uuid.uuid4()), f'ses_sub{_uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'SC', 'developer')", (uid, f'sc_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'c', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, opencode_session_id, session_token) "
                        "VALUES (%s, %s, 'running', %s, 0, 'oc_parent_x', %s)",
                        (root, uid, bid, f'tok-{root[:12]}'))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
                        "VALUES (%s, %s, 'general', 'completed')", (sub_oc, root))
    db_conn.commit()
    try:
        assert _platform_session_id('oc_parent_x') == root     # 既有路径
        assert _platform_session_id(sub_oc) == root            # 修复：子代理回退
        assert _platform_session_id('ses_unknown') == ''       # 均无 → 空
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_subtasks WHERE id = %s", (sub_oc,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        db_conn.commit()


def test_runtime_event_from_subagent_records_subtask(db_conn):
    """subagent 的 skill 上报：归属根会话 + subtask_id 标注 + outcome 收口。"""
    import uuid as _uuid
    from db import get_db
    from utils.skillopt import record_runtime_skill_event
    uid, bid = str(_uuid.uuid4()), str(_uuid.uuid4())
    root, sub_oc = str(_uuid.uuid4()), f'ses_sub{_uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'SC', 'developer')", (uid, f'sc_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'c', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, opencode_session_id, session_token) "
                        "VALUES (%s, %s, 'running', %s, 0, %s, %s)",
                        (root, uid, bid, sub_oc, f'tok-{root[:12]}'))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
                        "VALUES (%s, %s, 'general', 'completed')", (sub_oc, root))
    db_conn.commit()
    try:
        r = record_runtime_skill_event({
            'skillName': 'data-pull', 'sessionID': sub_oc,
            'messageID': 'msg_1', 'partID': 'prt_1', 'status': 'completed',
            'title': 'Loaded skill: data-pull'})
        assert r['ok'] is True and r.get('subtaskId') == sub_oc
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_id, subtask_id, evidence_level "
                            "FROM ai_skill_invocations WHERE skill_name='data-pull'")
                row = cur.fetchone()
        assert row and row[0] == root and row[1] == sub_oc and row[2] == 'confirmed'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_invocations WHERE session_id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_subtasks WHERE id = %s", (sub_oc,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        db_conn.commit()
