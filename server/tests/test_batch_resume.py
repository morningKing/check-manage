"""resume_batch / cancel_batch(UI 路由用) 的 DB 集成测试。

「停止后在原来的工作上继续」：
  - 已开跑过的 cancelled 子任务 → pending + continue_prompt（worker 走
    continue 模式在原 OpenCode 会话上续跑）；
  - 排队中被停止的 cancelled 子任务 → pending、不置 continue_prompt（全新执行）；
  - failed / completed 子任务不受影响（各归 retry-failed / reexecute）。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'rs_user_{uid[:8]}', 'x', f'RS User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, n=3):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'resume-test', 'p', %s)",
            (bid, user_id, n),
        )
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, i, f'f{i}.csv'),
            )
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _child(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, continue_prompt, opencode_session_id, error_message, "
            "       cancel_requested FROM ai_chat_sessions WHERE id = %s", (sid,))
        return cur.fetchone()


def _batch(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, done, failed, total FROM ai_chat_batches "
                    "WHERE id = %s", (bid,))
        return cur.fetchone()


def test_resume_batch_continues_started_and_reruns_queued(db_conn, user_id):
    from utils.batch_repo import resume_batch
    bid, sids = bid_sids = _seed_batch(db_conn, user_id, n=3)
    started, queued, done_child = sids
    with db_conn.cursor() as cur:
        # 停止前已开跑：有 oc 会话
        cur.execute("UPDATE ai_chat_sessions SET status='cancelled', "
                    "opencode_session_id='oc_1' WHERE id=%s", (started,))
        # 排队中被停止：没有 oc 会话
        cur.execute("UPDATE ai_chat_sessions SET status='cancelled' "
                    "WHERE id=%s", (queued,))
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s",
                    (done_child,))
        cur.execute("UPDATE ai_chat_batches SET done=1, failed=2, status='partial' "
                    "WHERE id=%s", (bid,))
    db_conn.commit()

    result = resume_batch(user_id, bid)
    assert result is not None

    st, cp, oc, err, cr = _child(db_conn, started)
    assert st == 'pending'
    assert oc == 'oc_1'            # 保留原会话：continue 模式续跑
    assert cp and '继续' in cp     # 注入了续跑提示词
    assert err is None and cr is False

    st, cp, oc, err, cr = _child(db_conn, queued)
    assert st == 'pending'
    assert oc is None              # 从未开跑：不置 continue_prompt
    assert cp is None

    st, *_ = _child(db_conn, done_child)
    assert st == 'completed'       # 已完成的不动

    status, done, failed, total = _batch(db_conn, bid)
    assert failed == 0             # 两个 cancelled 从 failed 计数回滚
    assert done == 1 and total == 3
    assert status == 'running'     # terminal -> running


def test_resume_batch_without_cancelled_raises(db_conn, user_id):
    from utils.batch_repo import resume_batch
    bid, sids = _seed_batch(db_conn, user_id, n=1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='failed' WHERE id=%s",
                    (sids[0],))
        cur.execute("UPDATE ai_chat_batches SET failed=1, status='failed' "
                    "WHERE id=%s", (bid,))
    db_conn.commit()
    with pytest.raises(ValueError):
        resume_batch(user_id, bid)
    # failed 不是 resume 的对象：状态没被改
    assert _child(db_conn, sids[0])[0] == 'failed'


def test_resume_batch_wrong_owner_returns_none(db_conn, user_id):
    from utils.batch_repo import resume_batch
    bid, _ = _seed_batch(db_conn, user_id, n=1)
    assert resume_batch(str(uuid.uuid4()), bid) is None


def test_resume_batch_detail_carries_cancelled_count(db_conn, user_id):
    """UI 的「继续运行」按钮显隐依据：detail/list 带 cancelled 计数。"""
    from utils.batch_repo import resume_batch, get_batch_detail, list_batches
    bid, sids = _seed_batch(db_conn, user_id, n=2)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='cancelled', "
                    "opencode_session_id='oc_1' WHERE id=%s", (sids[0],))
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s",
                    (sids[1],))
        cur.execute("UPDATE ai_chat_batches SET done=1, failed=1, "
                    "status='partial' WHERE id=%s", (bid,))
    db_conn.commit()
    assert get_batch_detail(user_id, bid)['batch']['cancelled'] == 1
    assert any(b['cancelled'] == 1 for b in list_batches(user_id, page=1,
                                                         page_size=20)['items']
               if b['id'] == bid)
    resume_batch(user_id, bid)
    assert get_batch_detail(user_id, bid)['batch']['cancelled'] == 0
