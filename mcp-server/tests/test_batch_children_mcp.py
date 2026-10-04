"""batch_children_status / batch_child_changes / batch_children_search
三个 MCP 工具的单元测试。

覆盖：归属校验（他人批任务不可见）、汇总结构、变更文件列表、
条件筛选（状态/门禁/报错/区间/关键词/limit 截断语义）、
参数缺失与非法取值的领域异常。
"""
import uuid

import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'server'))

from context import ToolContext
from tools import batch_children_status, batch_child_changes, batch_children_search


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
            "INSERT INTO ai_chat_sessions (id, user_id, title, batch_id, batch_seq, "
            "  status, batch_input_file, opencode_session_id, "
            "  created_at, last_active_at) "
            "VALUES (%s, 'user-owner', 'mcp-seeded-title', %s, 0, 'completed', "
            "  'inputs/one.txt', 'oc-mcp-1', NOW() - INTERVAL '2 minutes', NOW())",
            (cid_ok, bid))
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
    assert c['title'] == 'mcp-seeded-title'
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


# ---------------------------------------------------------------- search

@pytest.fixture
def seeded_search(db_conn, seeded):
    """在 seeded 批下再补 2 个子任务，构成 3 子可筛选集合：
    c0=completed/passed/无错; c1=failed/failed/有错(beta.log);
    c2=failed/未核对/无错(gamma.txt)。"""
    bid = seeded['batchId']
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, batch_id, batch_seq, "
            "  status, gate_status, error_message, batch_input_file, title, "
            "  opencode_session_id, created_at, last_active_at) "
            "VALUES (%s, 'user-owner', %s, 1, 'failed', 'failed', 'boom', "
            "  'inputs/beta.log', 'beta 会话', 'oc-mcp-2', "
            "  NOW() - INTERVAL '3 minutes', NOW())", (c1, bid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, batch_id, batch_seq, "
            "  status, gate_status, batch_input_file, title, "
            "  opencode_session_id, created_at, last_active_at) "
            "VALUES (%s, 'user-owner', %s, 2, 'failed', NULL, "
            "  'inputs/gamma.txt', 'gamma 会话', 'oc-mcp-3', "
            "  NOW() - INTERVAL '1 minutes', NOW())", (c2, bid))
        cur.execute(
            "UPDATE ai_chat_sessions SET gate_status = 'passed' "
            "WHERE batch_id = %s AND batch_seq = 0", (bid,))
        cur.execute("UPDATE ai_chat_batches SET total = 3 WHERE id = %s", (bid,))
    db_conn.commit()
    seeded['children'] = {'failed_with_error': c1, 'failed_unchecked': c2}
    return seeded


def test_search_filters_by_status(db_conn, ctx, seeded_search):
    r = batch_children_search.handle(
        {'batch_id': seeded_search['batchId'], 'status': ['failed']}, ctx)
    assert r['matched'] == 2
    assert r['totalChildren'] == 3
    assert r['statusCounts'] == {'failed': 2}
    assert [c['seq'] for c in r['children']] == [1, 2]


def test_search_has_error_gate_and_range(db_conn, ctx, seeded_search):
    bid = seeded_search['batchId']
    r = batch_children_search.handle(
        {'batch_id': bid, 'has_error': True}, ctx)
    assert [c['childId'] for c in r['children']] == [
        seeded_search['children']['failed_with_error']]
    r = batch_children_search.handle(
        {'batch_id': bid, 'gate_status': ['unchecked']}, ctx)
    # fixture 已把 c0 设为 passed → unchecked 只剩 c2
    assert [c['seq'] for c in r['children']] == [2]
    r = batch_children_search.handle(
        {'batch_id': bid, 'gate_status': ['passed', 'failed']}, ctx)
    assert r['matched'] == 2
    assert r['gateFailedCount'] == 1
    r = batch_children_search.handle(
        {'batch_id': bid, 'seq_from': 1, 'seq_to': 2}, ctx)
    assert [c['seq'] for c in r['children']] == [1, 2]


def test_search_keyword_literal_wildcards(db_conn, ctx, seeded_search):
    bid = seeded_search['batchId']
    r = batch_children_search.handle({'batch_id': bid, 'keyword': 'beta'}, ctx)
    assert r['matched'] == 1  # c1：beta.log 文件名 + 'beta 会话' 标题同属一个子任务
    assert r['children'][0]['seq'] == 1
    # % 按字面匹配，不当通配符（转义生效的判别断言）
    r = batch_children_search.handle({'batch_id': bid, 'keyword': '%'}, ctx)
    assert r['matched'] == 0


def test_search_matched_not_truncated_by_limit(db_conn, ctx, seeded_search):
    r = batch_children_search.handle(
        {'batch_id': seeded_search['batchId'], 'status': ['failed'],
         'limit': 1}, ctx)
    assert r['matched'] == 2          # 计数对全量筛选集
    assert r['statusCounts'] == {'failed': 2}
    assert len(r['children']) == 1    # 明细被 limit 截断


def test_search_duration_ms_uses_created_at(db_conn, ctx, seeded_search):
    r = batch_children_search.handle(
        {'batch_id': seeded_search['batchId'], 'seq_from': 0, 'seq_to': 0},
        ctx)
    # c0 created_at 比 last_active_at 早 2 分钟 → duration ≈ 120000ms
    assert r['children'][0]['durationMs'] >= 100_000


def test_search_created_window(db_conn, ctx, seeded_search):
    """created_from/created_to 日期窗：今天全天命中全部，昨天区间为空。"""
    from datetime import date, timedelta
    bid = seeded_search['batchId']
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = batch_children_search.handle(
        {'batch_id': bid, 'created_from': today, 'created_to': today}, ctx)
    assert r['matched'] == 3          # fixture 子任务都是 NOW() 创建
    assert r['children'][0]['title'] == 'mcp-seeded-title'
    r = batch_children_search.handle(
        {'batch_id': bid, 'created_from': yesterday,
         'created_to': yesterday}, ctx)
    assert r['matched'] == 0
    # 完整 ISO 时间戳：上界=当前时间 → 全命中
    from datetime import datetime
    now_iso = datetime.now().astimezone().isoformat(timespec='seconds')
    r = batch_children_search.handle(
        {'batch_id': bid, 'created_from': yesterday, 'created_to': now_iso},
        ctx)
    assert r['matched'] == 3
    # 非法格式 → 领域异常
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle(
            {'batch_id': bid, 'created_from': 'not-a-date'}, ctx)


def test_search_invalid_params_raise(ctx, seeded_search):
    bid = seeded_search['batchId']
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle(
            {'batch_id': bid, 'status': ['bogus']}, ctx)
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle(
            {'batch_id': bid, 'gate_status': ['bogus']}, ctx)
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle({'batch_id': bid, 'limit': 9999}, ctx)
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle(
            {'batch_id': bid, 'seq_from': 3, 'seq_to': 1}, ctx)
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle({}, ctx)


def test_search_ownership_enforced(ctx, seeded_search):
    with pytest.raises(batch_children_search.BatchChildrenSearchError):
        batch_children_search.handle(
            {'batch_id': seeded_search['otherBatchId']}, ctx)


def test_children_status_duration_not_always_zero(db_conn, ctx, seeded):
    """回归：durationMs 原来恒为 0（started/finished 同取 last_active_at），
    现用 created_at→last_active_at。"""
    r = batch_children_status.children_overview(seeded['batchId'])
    assert r['children'][0]['durationMs'] >= 100_000
