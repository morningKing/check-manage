"""admin_get_child_session 跨用户轻查：含 workspace_path 与 ownerUserId。

校验 session 属于 batch；不存在的 batch/session 返 None；workspace_path 可为 NULL
（子会话归档/清理后），调用方据此区分 files 端点返空数组 vs preview/download/import
返 400 NO_WORKSPACE。

真 Postgres 集成测试（不 mock），模式同 test_ai_batch_admin_repo.py：用 db_conn 夹具
+ uuid hex 后缀的唯一 id + try/finally 收尾，避免污染共享开发库。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                       # noqa: E402
from utils.batch_repo import admin_get_child_session  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具：owner + batch-A + 两条 child（一条带 workspace_path、一条 NULL）
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_pair():
    """插入 owner + batch-A + sess-1（带 workspace_path）+ sess-2（NULL workspace）。

    Users 表真实 schema：(id, username, password_hash, display_name, role) — brief
    误写 'password' 列；ai_chat_batches.prompt NOT NULL — brief INSERT 缺它。两处
    偏差均按真实 schema 修正（spec loser rule: schema 是真源）。

    收尾把造的行全删掉 —— 共享开发库，留下 id 会污染后续断言。
    """
    uid = 't3-owner-' + uuid.uuid4().hex[:8]
    bid_a = 't3-batchA-' + uuid.uuid4().hex[:8]
    sid1 = 't3-sess1-' + uuid.uuid4().hex[:8]
    sid2 = 't3-sess2-' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role) "
                "VALUES (%s, %s, 'x', %s, 'developer')",
                (uid, 'owa-' + uid, 'Owner ' + uid),
            )
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, status, total, done, failed) "
                "VALUES (%s, %s, '测试批', 'p', 'completed', 2, 2, 0)",
                (bid_a, uid),
            )
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, "
                "   batch_input_file, workspace_path) "
                "VALUES (%s, %s, 'completed', %s, 1, 'in/foo.txt', '/tmp/ws1'),"
                "       (%s, %s, 'completed', %s, 2, 'in/bar.txt', NULL)",
                (sid1, uid, bid_a, sid2, uid, bid_a),
            )
        conn.commit()
    yield {'batch_id': bid_a, 'uid': uid, 'sid1': sid1, 'sid2': sid2}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id IN (%s, %s)",
                        (sid1, sid2))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id IN (%s, %s)",
                        (sid1, sid2))
            cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid_a,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_admin_get_child_session_returns_columns(seeded_pair):
    """基本 happy path：返回四键 {id, status, workspace_path, ownerUserId}。"""
    f = seeded_pair
    r = admin_get_child_session(f['batch_id'], f['sid1'])
    assert r is not None
    assert r['id'] == f['sid1']
    assert r['status'] == 'completed'
    assert r['workspace_path'] == '/tmp/ws1'
    assert r['ownerUserId'] == f['uid']


def test_admin_get_child_session_null_workspace(seeded_pair):
    """workspace_path 可为 NULL —— caller 据此返 NO_WORKSPACE 而非空数组。"""
    f = seeded_pair
    r = admin_get_child_session(f['batch_id'], f['sid2'])
    assert r is not None
    assert r['workspace_path'] is None


def test_admin_get_child_session_wrong_batch_returns_none(seeded_pair):
    """session 存在但不属于传入的 batch_id —— 必须返 None。

    新插一对 batch-B + sess-3（同 owner，避免污染 fixture），断言：
      * sess-3 不属于 batch-A → None
      * sess-1 不属于 batch-B → None
    """
    f = seeded_pair
    bid_b = 't3-batchB-' + uuid.uuid4().hex[:8]
    sid3 = 't3-sess3-' + uuid.uuid4().hex[:8]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_chat_batches "
                    "  (id, user_id, name, prompt, status, total, done, failed) "
                    "VALUES (%s, %s, '测试批2', 'p', 'completed', 0, 0, 0)",
                    (bid_b, f['uid']),
                )
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq) "
                    "VALUES (%s, %s, 'completed', %s, 1)",
                    (sid3, f['uid'], bid_b),
                )
            conn.commit()
        assert admin_get_child_session(f['batch_id'], sid3) is None
        assert admin_get_child_session(bid_b, f['sid1']) is None
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid3,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid3,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid_b,))
            conn.commit()


def test_admin_get_child_session_nonexistent_batch(seeded_pair):
    """batch_id 不存在 → 返 None。"""
    f = seeded_pair
    assert admin_get_child_session('batch-XXX', f['sid1']) is None


def test_admin_get_child_session_nonexistent_session(seeded_pair):
    """session_id 不存在 → 返 None。"""
    f = seeded_pair
    assert admin_get_child_session(f['batch_id'], 'sess-XXX') is None