"""batch_children_status / batch_child_changes 两个 MCP 工具的单元测试。

覆盖：归属校验（他人批任务不可见）、汇总结构、变更文件列表、
参数缺失的领域异常。
"""
import uuid

import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'server'))

from context import ToolContext
from tools import batch_children_status, batch_child_changes


@pytest.fixture
def db_conn():
    """真实 dev 库连接（与 server/tests 同库），每测试独立回滚收尾。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    yield conn
    conn.close()


@pytest.fixture
def ctx():
    return ToolContext(session_id='sess_owner', user_id='user-owner',
                       role='admin')


@pytest.fixture
def seeded(db_conn):
    bid = str(uuid.uuid4())
    cid_ok = str(uuid.uuid4())
    cid_other = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES ('user-owner', 'mcp_owner', 'x', 'MCP Owner', 'developer') "
            "ON CONFLICT (id) DO NOTHING")
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, 'user-owner', 'mcp-test-batch', 'p', 1)", (bid,))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, batch_id, batch_seq, "
            "  status, batch_input_file, opencode_session_id, last_active_at) "
            "VALUES (%s, 'user-owner', %s, 0, 'completed', 'inputs/one.txt', "
            "  'oc-mcp-1', NOW())", (cid_ok, bid))
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s)",
            (f'{cid_ok}:mcp', cid_ok,
             '[{"type":"text","text":"汇总结果：已生成报告"}]'))
        cur.execute(
            "INSERT INTO ai_chat_session_files (session_id, path, status) "
            "VALUES (%s, 'outputs/report.md', 'added')", (cid_ok,))
        # 他人批任务（不同用户）——归属校验对照
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES ('user-other-mcp', 'mcp_other', 'x', 'Other', 'developer') "
            "ON CONFLICT (id) DO NOTHING")
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, 'user-other-mcp', 'other-batch', 'p', 1)",
            (cid_other,))
    db_conn.commit()
    return {'batchId': bid, 'childId': cid_ok,
            'otherBatchId': cid_other, 'otherChildId': cid_other}


def test_children_status_overview(db_conn, ctx, seeded):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s",
                    (seeded['childId'],))
        child_status = cur.fetchone()[0]
    r = batch_children_status.children_overview(seeded['batchId'])
    assert r['batchId'] == seeded['batchId']
    assert r['aggregate']['total'] == 1
    assert len(r['children']) == 1
    c = r['children'][0]
    assert c['childId'] == seeded['childId']
    assert c['status'] == child_status
    assert c['statusZh'] in ('运行中', '已完成', '失败', '待运行', '已暂停')
    # 结果摘要：最后一条 assistant 回复首 300 字
    assert c['resultSummary'] and '汇总结果' in c['resultSummary']


def test_children_status_unknown_batch(ctx):
    with pytest.raises(batch_children_status.BatchChildrenStatusError):
        batch_children_status.handle({'batch_id': str(uuid.uuid4())}, ctx)


def test_child_changes_lists_added_files(db_conn, ctx, seeded):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_session_files (session_id, path, status) "
            "VALUES (%s, 'outputs/r.md', 'added')", (seeded['childId'],))
    db_conn.commit()
    r = batch_child_changes.child_changes(seeded['batchId'], seeded['childId'])
    assert r is not None
    paths = {f['path'] for f in r['files']}
    # fixture 已含 'outputs/report.md'（seed 前置）+ 本测试插入的 r.md
    assert paths == {'outputs/report.md', 'outputs/r.md'}
    added = {f['path'] for f in r['files'] if f['status'] == 'added'}
    assert added == paths


def test_child_changes_mismatched_batch_returns_none(db_conn, ctx, seeded):
    """子任务不属于该批 → None（handle 转「不存在」）。"""
    import uuid as _uuid
    other_bid = str(_uuid.uuid4())
    r = batch_child_changes.child_changes(other_bid, seeded['childId'])
    assert r is None


def test_child_changes_ownership_enforced(db_conn, ctx, seeded):
    """handle 层：批不属于 MCP token 用户 → 领域异常（不泄漏存在性）。"""
    other_bid = seeded['otherBatchId']
    with pytest.raises(batch_child_changes.BatchChildChangesError) as ei:
        batch_child_changes.handle(
            {'batch_id': other_bid, 'session_id': seeded['otherChildId']},
            ctx)
    assert '不存在' in str(ei.value)


def test_children_status_ownership_enforced(db_conn, ctx, seeded):
    """批属他人（MCP token 用户不符）→ handle 报「批任务不存在」。"""
    import uuid as _uuid
    other_bid = str(_uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, 'mcp_other2', 'x', 'Other2', 'developer') "
            "ON CONFLICT (id) DO NOTHING", ('user-other-mcp-2',))
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'other-batch', 'p', 1)", (other_bid, 'user-other-mcp-2'))
    db_conn.commit()
    with pytest.raises(batch_children_status.BatchChildrenStatusError):
        batch_children_status.handle({'batch_id': other_bid}, ctx)


def test_missing_params_raise():
    with pytest.raises(batch_children_status.BatchChildrenStatusError):
        batch_children_status.handle({}, None)
    with pytest.raises(batch_child_changes.BatchChildChangesError):
        batch_child_changes.handle({'batch_id': 'b'}, None)
