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


def _seed_subtask_with_messages(owner_uid, other_uid, tied_created_at=True):
    """造一个属于 owner_uid 的顶层会话 + 一个子代理 + 若干消息。返回
    (root_session_id, subtask_id, message_ids_in_seq_order)。

    tied_created_at=True 时给三条消息一个与插入顺序**故意相反**的 created_at
    （第一条插入的给最新时间戳，最后一条给最老时间戳）——不是"created_at 全同"。
    全同的写法已经在本仓库栽过三次以上（批任务消息、管理员子任务消息）：改错
    排序列后，Postgres 对并列值的 tie-break 可能恰好复现 seq 顺序，测试"恰好"
    保持绿色，看不出实现被改错，只有用 `PGOPTIONS` 关掉 index/bitmap scan 才能
    现红，日常开发和 CI 都不会这么跑。三条严格递减/递增、互不相等的时间戳完全
    避开 tie-break，让"排序列被改成 created_at"这个变异在默认命令下 100% 变红
    （比照 test_ai_batch_admin_repo.py 的 `batch_with_seq_created_at_reversed`）。
    """
    import json
    sid = 's-subtask-' + uuid.uuid4().hex[:8]
    subid = 'ses_subtask_' + uuid.uuid4().hex[:12]
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid in (owner_uid, other_uid):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer') ON CONFLICT (id) DO NOTHING",
                            (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, owner_uid, 'tok-' + uuid.uuid4().hex))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description) "
                        "VALUES (%s,%s,'build','测试子代理')", (subid, sid))
            mids = []
            # i=0 插入最早（seq 最小）但给最新的 created_at；i=2 插入最晚
            # （seq 最大）但给最老的 created_at —— 与插入顺序完全相反、互不相等。
            reversed_created_at = (
                '2026-01-01 00:00:03+08',
                '2026-01-01 00:00:02+08',
                '2026-01-01 00:00:01+08',
            )
            for i, text in enumerate(('第三条', '第一条', '第二条')):
                mid = f'm-{subid}-{i}'
                mids.append(mid)
                if tied_created_at:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content, created_at) "
                        "VALUES (%s,%s,'assistant',%s::jsonb, %s::timestamptz)",
                        (mid, subid, json.dumps([{'type': 'text', 'text': text}]),
                         reversed_created_at[i]))
                else:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s::jsonb)",
                        (mid, subid, json.dumps([{'type': 'text', 'text': text}])))
        conn.commit()
    return sid, subid, mids


def _cleanup_subtask_seed(owner_uid, other_uid, sid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_uid, other_uid))
        conn.commit()


def test_get_subtask_messages_ordered_by_seq_not_created_at():
    """三条消息的 created_at 与插入顺序（= seq 顺序）故意完全相反，必须按 seq
    返回，而不是按 created_at。若实现误按 created_at 排序，结果会是
    ['第二条','第一条','第三条']（完全颠倒）而不是插入顺序——两者互不相等、
    不吃任何 tie-break，改错排序列在默认命令下 100% 变红。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other, tied_created_at=True)
    try:
        res = get_subtask_messages(subid)
        texts = [p['text'] for m in res['messages'] for p in m['content']
                if p.get('type') == 'text']
        assert texts == ['第三条', '第一条', '第二条']   # 插入顺序 = seq 顺序
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_truncates_to_limit():
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        res = get_subtask_messages(subid, limit=2)
        assert res['truncated'] is True
        assert res['total'] == 3
        assert len(res['messages']) == 2
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_owner_scoped_rejects_wrong_user():
    """实时聊天路径必须校验归属；传错 owner_user_id 一律 None（404），不泄漏存在性。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        assert get_subtask_messages(subid, owner_user_id=other) is None
        assert get_subtask_messages(subid, owner_user_id=owner) is not None
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_unscoped_for_admin_path():
    """owner_user_id=None（管理员路径）不做归属校验——鉴权已经在路由层的
    require_permission('admin.ai_chat_admin') 做过了。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        assert get_subtask_messages(subid) is not None
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_missing_subtask_is_none():
    from utils.subtask_repo import get_subtask_messages
    assert get_subtask_messages('ses_does_not_exist') is None


def test_get_subtask_messages_returns_subtask_summary():
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        res = get_subtask_messages(subid)
        assert res['subtask']['id'] == subid
        assert res['subtask']['agent'] == 'build'
        assert res['subtask']['description'] == '测试子代理'
        assert res['subtask']['status'] == 'running'
    finally:
        _cleanup_subtask_seed(owner, other, sid)
