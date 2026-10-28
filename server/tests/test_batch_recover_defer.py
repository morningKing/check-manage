"""对账器「飞行中回合缓重排」（pending-flip 修复 F5）。

生产缺陷（后端重启/发版场景）：worker 线程全死，子会话行留在 running、
租约过期；OpenCode serve 是独立进程，回合还在正常生成。对账器当前在
租约过期后立即重排（requeue_continue）——行翻 pending、批次翻待运行，
新 claim 又向同一 OC 会话补发 continue 提示词：状态说谎 + 同回合双发。

锁定契约：
  F5  OC 回合仍在飞行中（最新 assistant 消息未终态/带未完成工具）→
      对账器跳过本轮（行保持 running），回合空闲后再重排续跑；
      缓重排有兜底上限（RECOVER_DEFER_MAX_SEC，防回合永久悬挂挂死恢复）。
  护栏（既有语义，修复前后都必须绿）：
      会话无 assistant 消息 / 最新消息已终态（finish+time.completed、无
      running tool）→ 首轮即重排，恢复延迟不回退。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def claim_guard():
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
            (uid, f'df_user_{uid[:8]}', 'x', f'DF User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_lost_running(db_conn, user_id):
    """租约已过期的 running 行（后端重启后对账器看到的形状）。"""
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'defer-test', 'p', 1)", (bid, user_id))
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
            "   opencode_session_id, fencing_token, lease_owner, lease_until) "
            "VALUES (%s, %s, 'running', %s, 0, 'f.csv', 'oc-live', 3, "
            "        'dead-owner', NOW() - interval '10 minutes')",
            (sid, user_id, bid))
    db_conn.commit()
    return bid, sid


def _inflight_raw():
    """飞行中：最新 assistant 消息无 finish/time.completed（正在流式生成）。"""
    return [{'info': {'id': 'm1', 'role': 'assistant', 'finish': None,
                      'time': {}},
             'parts': [{'type': 'text', 'text': '生成中…'}]}]


def _idle_raw():
    """空闲：最新 assistant 消息已终态（finish=stop + time.completed）。"""
    return [{'info': {'id': 'm1', 'role': 'assistant', 'finish': 'stop',
                      'time': {'completed': {'seconds': 1}}},
             'parts': [{'type': 'text', 'text': 'done'}]}]


def _patch_oc(monkeypatch, raw):
    import utils.batch_engine as eng
    monkeypatch.setattr(eng, 'opencode_client', type('F', (), {
        'get_messages': staticmethod(lambda oc, directory='': raw),
    })())


def _status(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sid,))
        return cur.fetchone()[0]


def _worker():
    from utils.batch_engine import BatchWorker
    return BatchWorker()


def test_reconcile_defers_while_turn_in_flight(db_conn, user_id, claim_guard,
                                               monkeypatch):
    """F5 红：回合飞行中不重排；缓重排超上限后仍重排（兜底防悬挂）。"""
    from utils import execution_checkpoint
    bid, sid = _seed_lost_running(db_conn, user_id)
    claim_guard(sid)
    # 有进度证据（checkpoint），跳过 failed_no_progress 分支直达 requeue 决策
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    _patch_oc(monkeypatch, _inflight_raw())
    worker = _worker()

    worker._reconcile_stale_running()
    assert _status(db_conn, sid) == 'running'   # 红：当前立即 pending

    # 兜底上限：缓重排不得无限等（回合「看似活着」的永久悬挂也要能恢复）
    worker.RECOVER_DEFER_MAX_SEC = -1
    worker._reconcile_stale_running()
    assert _status(db_conn, sid) == 'pending'


def test_reconcile_requeues_idle_session_first_pass(db_conn, user_id,
                                                    claim_guard, monkeypatch):
    """护栏：回合已空闲（终态消息）→ 首轮即重排，恢复延迟不回退。"""
    from utils import execution_checkpoint
    bid, sid = _seed_lost_running(db_conn, user_id)
    claim_guard(sid)
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    _patch_oc(monkeypatch, _idle_raw())

    _worker()._reconcile_stale_running()
    assert _status(db_conn, sid) == 'pending'


def test_reconcile_requeues_session_without_messages(db_conn, user_id,
                                                     claim_guard,
                                                     monkeypatch):
    """护栏：无 assistant 消息（尚未开跑）→ 首轮即重排（既有语义）。"""
    from utils import execution_checkpoint
    bid, sid = _seed_lost_running(db_conn, user_id)
    claim_guard(sid)
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    _patch_oc(monkeypatch, [])

    _worker()._reconcile_stale_running()
    assert _status(db_conn, sid) == 'pending'
