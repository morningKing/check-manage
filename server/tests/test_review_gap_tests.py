# -*- coding: utf-8 -*-
"""复核报告缺口测试补齐（§6.2）：
- H2 内部 DELETE drain 超时分支（BATCH_DRAIN_TIMEOUT，任务保留）
- H4 编排 step 并发推进只派发一次（6 线程探针落库）
- M12 outbox sending 卡死回收 + claim 时间锚
- M14 SAVEPOINT 隔离（事件失败不毒化外层终态事务）
- H3 入口 D：账本不健康 + 仅补挂期望 → fail-closed（inconclusive）
"""
import json
import sys
import threading
import time
import uuid

import pytest

sys.path.insert(0, __import__('os').path.join(
    __import__('os').path.dirname(__file__), '..'))

from auth import create_token  # noqa: E402
from config import MCP_INTERNAL_TOKEN  # noqa: E402
from db import get_db  # noqa: E402


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'gap_user_{uid[:8]}', 'x', f'GAP {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    db_conn.rollback()  # 用例失败可能把 db_conn 留在错误态
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        # ai_batch_events 无 FK，且 event_id 全局唯一——必须随用例回收；
        # 编排用例的事件挂在 run id 上（不在批次表），两条路都要回收
        # （10 号 §3.9：此前 H4 用例每轮泄漏 8 条编排事件）
        cur.execute(
            "DELETE FROM ai_batch_events WHERE batch_id IN "
            "(SELECT id FROM ai_chat_batches WHERE user_id = %s)", (uid,))
        cur.execute(
            "DELETE FROM ai_batch_events WHERE batch_id IN "
            "(SELECT id FROM ai_orchestration_runs WHERE requested_by = %s)",
            (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_orchestration_runs WHERE requested_by = %s", (uid,))
        cur.execute(
            "DELETE FROM ai_orchestration_definitions WHERE owner_user_id = %s",
            (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# H2：内部 DELETE drain 超时 → 409 BATCH_DRAIN_TIMEOUT，任务保留
# ---------------------------------------------------------------------------

@pytest.fixture
def gap_internal_client(db_conn):
    import db as db_module
    db_module.pool = None
    from app import app
    app.config['TESTING'] = True
    token = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    return app.test_client(), {'Authorization': f'Bearer {token}',
                               'Content-Type': 'application/json'}


def _seed_batch(db_conn, user_id, n=1):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'gap-test', 'p', %s)", (bid, user_id, n))
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, 'f.csv')",
                (sid, user_id, bid, i))
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _seed_running_batch(db_conn, uid, name='AITEST-drain'):
    bid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, status) "
            "VALUES (%s, %s, %s, 'p', 1, 'running')", (bid, uid, name))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
            "  batch_seq, batch_input_file) VALUES (%s, %s, 'running', %s, 0, 'f.csv')",
            (sid, uid, bid))
    db_conn.commit()
    return bid, sid


def test_h2_internal_delete_drain_timeout(db_conn, gap_internal_client,
                                          monkeypatch):
    import routes.ai_chat_batches as mod
    monkeypatch.setattr(mod, 'DRAIN_TIMEOUT_SEC', 0.6)
    uid = 'user-admin'
    bid, sid = _seed_running_batch(db_conn, uid)
    client, hdrs = gap_internal_client
    r = client.delete(f'/ai/chat/batches/{bid}?stop=1', headers=hdrs)
    assert r.status_code == 409
    assert r.get_json()['error']['code'] == 'BATCH_DRAIN_TIMEOUT'
    # 任务与子会话保留（不删库不拆工作区）
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() is not None
    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# H4：编排 step 并发推进只派发一次
# ---------------------------------------------------------------------------

def test_h4_concurrent_advance_dispatches_once(db_conn, user_id, monkeypatch,
                                               tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    from utils import orchestration_defs as defs, orchestration_engine as eng2

    root = tmp_path
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))

    d = defs.publish_definition(
        f'h4-conc-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[{'id': 'solo', 'kind': 'agent', 'prompt_template': '做 {{input.task}}'}],
        edges=[])
    run = eng2.create_run(d['id'], user_id, run_input={'task': 'x'})

    fake = MagicMock()
    fake.create_session.return_value = 'oc-' + uuid.uuid4().hex[:8]
    fake.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]
    fake.send_message.return_value = {'id': 'm'}
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done'}]}]
    fake.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake)

    # 6 线程并发推进同一 run（复现 06 报告 §3 H4 的探针场景）
    barrier = threading.Barrier(6)

    def _advance():
        barrier.wait()
        eng2._advance_run(run['id'])

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda _: _advance(), range(6)))

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_sessions "
                    "WHERE orchestration_run_id=%s", (run['id'],))
        assert cur.fetchone()[0] == 1   # 只派发一次（修复前 6 个）
        cur.execute("SELECT attempt_count FROM ai_orchestration_steps "
                    "WHERE run_id=%s", (run['id'],))
        assert cur.fetchone()[0] == 1

    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM ai_orchestration_runs WHERE requested_by=%s", (user_id,))
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# M12：outbox sending 卡死回收 + claim 时间锚
# ---------------------------------------------------------------------------

def test_m12_sending_row_reclaimed(db_conn, user_id, monkeypatch):
    from utils import delivery_outbox
    bid = str(uuid.uuid4())
    oid = 'obx-m12-' + uuid.uuid4().hex[:8]
    eid = 'evt-m12-' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        # sending 且 next_retry_at 已是 10 分钟前（进程 kill 遗留形态）
        cur.execute(
            "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, event_type, "
            "  target_url, payload, signature, idempotency_key, status, "
            "  next_retry_at, attempt_count) "
            "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, 'sending', "
            "  NOW() - interval '20 minutes', 3)",
            (oid, eid, bid, f'{eid}:{bid}'))
    db_conn.commit()
    try:
        monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
        monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: True)
        assert delivery_outbox.drain_due_once() >= 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_delivery_outbox WHERE id=%s", (oid,))
            assert cur.fetchone()[0] == 'delivered'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_delivery_outbox WHERE id=%s", (oid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# M14：SAVEPOINT 隔离——事件失败不毒化外层终态事务
# ---------------------------------------------------------------------------

def test_m14_event_failure_does_not_poison_txn(db_conn, user_id):
    from utils import batch_events
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    # 真实失败路径：预置同 event_id（uniq_batch_event_id 全局唯一），
    # append_event 的 INSERT 撞唯一索引 → SAVEPOINT 只回滚事件本身
    dup_eid = 'dup-' + uuid.uuid4().hex[:8]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE ai_chat_sessions SET status='failed' WHERE id=%s",
                        (sid,))
            with db_conn.cursor() as c2:
                c2.execute(
                    "INSERT INTO ai_batch_events (batch_id, event_seq, event_id, "
                    "  event_type, aggregate_type) VALUES (%s, 1, %s, 'x', 'batch')",
                    (bid, dup_eid))
            db_conn.commit()
            out = batch_events.append_event(bid, 'batch.status', conn=conn,
                                            event_id=dup_eid)
            assert out is None                    # 事件失败返回 None
            cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s",
                        (sid,))
            conn.commit()

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sid,))
                assert cur.fetchone()[0] == 'completed'   # 外层终态写入存活
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s", (bid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# H3 入口 D：账本不健康 + 仅补挂期望 → fail-closed（inconclusive 携带 expected）
# ---------------------------------------------------------------------------

def _set_child(db_conn, sid, **fields):
    sets = ', '.join(f'{k} = %s' for k in fields)
    with db_conn.cursor() as cur:
        cur.execute(f"UPDATE ai_chat_sessions SET {sets} WHERE id = %s",
                    (*fields.values(), sid))
    db_conn.commit()


def test_h3_attach_expectations_fail_closed(db_conn, user_id):
    from utils import agent_ledger
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='running', opencode_session_id='oc-att-x')

    # 入口 D 补挂（批级无 action_checks，applicable=0）
    agent_ledger.register_session_expectations(
        sid, [{'name': 'must-clone', 'tool': 'bash', 'args_pattern': 'git clone'}],
        source='attach', get_db=get_db)

    # 账本不健康 → inconclusive 且携带 expected=1
    gate = agent_ledger.check_session_gate(sid, ledger_healthy=False,
                                           get_db=get_db)
    assert gate['status'] == 'inconclusive'
    assert gate.get('expected') == 1


def test_h3_exception_branch_also_fails_closed(db_conn, user_id, monkeypatch):
    """10 号 §3.1：核对自身异常的兜底分支同样携带 expected——
    "有期望但核对异常"不得被引擎判 skipped 放行。"""
    from utils import agent_ledger
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='running', opencode_session_id='oc-att-e')
    agent_ledger.register_session_expectations(
        sid, [{'name': 'must-clone', 'tool': 'bash', 'args_pattern': 'git clone'}],
        source='attach', get_db=get_db)

    # 让核对过程自身抛异常（树展开查询爆炸）
    def _boom(*a, **k):
        raise RuntimeError('ledger exploded')
    monkeypatch.setattr(agent_ledger, '_subtree_oc_ids', _boom)
    gate = agent_ledger.check_session_gate(sid, ledger_healthy=True,
                                           get_db=get_db)
    assert gate['status'] == 'inconclusive'
    assert gate.get('expected') == 1      # 引擎据此 fail-closed
    # 判定信号走 gate_participates（单一事实来源，引擎/测试共用）
    from utils.batch_engine import gate_participates
    assert gate_participates({'results': [], 'expected': 1}, 0) is True
    assert gate_participates({'results': [], 'expected': 0}, 0) is False
    assert gate_participates({'results': [{'x': 1}]}, 0) is True
    assert gate_participates({}, 2) is True


def test_validate_checks_rejects_pg_incompatible_regex(db_conn):
    """10 号 §3.1：Python re 接受但 PG `~` 拒绝的方言（embedded 命名组），
    登记时就以 PG 口径拒绝——不再写入"永远无法核对"的期望。"""
    from utils import agent_ledger
    with pytest.raises(ValueError, match='PostgreSQL'):
        agent_ledger.validate_checks(
            [{'name': 'bad', 'tool': 'bash', 'args_pattern': r'(?P<x>a)b'}])
    # 常规正则不受影响
    ok = agent_ledger.validate_checks(
        [{'name': 'good', 'tool': 'bash', 'args_pattern': r'git clone'}])
    assert len(ok) == 1


# ---------------------------------------------------------------------------
# H4①（10 号 §4）：CAS 谓词收窄为 blocked——failed 形态不再复位重派发
# ---------------------------------------------------------------------------

def test_h4_failed_step_not_redispatched(db_conn, user_id, monkeypatch):
    """base（IN ('blocked','failed')）上必失败、修复后通过：DB 中已 failed
    且无会话的 step 不会被 _launch_agent_step 复位 attempt_count 并新建会话
    （blocked 快照 → 并发转 failed 的竞态通道）。"""
    from utils import orchestration_defs as defs, orchestration_engine as eng2
    d = defs.publish_definition(
        f'h4-narrow-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[{'id': 'solo', 'kind': 'agent', 'prompt_template': '做 {{input.task}}'}],
        edges=[])
    run = eng2.create_run(d['id'], user_id, run_input={'task': 'x'})
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM ai_orchestration_steps WHERE run_id=%s",
                    (run['id'],))
        step_id = cur.fetchone()[0]
        # 竞态终态：step 已 failed、无会话（blocked 快照后并发转 failed）
        cur.execute("UPDATE ai_orchestration_steps SET status='failed', "
                    "  attempt_count=0, session_id=NULL WHERE id=%s", (step_id,))
    db_conn.commit()
    step = {'id': step_id, 'node_id': 'solo', 'name': 'solo',
            'node_def': {'id': 'solo', 'kind': 'agent',
                         'prompt_template': '做 {{input.task}}'}}
    eng2._launch_agent_step(run, step, {})
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_sessions "
                    "WHERE orchestration_run_id=%s", (run['id'],))
        assert cur.fetchone()[0] == 0          # 不新建会话（base: 1）
        cur.execute("SELECT attempt_count, status FROM ai_orchestration_steps "
                    "WHERE id=%s", (step_id,))
        attempt, status = cur.fetchone()
        assert attempt == 0 and status == 'failed'   # 不复位


# ---------------------------------------------------------------------------
# H7（10 号 §3.2）：effect 同事务 / settle 语义
# ---------------------------------------------------------------------------

def test_record_effect_same_transaction_rollback(db_conn, user_id):
    """record_effect(conn=…) 随外层事务提交/回滚——外层回滚不留孤儿 planned。"""
    from utils import execution_effect
    from utils.execution_effect import record_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    with get_db() as conn:
        out = record_effect(sid, 'callback', f'obx-tx-{uuid.uuid4().hex[:8]}',
                            batch_id=bid, conn=conn)
        assert out and out['status'] == 'planned'
        conn.rollback()                        # 外层回滚
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_execution_effects "
                        "WHERE session_id=%s", (sid,))
            assert cur.fetchone()[0] == 0      # 无孤儿 planned


def test_settle_effect_by_key_transitions(db_conn, user_id):
    """settle 语义（10 号 §3.2）：failed/unknown → committed 允许（退避重试
    或人工重放成功的正向收口）；committed 终态不可再改。"""
    from utils.execution_effect import record_effect, settle_effect_by_key
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    key = f'obx-settle-{uuid.uuid4().hex[:8]}'
    record_effect(sid, 'callback', key, batch_id=bid)
    assert settle_effect_by_key('callback', key, 'failed') is True
    # 首次失败后重试成功：failed → committed（修复前 0 行）
    assert settle_effect_by_key('callback', key, 'committed') is True
    assert settle_effect_by_key('callback', key, 'failed') is False  # 终态锁定
    # unknown → committed 同样允许
    key2 = f'obx-unknown-{uuid.uuid4().hex[:8]}'
    record_effect(sid, 'callback', key2, batch_id=bid)
    assert settle_effect_by_key('callback', key2, 'unknown') is True
    assert settle_effect_by_key('callback', key2, 'committed') is True


def test_outbox_uncertain_delivery_sets_unknown_effect(db_conn, user_id,
                                                       monkeypatch):
    """unknown 写入方（10 号 §3.2）：超时/连接类不确定结局 → effect 落
    unknown（此前一律 failed，has_unknown_effects→needs_review 永不触发）。"""
    from utils import delivery_outbox
    from utils.execution_effect import record_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    oid = 'obx-unc-' + uuid.uuid4().hex[:8]
    eid = 'evt-unc-' + uuid.uuid4().hex[:8]
    key = f'outbox:{eid}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, event_type, "
            "  target_url, payload, signature, idempotency_key, status, "
            "  next_retry_at, attempt_count) "
            "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, 'pending', "
            "  NOW(), 0)", (oid, eid, bid, key))
    db_conn.commit()
    record_effect(sid, 'callback', key, batch_id=bid)
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: 'uncertain')
    delivery_outbox.drain_due_once()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_execution_effects "
                        "WHERE idempotency_key=%s", (key,))
            assert cur.fetchone()[0] == 'unknown'
    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_delivery_outbox WHERE id=%s", (oid,))
        cur.execute("DELETE FROM ai_execution_effects WHERE idempotency_key=%s",
                    (key,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# M1 CAS-miss（10 号 §7-3）：并发 reexecute 只有一次成功，计数不重复回滚
# ---------------------------------------------------------------------------

def test_m1_reexecute_cas_miss_concurrent(db_conn, user_id):
    from concurrent.futures import ThreadPoolExecutor
    from utils.batch_repo import reexecute_child
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='failed')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET failed=1, status='failed' "
                    "WHERE id=%s", (bid,))
    db_conn.commit()

    barrier = threading.Barrier(3)

    def _re():
        barrier.wait()
        try:
            reexecute_child(user_id, bid, sid)
            return 'ok'
        except ValueError:
            return 'rejected'

    with ThreadPoolExecutor(max_workers=3) as ex:
        outcomes = list(ex.map(lambda _: _re(), range(3)))

    assert outcomes.count('ok') == 1            # CAS 只放一个赢家
    assert outcomes.count('rejected') == 2
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, failed FROM ai_chat_batches WHERE id=%s",
                    (bid,))
        status, failed = cur.fetchone()
        assert status in ('pending', 'running')
        assert failed >= 0                       # 计数未重复回滚变负


# ---------------------------------------------------------------------------
# §4.4（10 号 §3.7）：delivery/scheduler 租约 acquire-retry 接管 + stop join
# ---------------------------------------------------------------------------

def test_outbox_loop_retry_thread_takeover(monkeypatch):
    from utils import delivery_outbox, execution_lease
    calls = {'acquire': 0}

    def fake_acquire(key, owner, lease_kind=None):
        calls['acquire'] += 1
        return (calls['acquire'] >= 2, None)     # 首抢失败，重试成功

    monkeypatch.setattr(execution_lease, 'acquire', fake_acquire)
    monkeypatch.setattr(execution_lease, 'heartbeat',
                        lambda key, owner: False)  # 立即退出 loop
    monkeypatch.setattr(delivery_outbox, 'drain_due_once', lambda *a, **k: 0)

    loop = delivery_outbox.OutboxDeliveryLoop()
    monkeypatch.setattr(loop, 'RETRY_SEC', 0.05)
    loop.start()
    assert loop._retry_thread is not None        # 进入重试线程
    deadline = time.time() + 5
    while time.time() < deadline and loop._thread is None:
        time.sleep(0.05)
    assert loop._thread is not None              # 重试接管启动了投递循环
    loop.stop()
    assert not loop._retry_thread.is_alive()     # stop join 了重试线程


def test_scheduler_loop_retry_thread_takeover(monkeypatch):
    from utils import execution_lease, orchestration_engine as eng2
    calls = {'acquire': 0}

    def fake_acquire(key, owner, lease_kind=None):
        calls['acquire'] += 1
        return (calls['acquire'] >= 2, None)

    monkeypatch.setattr(execution_lease, 'acquire', fake_acquire)
    monkeypatch.setattr(execution_lease, 'heartbeat',
                        lambda key, owner: False)
    sched = eng2.OrchestrationScheduler()
    monkeypatch.setattr(sched, 'RETRY_SEC', 0.05)
    monkeypatch.setattr(sched, 'tick', lambda: None)
    sched.start()
    assert sched._retry_thread is not None
    deadline = time.time() + 5
    while time.time() < deadline and sched._thread is None:
        time.sleep(0.05)
    assert sched._thread is not None
    sched.stop()
    assert not sched._retry_thread.is_alive()


# ---------------------------------------------------------------------------
# M9（10 号 §7-3 零测试区）：跨用户不再串行复用他人 artifact 行
# ---------------------------------------------------------------------------

def test_m9_artifact_dedup_scoped_by_owner(db_conn, user_id, monkeypatch,
                                           tmp_path):
    import config as cfg
    from utils import artifact_store
    monkeypatch.setattr(cfg, 'AI_WORKSPACE_ROOT', str(tmp_path))
    f = tmp_path / 'out.txt'
    f.write_text('same content', encoding='utf-8')
    a1 = artifact_store.put_file(str(f), owner_user_id=user_id, name='out.txt')
    other = str(uuid.uuid4())
    a2 = artifact_store.put_file(str(f), owner_user_id=other, name='out.txt')
    assert a1 and a2 and a1 != a2                # 跨用户不串行（base: 相同行）
    a3 = artifact_store.put_file(str(f), owner_user_id=user_id, name='out.txt')
    assert a3 == a1                              # 同用户仍复用
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM artifacts WHERE id IN (%s, %s)", (a1, a2))
    db_conn.commit()


# ---------------------------------------------------------------------------
# M8（10 号 §3.6）：编排臂只清测试命名用户的 run，真实形态行存活
# ---------------------------------------------------------------------------

def test_m8_orchestration_cleanup_spares_real_form(db_conn):
    from tests.test_orchestration_p2 import _clear_other_pending
    uid = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'realform_{uid[:8]}', f'REAL {uid[:8]}'))
        cur.execute(
            "INSERT INTO ai_orchestration_runs (id, definition_id, "
            "  definition_version, status, requested_by, run_input_snapshot) "
            "VALUES (%s, 'def-x', 1, 'running', %s, '{}')", (run_id, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, "
            "  orchestration_run_id, orchestration_step_id) "
            "VALUES (%s, %s, 'pending', %s, 'step-x')", (sid, uid, run_id))
    db_conn.commit()
    try:
        _clear_other_pending(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_chat_sessions WHERE id=%s",
                        (sid,))
            assert cur.fetchone()[0] == 1        # 真实形态不被删（base: 0）
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_orchestration_runs WHERE id=%s",
                        (run_id,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# M3（10 号 §3.5）：编排子会话与子任务 compact 同受执行所有权门禁
# ---------------------------------------------------------------------------

def test_m3_orchestration_child_gates(db_conn, user_id, gap_internal_client):
    uid = 'user-admin'
    sid = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_orchestration_runs (id, definition_id, "
            "  definition_version, status, requested_by, run_input_snapshot) "
            "VALUES (%s, 'def-x', 1, 'running', %s, '{}')", (run_id, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, "
            "  orchestration_run_id, orchestration_step_id) "
            "VALUES (%s, %s, 'running', %s, 'step-x')", (sid, uid, run_id))
    db_conn.commit()
    client, hdrs = gap_internal_client
    try:
        # send_message → 409
        r = client.post(f'/ai/chat/sessions/{sid}/messages', headers=hdrs,
                        json={'content': '插队'})
        assert r.status_code == 409
        assert r.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'
        # /command → 409
        r = client.post(f'/ai/chat/sessions/{sid}/command', headers=hdrs,
                        json={'command': 'compact'})
        assert r.status_code == 409
        # compact → 409
        r = client.post(f'/ai/chat/sessions/{sid}/compact', headers=hdrs)
        assert r.status_code == 409
        # 子任务 compact：父会话受控 → 409（修复前 200）
        r = client.post(f'/ai/chat/sessions/{sid}/subtasks/st-x/compact',
                        headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_orchestration_runs WHERE id=%s",
                        (run_id,))
        db_conn.commit()
