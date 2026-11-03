"""对账器「飞行中回合缓重排」（pending-flip 修复 F5）。

生产缺陷（后端重启/发版场景）：worker 线程全死，子会话行留在 running、
租约过期；OpenCode serve 是独立进程，回合还在正常生成。对账器当前在
租约过期后立即重排（requeue_continue）——行翻 pending、批次翻待运行，
新 claim 又向同一 OC 会话补发 continue 提示词：状态说谎 + 同回合双发。

锁定契约：
  F5  OC 回合仍在飞行中（最新 assistant 消息未终态/带未完成工具）→
      对账器跳过本轮（行保持 running），回合空闲后再重排续跑；
      缓重排有兜底上限（RECOVER_DEFER_MAX_SEC，防回合永久悬挂挂死恢复）。
  模糊态（2026-11-03）：continuation finish + 工具已落地（步骤边界），或
      REST 空快照但库内已有消息（轮询失明）→ 单次采样不可信，要求进度
      签名在 RECOVER_AMBIGUOUS_STABLE_SEC 内不变才判空闲。
  护栏（既有语义，修复前后都必须绿）：
      从未开跑（无 assistant 消息且库内无消息）/ 最新消息已终态
      （finish+time.completed、无 running tool）→ 首轮即重排，恢复延迟
      不回退。
  清场：重排/失败前先 abort 该 OC 回合（先停再翻状态），不留继续输出的
      僵尸回合（2026-11-03）。
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


def _seed_lost_running(db_conn, user_id, oc='oc-live'):
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
            "VALUES (%s, %s, 'running', %s, 0, 'f.csv', %s, 3, "
            "        'dead-owner', NOW() - interval '10 minutes')",
            (sid, user_id, bid, oc))
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


# ---------------------------------------------------------------------------
# 2026-11-03：「对账器说工作线程丢了、会话却还在正常执行」的双修
#   1) 重排/失败前 abort 该 OC 回合（先停再翻状态，不留继续输出的僵尸回合）；
#   2) F5 二次确认：步骤边界（continuation finish + 工具已落地）与「REST 空
#      快照但库里有历史」不再单次采样判空闲，要求签名在窗口内不变才重排。
# ---------------------------------------------------------------------------

def _step_boundary_raw(extra_tool: bool = False):
    """步骤边界：消息已终态、finish 仍是 tool-calls（工具已落地）。

    单次采样与「回合真的结束」（question 被自动拒绝后 OpenCode 直接完结
    消息、finish 留在 tool-calls）不可区分——正是误重排在飞回合的窗口。"""
    parts = [{'type': 'tool', 'tool': 'write',
              'state': {'status': 'completed', 'output': 'ok'}}]
    if extra_tool:
        parts = [{'type': 'tool', 'tool': 'read',
                  'state': {'status': 'completed', 'output': 'ok'}}] + parts
    return [{'info': {'id': 'm1', 'role': 'assistant',
                      'finish': 'tool-calls',
                      'time': {'completed': {'seconds': 1}}},
             'parts': parts}]


def _patch_oc_abort(monkeypatch, raw, on_abort=None):
    """Fake OC：get_messages 固定返回 raw；abort_session 记录调用。"""
    import utils.batch_engine as eng
    calls = []

    def abort_session(oc, directory=''):
        calls.append((oc, directory))
        if on_abort is not None:
            on_abort()

    monkeypatch.setattr(eng, 'opencode_client', type('F', (), {
        'get_messages': staticmethod(lambda oc, directory='': raw),
        'abort_session': staticmethod(abort_session),
    })())
    return calls


def _decisions(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT payload->>'decision' FROM ai_batch_events "
            "WHERE batch_id = %s AND event_type = 'child.recovered' "
            "ORDER BY event_seq", (bid,))
        return [r[0] for r in cur.fetchall()]


def test_turn_flight_state_three_way():
    """flying / ambiguous / idle 三态判定（含空快照与库内历史组合）。"""
    from utils.batch_engine import BatchWorker as W
    assert W._turn_flight_state(
        [{'info': {'role': 'assistant', 'finish': None, 'time': {}},
          'parts': [{'type': 'text', 'text': '生成中'}]}]) == 'flying'
    assert W._turn_flight_state(
        [{'info': {'role': 'assistant', 'finish': 'tool-calls',
                   'time': {'completed': 1}},
          'parts': [{'type': 'tool',
                     'state': {'status': 'running'}}]}]) == 'flying'
    assert W._turn_flight_state(_step_boundary_raw()) == 'ambiguous'
    assert W._turn_flight_state(_idle_raw()) == 'idle'
    assert W._turn_flight_state([]) == 'idle'
    # REST 空快照 + 库里有历史：轮询瞬时失明，不能当空闲
    assert W._turn_flight_state([], has_persisted_history=True) == 'ambiguous'


def test_recover_turn_state_resets_window_when_signature_moves():
    """模糊态期间只要有新进展（签名变化）就重置稳定窗口，不误判空闲。"""
    w = _worker()
    w.RECOVER_AMBIGUOUS_STABLE_SEC = 3600
    assert w._recover_turn_state('s1', _step_boundary_raw()) == 'deferred_ambiguous'
    sig1 = w._recover_ambiguous['s1']['sig']
    assert w._recover_turn_state(
        's1', _step_boundary_raw(extra_tool=True)) == 'deferred_ambiguous'
    assert w._recover_ambiguous['s1']['sig'] != sig1   # 窗口已重置


def test_reconcile_defers_step_boundary_then_requeues_with_abort(
        db_conn, user_id, claim_guard, monkeypatch):
    """F5 二次确认：步骤边界先缓一轮；签名稳定超窗口后才重排，且重排前
    先 abort 该 OC 回合（abort 时行仍是 running，先停再翻状态）。"""
    from utils import execution_checkpoint
    oc = f'oc-step-{uuid.uuid4().hex[:8]}'
    bid, sid = _seed_lost_running(db_conn, user_id, oc=oc)
    claim_guard(sid)
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    status_at_abort = {}

    def _on_abort():
        status_at_abort['v'] = _status(db_conn, sid)

    calls = _patch_oc_abort(monkeypatch, _step_boundary_raw(),
                            on_abort=_on_abort)
    worker = _worker()

    worker._reconcile_stale_running()
    assert _status(db_conn, sid) == 'running'   # 模糊态：先不判定
    assert [c for c in calls if c[0] == oc] == []   # 也还没 abort
    assert _decisions(db_conn, bid) == ['deferred_ambiguous']

    # 窗口过去且签名仍未动（没有下一步/新输出）→ 判空闲，重排续跑
    worker.RECOVER_AMBIGUOUS_STABLE_SEC = -1
    worker._reconcile_stale_running()
    assert _status(db_conn, sid) == 'pending'
    mine = [c for c in calls if c[0] == oc]
    assert mine and mine[0][0] == oc
    assert status_at_abort['v'] == 'running'    # abort 先于状态翻转
    assert _decisions(db_conn, bid) == [
        'deferred_ambiguous', 'requeue_continue']


def test_reconcile_defers_empty_snapshot_with_history(
        db_conn, user_id, claim_guard, monkeypatch):
    """REST 空快照 + 库里已有消息 = 轮询失明，不得当空闲立即重排。"""
    from utils import execution_checkpoint
    oc = f'oc-empty-{uuid.uuid4().hex[:8]}'
    bid, sid = _seed_lost_running(db_conn, user_id, oc=oc)
    claim_guard(sid)
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
            "VALUES (%s, %s, 'assistant', '[]', 1)",
            (str(uuid.uuid4()), sid))
    db_conn.commit()
    calls = _patch_oc_abort(monkeypatch, [])

    worker = _worker()
    worker._reconcile_stale_running()
    assert _status(db_conn, sid) == 'running'
    assert [c for c in calls if c[0] == oc] == []
    assert _decisions(db_conn, bid) == ['deferred_ambiguous']


def test_requeue_lost_budget_exhausted_aborts_before_failing(
        db_conn, user_id, claim_guard, monkeypatch):
    """重排预算用尽的终态失败同样先 abort——「判线程丢失」不等于回合已停。"""
    from utils import execution_checkpoint
    oc = f'oc-budget-{uuid.uuid4().hex[:8]}'
    bid, sid = _seed_lost_running(db_conn, user_id, oc=oc)
    claim_guard(sid)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET retry_count = 2 WHERE id = %s",
                    (sid,))
    db_conn.commit()
    execution_checkpoint.write_checkpoint(
        sid, checkpoint_type='progress', attempt_id=None,
        execution_generation=0, context_snapshot={})
    calls = _patch_oc_abort(monkeypatch, _idle_raw())

    _worker()._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions "
                    "WHERE id = %s", (sid,))
        status, err = cur.fetchone()
    assert status == 'failed'
    assert '工作线程丢失' in err
    assert [c for c in calls if c[0] == oc]
