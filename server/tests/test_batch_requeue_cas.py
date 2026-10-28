"""重排类 UPDATE 的 CAS 与控制标志保护（pending-flip 修复 F1）。

生产缺陷：批子会话仍在工作，状态却从 running 翻回 pending。三个重排写点
（_maybe_auto_retry / _maybe_gate_retry / _requeue_lost）的 UPDATE 只有
`WHERE id = %s`（requeue_lost 好一点，只缺控制标志条件）：

  - 终态行（cancelled/needs_review/…）会被拉回 pending「复活」；
  - running 行上用户刚置位的 cancel_requested / pause_requested 被静默
    清掉——异常窗口内按下的中断被吞，任务重跑。

本文件锁定契约：
  F1-a auto-retry 不复活终态行；
  F1-b auto-retry/gate-retry 不吞 running 行上的取消请求（CAS 0 行 → False，
      由调用方走 _mark_failed → CAS 未命中 → _on_transition_miss 重定向到
      cancelled/paused）；
  F1-c 对账器 _requeue_lost 保留控制标志——重排回 pending 后由既有的
      _cancel_pending_requests 正常消费成 cancelled，而不是抹掉重跑。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def claim_guard():
    """持有测试行的 FOR KEY SHARE 行锁（跨提交不释放），防共享库上正在运行
    的 dev 后端认领/改写测试行（同 test_batch_auto_retry 的夹具）。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        def hold(*sids):
            if not sids:
                return
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ai_chat_sessions "
                            "WHERE id = ANY(%s) FOR KEY SHARE", (list(sids),))
        yield hold
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'cas_user_{uid[:8]}', 'x', f'CAS User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed(db_conn, user_id, status, *, cancel=False, oc='oc-x'):
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'cas-test', 'p', 1)", (bid, user_id))
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
            "   opencode_session_id, cancel_requested) "
            "VALUES (%s, %s, %s, %s, 0, 'f.csv', %s, %s)",
            (sid, user_id, status, bid, oc, cancel))
    db_conn.commit()
    return bid, sid


def _row(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, cancel_requested, pause_requested "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        return cur.fetchone()


def _worker():
    from utils.batch_engine import BatchWorker
    return BatchWorker()


def test_auto_retry_not_resurrect_terminal(db_conn, user_id, claim_guard):
    bid, sid = _seed(db_conn, user_id, 'completed')
    claim_guard(sid)
    requeued = _worker()._maybe_auto_retry(sid)
    assert requeued is False
    assert _row(db_conn, sid)[0] == 'completed'


def test_auto_retry_preserves_cancel_on_running(db_conn, user_id, claim_guard):
    bid, sid = _seed(db_conn, user_id, 'running', cancel=True)
    claim_guard(sid)
    requeued = _worker()._maybe_auto_retry(sid)
    status, cancel_flag, _ = _row(db_conn, sid)
    assert requeued is False
    assert status == 'running'
    assert cancel_flag is True


def test_gate_retry_preserves_cancel_on_running(db_conn, user_id, claim_guard,
                                                monkeypatch):
    monkeypatch.setenv('AI_BATCH_GATE_RETRY', '1')
    bid, sid = _seed(db_conn, user_id, 'running', cancel=True)
    claim_guard(sid)
    retried = _worker()._maybe_gate_retry(
        sid, {'status': 'failed', 'results': []}, batch_id=bid)
    status, cancel_flag, _ = _row(db_conn, sid)
    assert retried is False
    assert status == 'running'
    assert cancel_flag is True


def test_requeue_lost_preserves_flags_then_cancels(db_conn, user_id,
                                                   claim_guard, monkeypatch):
    import utils.batch_engine as eng
    bid, sid = _seed(db_conn, user_id, 'running', cancel=True)
    claim_guard(sid)
    # worker 失联期间用户按了中断：对账器重排必须带着标志回 pending
    monkeypatch.setattr(eng, 'opencode_client', type('F', (), {
        'get_messages': staticmethod(lambda oc, directory='': []),
    })())
    worker = _worker()
    worker._requeue_lost(sid, continue_on_same=True, batch_id=bid)
    status, cancel_flag, _ = _row(db_conn, sid)
    assert status == 'pending'
    assert cancel_flag is True
    # 标志保留后由既有的 pending 取消消费器落成 cancelled（不是抹掉重跑）
    worker._cancel_pending_requests()
    assert _row(db_conn, sid)[0] == 'cancelled'
