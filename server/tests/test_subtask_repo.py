"""server/utils/subtask_repo.py 的测试（真实 DB）。"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402


def test_tables_exist_with_expected_columns():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'ai_chat_subtasks' ORDER BY column_name
            """)
            cols = {r[0] for r in cur.fetchall()}
    assert cols == {'id', 'root_session_id', 'parent_subtask_id', 'parent_part_id',
                    'agent', 'prompt', 'description', 'status', 'error_message',
                    'created_at', 'completed_at'}


def test_subtask_messages_table_has_seq_bigserial():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = 'ai_chat_subtask_messages' AND column_name = 'seq'
            """)
            row = cur.fetchone()
    assert row is not None
    assert row[1] == 'bigint'   # BIGSERIAL 底层类型


def test_deleting_root_session_cascades_to_subtasks_and_messages():
    """外键 ON DELETE CASCADE：删顶层会话要连带清掉子代理数据，不留孤儿。"""
    uid = 'u-subtask-' + uuid.uuid4().hex[:8]
    sid = 's-subtask-' + uuid.uuid4().hex[:8]
    subid = 'ses_subtask_' + uuid.uuid4().hex[:12]
    msg_id = 'm-subtask-' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description) "
                        "VALUES (%s,%s,'build','测试子代理')", (subid, sid))
            cur.execute("INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s,%s,'assistant','[]'::jsonb)", (msg_id, subid))
        conn.commit()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        conn.commit()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_chat_subtasks WHERE id = %s", (subid,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM ai_chat_subtask_messages WHERE id = %s", (msg_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()


def test_subtask_tables_are_in_backup_table_map():
    """本仓库已经因为"忘了把新表加进备份清单"栽过三次（row_actions、seq、meta）。
    这条测试把它钉死——新表必须出现在 BACKUP_TABLE_MAP 与 RESTORE_ORDER 里。"""
    from utils.backup import BACKUP_TABLE_MAP, RESTORE_ORDER
    assert 'ai_chat_subtasks' in BACKUP_TABLE_MAP
    assert 'ai_chat_subtask_messages' in BACKUP_TABLE_MAP
    assert 'ai_chat_subtasks' in RESTORE_ORDER
    assert 'ai_chat_subtask_messages' in RESTORE_ORDER
    # 依赖顺序：subtasks 早于 subtask_messages，且都晚于 ai_chat_sessions
    assert (RESTORE_ORDER.index('ai_chat_sessions')
            < RESTORE_ORDER.index('ai_chat_subtasks')
            < RESTORE_ORDER.index('ai_chat_subtask_messages'))
