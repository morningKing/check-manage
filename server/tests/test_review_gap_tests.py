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

    # 清理交给 user_id fixture（12 号 §4.2：函数内先删 run 会让 teardown
    # 的「按 run 回收事件」子查询落空 → 每轮泄漏 8 条编排事件）


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
    """12 号 §4.1：清理判定收紧——只认 fixture 生成模式（p2_user_/gap_user_）
    与测试直插 SQL 的 NULL requested_by run；测试风名字的存活用户
    （e2e_zhang 等）不被删。base（宽 LIKE e2e%/%-test）上第一断言失败。"""
    from tests.test_orchestration_p2 import _clear_other_pending
    uid = str(uuid.uuid4())
    gone_uid = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    gone_run_id = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    gone_sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'e2e_zhang_{uid[:8]}', f'REAL {uid[:8]}'))  # 测试风名字的存活用户
        cur.execute(
            "INSERT INTO ai_orchestration_runs (id, definition_id, "
            "  definition_version, status, requested_by, run_input_snapshot) "
            "VALUES (%s, 'def-x', 1, 'running', %s, '{}')", (run_id, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, "
            "  orchestration_run_id, orchestration_step_id) "
            "VALUES (%s, %s, 'pending', %s, 'step-x')", (sid, uid, run_id))
        # 崩溃残留形态：fixture 模式命名的用户 + 其 run 与 pending 子会话
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (gone_uid, f'p2_user_{gone_uid[:8]}', f'GONE {gone_uid[:8]}'))
        cur.execute(
            "INSERT INTO ai_orchestration_runs (id, definition_id, "
            "  definition_version, status, requested_by, run_input_snapshot) "
            "VALUES (%s, 'def-x', 1, 'running', %s, '{}')",
            (gone_run_id, gone_uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, "
            "  orchestration_run_id, orchestration_step_id) "
            "VALUES (%s, %s, 'pending', %s, 'step-x')",
            (gone_sid, gone_uid, gone_run_id))
    db_conn.commit()
    try:
        _clear_other_pending(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_chat_sessions WHERE id=%s",
                        (sid,))
            assert cur.fetchone()[0] == 1        # 测试风名字的存活用户不被删
            cur.execute("SELECT count(*) FROM ai_chat_sessions WHERE id=%s",
                        (gone_sid,))
            assert cur.fetchone()[0] == 0        # fixture 模式用户的残留被回收
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id IN (%s, %s)",
                        (sid, gone_sid))
            cur.execute("DELETE FROM ai_orchestration_runs WHERE id IN (%s, %s)",
                        (run_id, gone_run_id))
            cur.execute("DELETE FROM users WHERE id IN (%s, %s)", (uid, gone_uid))
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


def test_m3_subtask_compact_ownership_404(db_conn, user_id, gap_internal_client,
                                          tmp_path):
    """12 号 §6-3：子任务 compact 的归属 404 路径——子任务属于同 owner 的
    其他会话（root 不匹配 URL sid）→ 404，不能凭 owner 枚举。"""
    uid = 'user-admin'
    sid = str(uuid.uuid4())
    other_root = str(uuid.uuid4())
    stid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, "
            "  opencode_session_id, workspace_path) "
            "VALUES (%s, %s, 'completed', 'oc-x', %s)", (sid, uid, str(tmp_path)))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status) "
            "VALUES (%s, %s, 'completed')", (other_root, uid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, "
            "  status) VALUES (%s, %s, 'build', 'completed')",
            (stid, other_root))
    db_conn.commit()
    client, hdrs = gap_internal_client
    try:
        r = client.post(f'/ai/chat/sessions/{sid}/subtasks/{stid}/compact',
                        headers=hdrs)
        assert r.status_code == 404
        assert r.get_json()['code'] == 'SUBTASK_NOT_FOUND'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_subtasks WHERE id=%s", (stid,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id IN (%s, %s)",
                        (sid, other_root))
        db_conn.commit()


# ---------------------------------------------------------------------------
# H7③（12 号 §3 必修）：真实 deliver_one 读 _fire_single_webhook 返回值——
# HTTP 2xx/4xx/超时三分分类，unknown 生产可达
# ---------------------------------------------------------------------------

def _seed_outbox_and_effect(db_conn, bid, sid):
    oid = 'obx-cls-' + uuid.uuid4().hex[:8]
    eid = 'evt-cls-' + uuid.uuid4().hex[:8]
    key = f'outbox:{eid}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, event_type, "
            "  target_url, payload, signature, idempotency_key, status, "
            "  next_retry_at, attempt_count) "
            "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, 'pending', "
            "  NOW(), 0)", (oid, eid, bid, key))
    db_conn.commit()
    from utils.execution_effect import record_effect
    record_effect(sid, 'callback', key, batch_id=bid)
    return oid, key


def test_deliver_one_real_classification(db_conn, user_id, monkeypatch):
    """网络层打桩（requests.post），走**真实** _fire_single_webhook +
    deliver_one（12 号 §6-2：monkeypatch deliver_one 不构成生产证据）。"""
    import requests as _rq
    import utils.webhook_engine as wh
    from utils import delivery_outbox

    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]

    def _run(fake_post, expect_outbox, expect_effect):
        oid, key = _seed_outbox_and_effect(db_conn, bid, sid)
        monkeypatch.setattr(wh.requests, 'post', fake_post)
        monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
        delivery_outbox.drain_due_once()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_delivery_outbox WHERE id=%s",
                            (oid,))
                assert cur.fetchone()[0] == expect_outbox, expect_outbox
                cur.execute("SELECT status FROM ai_execution_effects "
                            "WHERE idempotency_key=%s", (key,))
                assert cur.fetchone()[0] == expect_effect, expect_effect
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_delivery_outbox WHERE id=%s", (oid,))
            cur.execute("DELETE FROM ai_execution_effects WHERE idempotency_key=%s",
                        (key,))
        db_conn.commit()

    ok = type('R', (), {'status_code': 200, 'text': 'ok', 'raise_for_status': lambda s: None})()
    # 2xx → delivered + committed
    _run(lambda *a, **k: ok, 'delivered', 'committed')
    # 500（确定性否定）→ failed/退避 + effect failed（修复前记 delivered+committed）
    err500 = type('R', (), {'status_code': 500, 'text': 'boom',
                            'raise_for_status': lambda s: None})()
    _run(lambda *a, **k: err500, 'failed', 'failed')
    # 超时（无响应）→ 退避 + effect unknown（修复前记 delivered+committed）
    _run(lambda *a, **k: (_ for _ in ()).throw(_rq.exceptions.Timeout('t')),
         'failed', 'unknown')


def test_settle_unknown_can_fail_later(db_conn, user_id):
    """12 号 §4.3：unknown 粘滞缓解——后续拿到确定性否定（dead_letter 时
    4xx）允许 unknown → failed；committed 仍粘性。"""
    from utils.execution_effect import record_effect, settle_effect_by_key
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    key = f'obx-uf-{uuid.uuid4().hex[:8]}'
    record_effect(sid, 'callback', key, batch_id=bid)
    assert settle_effect_by_key('callback', key, 'unknown') is True
    assert settle_effect_by_key('callback', key, 'failed') is True   # 12 号新增
    assert settle_effect_by_key('callback', key, 'committed') is True
    assert settle_effect_by_key('callback', key, 'unknown') is False  # 粘性


def test_drain_heartbeat_per_row(db_conn, user_id, monkeypatch):
    """12 号 §6-4：drain 逐行续租回调真实被调用（TTL 窗口关闭的可测面）。"""
    from utils import delivery_outbox
    bid, sids = _seed_batch(db_conn, user_id, 1)
    oids = []
    for i in range(2):
        oid = f'obx-hb{i}-' + uuid.uuid4().hex[:8]
        eid = f'evt-hb{i}-' + uuid.uuid4().hex[:8]
        oids.append(oid)
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, "
                "  event_type, target_url, payload, signature, idempotency_key, "
                "  status, next_retry_at, attempt_count) "
                "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, "
                "  'pending', NOW(), 0)", (oid, eid, bid, f'k-{eid}'))
    db_conn.commit()
    beats = {'n': 0}
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: True)
    delivery_outbox.drain_due_once(heartbeat_fn=lambda: beats.__setitem__(
        'n', beats['n'] + 1))
    assert beats['n'] == 2                          # 每行一次
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_delivery_outbox WHERE id = ANY(%s)", (oids,))
    db_conn.commit()


def test_settle_skips_event_for_deleted_batch(db_conn, user_id, monkeypatch):
    """16 号 §2 问题 2：批次已删后 outbox 重投递（dev-server 侧，不在任何
    pytest 会话内）不再产生孤儿 ai_batch_events——_settle 查批次存活才写事件。
    批次存活的对照组照常写。"""
    from utils import delivery_outbox
    bid_alive, sids = _seed_batch(db_conn, user_id, 1)
    bid_gone = str(uuid.uuid4())                    # 批次行不存在
    rows = []
    for bid, tag in ((bid_alive, 'alive'), (bid_gone, 'gone')):
        oid = f'obx-del-{tag}-' + uuid.uuid4().hex[:8]
        eid = f'evt-del-{tag}-' + uuid.uuid4().hex[:8]
        rows.append((oid, eid, bid))
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, "
                "  event_type, target_url, payload, signature, idempotency_key, "
                "  status, next_retry_at, attempt_count) "
                "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, "
                "  'pending', NOW(), 0)", (oid, eid, bid, f'k-{eid}'))
    db_conn.commit()
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: True)
    delivery_outbox.drain_due_once()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                for oid, eid, bid in rows:
                    cur.execute(
                        "SELECT count(*) FROM ai_batch_events "
                        "WHERE batch_id=%s AND aggregate_id=%s", (bid, oid))
                    n = cur.fetchone()[0]
                    if bid == bid_alive:
                        assert n == 1               # 存活批次照常写
                    else:
                        assert n == 0               # 已删批次不再写（修复前 1）
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_delivery_outbox WHERE id = ANY(%s)",
                        ([r[0] for r in rows],))
            cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s",
                        (bid_alive,))
        db_conn.commit()


def test_m14_outbox_enqueue_failure_does_not_poison_txn(db_conn, user_id,
                                                        monkeypatch):
    """M14 outbox 侧（12 号 §2.2-8：代码对齐但零测试）：入队失败只回滚
    入队本身，外层终态事务写入存活。"""
    from utils import delivery_outbox
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    monkeypatch.setattr(delivery_outbox, '_insert',
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError('boom')))
    with get_db() as conn:
        cur = conn.cursor()
        out = delivery_outbox.enqueue(bid, target_url='http://cb/x',
                                      payload={'e': 1}, secret='',
                                      event_id='evt-x', conn=conn)
        assert out is None                          # 入队失败返回 None
        cur.execute("UPDATE ai_chat_sessions SET status='completed' "
                    "WHERE id=%s", (sid,))
        conn.commit()                               # 外层提交不被毒化
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sid,))
            assert cur.fetchone()[0] == 'completed'


def test_workspace_quota_per_session_semantics(db_conn, user_id, tmp_path,
                                               monkeypatch):
    """配额口径语义变更（2026-09-28，用户决定）：`AI_WORKSPACE_QUOTA_MB`
    限制**单个会话**工作区大小，而非用户全部会话合计——创建新会话不再被
    其他会话的占用阻断。refresh 判定本会话大小并跨阈值告警一次。"""
    import utils.workspace as ws_mod
    from utils.workspace import refresh_workspace_bytes, create_session_workspace
    import config as cfg
    monkeypatch.setattr(cfg, 'AI_WORKSPACE_QUOTA_MB', 1)  # 1MB
    # ai_execution_usage.session_id 有 FK → 需真实会话行
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
            "VALUES ('sess-q1', %s, 'q1', 'active'), "
                    "('sess-q2', %s, 'q2', 'active')", (user_id, user_id))
    db_conn.commit()

    # 小文件 → 未超限；usage 行已刷新
    small = tmp_path / 'small'
    small.mkdir()
    (small / 'a.txt').write_text('x' * 100, encoding='utf-8')
    r1 = refresh_workspace_bytes('sess-q1', str(small))
    assert r1['quotaExceeded'] is False
    with db_conn.cursor() as cur:
        cur.execute("SELECT workspace_bytes FROM ai_execution_usage "
                    "WHERE session_id='sess-q1'")
        assert cur.fetchone()[0] >= 100

    # 大文件（2MB > 1MB）→ 超限
    big = tmp_path / 'big'
    big.mkdir()
    (big / 'b.bin').write_bytes(b'x' * (2 * 1024 * 1024))
    r2 = refresh_workspace_bytes('sess-q2', str(big))
    assert r2['quotaExceeded'] is True
    r3 = refresh_workspace_bytes('sess-q2', str(big))
    assert r3['quotaExceeded'] is True   # 持续超限持续返回 True

    # 单会话口径核心断言：另一个会话超限不影响新会话创建（用户合计口径下
    # 会基于累计值阻断；锁定「创建永不因其他会话占用被拦」）
    ws = create_session_workspace(str(tmp_path), user_id, 'sess-q-new')
    assert ws

    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_execution_usage WHERE session_id IN "
                    "('sess-q1','sess-q2')")
        cur.execute("DELETE FROM ai_chat_sessions WHERE id IN ('sess-q1','sess-q2')")
    db_conn.commit()


def test_cursor_expired_returns_410(db_conn, user_id, gap_internal_client):
    """缺口补齐 V4：保留期裁剪后游标过旧 → 410 CURSOR_EXPIRED。"""
    bid, sids = _seed_batch(db_conn, 'user-admin', 1)  # client 身份为 admin
    client, hdrs = gap_internal_client
    # 造 seq=1..5 的事件并删除 seq<5 的（模拟保留期裁剪），使 min_seq=5
    with db_conn.cursor() as cur:
        for seq in range(1, 6):
            cur.execute(
                "INSERT INTO ai_batch_events (batch_id, event_seq, event_id, "
                "  event_type, aggregate_type) "
                "VALUES (%s, %s, %s, 'x', 'batch')",
                (bid, seq, f'evt-ce-{bid}-{seq}'))
        cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s "
                    "AND event_seq < 5", (bid,))
    db_conn.commit()
    r = client.get(f'/ai/chat/batches/{bid}/events?afterSeq=2', headers=hdrs)
    assert r.status_code == 410
    assert 'CURSOR_EXPIRED' in r.get_data(as_text=True)
    # 正常游标仍可用
    r2 = client.get(f'/ai/chat/batches/{bid}/events?afterSeq=5', headers=hdrs)
    assert r2.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s", (bid,))
    db_conn.commit()


def test_scan_scheduler_requires_lease(db_conn, monkeypatch):
    """缺口补齐 V3：scan scheduler tick 纳入 DB 租约——抢不到租约则本轮
    不执行任何扫描任务（多进程单实例）；租约可用则正常执行。"""
    from utils import ai_scan_scheduler as sched
    ran = []
    import utils.ai_scan_repo as _repo
    monkeypatch.setattr(_repo, 'list_tasks', lambda: [
        {'id': 't1', 'enabled': True}])
    import utils.ai_scan_engine as _engine
    monkeypatch.setattr(_engine, 'run_task', lambda task: ran.append(task['id']))

    class _FakeLock:
        def acquire(self, blocking=False): return True
        def release(self): pass
    monkeypatch.setattr(sched, '_task_lock', lambda tid: _FakeLock())
    import utils.execution_lease as el
    monkeypatch.setattr(el, 'acquire', lambda key, owner, lease_kind=None:
                        (False, None))
    sched._tick()
    assert ran == []                       # 无租约：不扫描
    monkeypatch.setattr(el, 'acquire', lambda key, owner, lease_kind=None:
                        (True, None))
    sched._tick()
    assert ran == ['t1']                   # 有租约：执行


def test_force_stop_batch_cancels_running(db_conn, user_id, monkeypatch):
    """缺口补齐 4（P1 §6.4）：force_stop 立即取消全部非终态子任务
    （fencing+1 使旧执行体失效），批次落 failed，abort 尽力执行。"""
    from unittest.mock import MagicMock
    from utils.batch_repo import force_stop_batch
    bid, sids = _seed_batch(db_conn, user_id, 2)
    _set_child(db_conn, sids[0], status='running', opencode_session_id='oc-fs-1')
    _set_child(db_conn, sids[1], status='pending')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET status='running' WHERE id=%s",
                    (bid,))
    db_conn.commit()
    aborted = []
    fake_oc = MagicMock()
    fake_oc.abort_session.side_effect = lambda oc, **k: aborted.append(oc)
    import utils.opencode_client as _ocmod
    monkeypatch.setattr(_ocmod, 'OpenCodeClient', lambda base_url: fake_oc)
    r = force_stop_batch(bid, operator='test-admin')
    assert r['cancelled'] == 2
    assert sorted(aborted) == ['oc-fs-1']
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sids[0],))
        assert cur.fetchone()[0] == 'cancelled'
        cur.execute("SELECT status FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] == 'failed'


def test_v1_effect_writers_end_to_end(db_conn, user_id, monkeypatch, tmp_path):
    """V1（缺口补齐验证）：artifact 与 file_import 两类 effect 生产写入方，
    经真实代码路径（ingest_session_outputs / import_recorded_files）断言
    effect 行登记与终态。mcp_write 经内部转发端点的测试见
    test_internal_commands_endpoint 家族；scan_writeback 的登记见
    ai_scan_engine（on_child_finished 回写成功路径）。"""
    import config as cfg
    from utils import artifact_store
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    monkeypatch.setattr(cfg, 'AI_WORKSPACE_ROOT', str(tmp_path))
    out_dir = tmp_path / 'outputs'
    out_dir.mkdir()
    (out_dir / 'result.txt').write_text('产出内容', encoding='utf-8')
    ids = artifact_store.ingest_session_outputs(
        sid, str(tmp_path), run_id=f'run-v1-{uuid.uuid4().hex[:8]}',
        step_id='st-v1', owner_user_id=user_id, batch_id=bid)
    assert ids, '产物应登记成功'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai_execution_effects "
                "WHERE session_id=%s AND effect_type='artifact'", (sid,))
            assert cur.fetchone()[0] >= 1          # artifact effect 已登记
    # file_import：登记 session_files 记录 → 导入 → effect committed
    from utils.session_file_import import import_recorded_files
    src = tmp_path / 'doc.txt'
    src.write_text('导入内容', encoding='utf-8')
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_session_files (session_id, path, status) "
            "VALUES (%s, 'doc.txt', 'added') RETURNING id", (sid,))
        _sfid = cur.fetchone()[0]
    db_conn.commit()
    results = import_recorded_files(sid, str(tmp_path), ['doc.txt'],
                                    uploaded_by=user_id)
    assert results and results[0]['status'] in ('imported', 'existing')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM ai_execution_effects "
                "WHERE effect_type='file_import' AND session_id=%s "
                "ORDER BY created_at DESC LIMIT 1", (sid,))
            row = cur.fetchone()
            assert row is not None, 'file_import effect 未登记'
            assert row[0] == 'committed'
    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_execution_effects WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM artifacts WHERE id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM ai_execution_effects "
                    "WHERE session_id=%s AND effect_type='file_import'", (sid,))
    db_conn.commit()


def test_internal_commands_endpoint(db_conn, gap_internal_client):
    """缺口补齐 4.3（P1 §8.1）：内部 commands 端点——幂等（重复键 200 +
    duplicate=True）、状态查询、expectedGeneration 冲突 409 VERSION_CONFLICT。
    gap_internal_client 的身份是 user-admin，批次须归属同一用户。"""
    bid, sids = _seed_batch(db_conn, 'user-admin', 1)
    client, hdrs = gap_internal_client
    idem = f'idem-{uuid.uuid4().hex[:8]}'
    r1 = client.post(f'/ai/chat/batches/{bid}/commands',
                     headers={**hdrs, 'Idempotency-Key': idem},
                     json={'type': 'pause'})
    assert r1.status_code == 202
    body1 = r1.get_json()
    assert body1['status'] == 'applied'
    # 幂等重放：同一键 → 200 + duplicate=True + 同一 commandId
    r2 = client.post(f'/ai/chat/batches/{bid}/commands',
                     headers={**hdrs, 'Idempotency-Key': idem},
                     json={'type': 'pause'})
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert body2['duplicate'] is True and body2['commandId'] == body1['commandId']
    # 状态查询
    r3 = client.get(f"/ai/chat/batches/{bid}/commands/{body1['commandId']}",
                    headers=hdrs)
    assert r3.status_code == 200
    assert r3.get_json()['status'] == 'applied'
    # expectedGeneration 冲突 → 409 VERSION_CONFLICT（结构化错误码）
    r4 = client.post(f'/ai/chat/batches/{bid}/commands',
                     headers={**hdrs,
                              'Idempotency-Key': f'idem-{uuid.uuid4().hex[:8]}'},
                     json={'type': 'resume', 'expectedGeneration': 999})
    assert r4.status_code == 409
    assert r4.get_json()['error']['code'] == 'VERSION_CONFLICT'
    # 清理（user-admin 不经 fixture）
    client.delete(f'/ai/chat/batches/{bid}?stop=1', headers=hdrs)


def test_structured_error_detail(app):
    """P1-A1：err() 支持 error_detail 增量字段。"""
    from utils.api_errors import err
    with app.test_request_context():
        resp, status = err('msg', 'CODE', 409,
                           retryable=True, phase='dispatch', attempt=2,
                           evidence_refs=['event:bevt_x'])
        body = resp.get_json()
    assert body['error'] == 'msg'
    assert body['code'] == 'CODE'
    assert body['error_detail']['retryable'] is True
    assert body['error_detail']['phase'] == 'dispatch'
    assert body['error_detail']['attempt'] == 2
    assert body['error_detail']['evidenceRefs'] == ['event:bevt_x']


def test_structured_error_backward_compat(app):
    """P1-A1：不传 kwargs 时行为与既有完全一致（无 error_detail 键）。"""
    from utils.api_errors import err
    with app.test_request_context():
        resp, status = err('msg', 'CODE', 404)
        body = resp.get_json()
    assert body['error'] == 'msg'
    assert body['code'] == 'CODE'
    assert 'error_detail' not in body


# ---------------------------------------------------------------------------
# P1-A1：对外 _batch_out 增量返回 paused/eventCursor + children 结构化数组
# ---------------------------------------------------------------------------

OPEN_BASE = '/v1/ai-batches'


@pytest.fixture
def gap_open_client(db_conn):
    """真实（不 mock DB）的对外 API test client。

    同 test_open_api_batch_integration.py 的模式：把已导入模块绑定的
    get_db 一律 rebind 回真实实现，让真实 api_key_required 网关 +
    get_batch_detail/get_children_progress 走真库。"""
    import db as db_module
    if hasattr(db_module.pool, '_mock_name'):
        db_module.pool = None
    real_get_db = db_module.get_db
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
                mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            try:
                mod.get_db = real_get_db
            except (AttributeError, TypeError):
                pass
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _seed_open_key(db_conn, user_id):
    """造一把绑定到 user_id 的真实 API Key，返回 (明文 key, key id)。"""
    from auth import hash_api_key
    key = 'cm_gap_' + uuid.uuid4().hex
    kid = 'ak-gap-' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash, is_active, owner_user_id) "
            "VALUES (%s, 'gap-open-key', %s, TRUE, %s)",
            (kid, hash_api_key(key), user_id))
    db_conn.commit()
    return key, kid


def _seed_open_batch(db_conn, user_id, api_key_id, files):
    """带 api_key_id 的批次 + n 个 pending 子会话（get_batch_detail 按
    api_key_id 过滤，必须写上才能被对外 detail 读到）。返回 (batch_id, sids)。"""
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, api_key_id, name, "
            "  prompt, total, status) "
            "VALUES (%s, %s, %s, 'gap-open-batch', 'p', %s, 'pending')",
            (bid, user_id, api_key_id, len(files)))
        for i, f in enumerate(files):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)", (sid, user_id, bid, i, f))
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _cleanup_open_batch(db_conn, bid, kid):
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s", (bid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
        cur.execute("DELETE FROM api_keys WHERE id=%s", (kid,))
    db_conn.commit()


def test_batch_out_has_paused_and_event_cursor(db_conn, user_id, gap_open_client):
    """P1-A1：_batch_out 增量返回 paused 和 eventCursor。"""
    key, kid = _seed_open_key(db_conn, user_id)
    bid, sids = _seed_open_batch(db_conn, user_id, kid, ['a.csv'])
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_batch_events (batch_id, event_seq, event_id, "
                "  event_type, aggregate_type) VALUES (%s, 1, %s, 'x', 'batch')",
                (bid, f'evt-pec-{bid}'))
        db_conn.commit()
        r = gap_open_client.get(f'{OPEN_BASE}/{bid}', headers={'X-API-Key': key})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert 'paused' in body and body['paused'] is False  # 无 paused 子会话
        assert 'eventCursor' in body and isinstance(body['eventCursor'], int)
        assert body['eventCursor'] == 1                      # 读的是真实事件游标
    finally:
        _cleanup_open_batch(db_conn, bid, kid)


def test_batch_out_children_array(db_conn, user_id, gap_open_client):
    """P1-A1：_batch_out 返回 children 结构化数组。"""
    key, kid = _seed_open_key(db_conn, user_id)
    bid, sids = _seed_open_batch(db_conn, user_id, kid, ['dir/a.csv', 'b.pdf'])
    try:
        r = gap_open_client.get(f'{OPEN_BASE}/{bid}', headers={'X-API-Key': key})
        assert r.status_code == 200, r.get_data(as_text=True)
        children = r.get_json()['children']
        assert isinstance(children, list) and len(children) == 2
        first, second = children              # ORDER BY batch_seq
        assert first['childId'] == sids[0] and first['seq'] == 0
        assert first['name'] == 'a.csv'       # 路径只取文件名
        assert first['status'] == 'pending'
        assert first['attempt'] == 1          # execution_generation 0 + 1
        assert first['retryCount'] == 0
        assert first['retryable'] is True     # 非终态可重试
        assert first['error'] == {'code': None, 'message': None, 'retryable': False}
        assert second['childId'] == sids[1] and second['name'] == 'b.pdf'
        for c in children:
            assert {'childId', 'name', 'status', 'attempt', 'retryCount',
                    'retryable', 'error'} <= set(c)
    finally:
        _cleanup_open_batch(db_conn, bid, kid)


# ---------------------------------------------------------------------------
# P1-A2b：扫描产物导入 data_files 的 file_import effect 补漏
# ---------------------------------------------------------------------------

def test_scan_output_import_records_file_import_effect(db_conn, user_id,
                                                       tmp_path, monkeypatch):
    """_import_child_outputs_to_record 直插 data_files（不经
    import_recorded_files），补登记 file_import effect 并即时收口
    committed——幂等键 = session_id:文件相对路径。"""
    from unittest.mock import patch

    from utils.ai_scan_engine import _import_child_outputs_to_record

    coll = f'gap_eff_{uuid.uuid4().hex[:8]}'
    rid, sid, bid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    ws = tmp_path / 'ws'
    (ws / 'outputs').mkdir(parents=True)
    (ws / 'outputs' / 'result.txt').write_text('ok', encoding='utf-8')
    monkeypatch.setenv('DATA_FILES_ROOT', str(tmp_path / 'storage'))

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, branch_id) "
            "VALUES (%s, %s, %s::jsonb, 'main')",
            (rid, coll, json.dumps({'files': []})))
    db_conn.commit()
    try:
        session_row = {'id': sid, 'user_id': user_id, 'batch_id': bid,
                       'workspace_path': str(ws)}
        task = {'collection': coll, 'branchId': 'main'}
        with patch('utils.execution_effect.record_effect') as m_rec, \
             patch('utils.execution_effect.settle_effect') as m_set:
            m_rec.return_value = {'id': 'eff-test', 'status': 'planned',
                                  'external_ref': None, 'result_hash': None}
            n = _import_child_outputs_to_record(task, rid, session_row,
                                                'files')
        assert n == 1
        assert m_rec.call_count == 1
        args, kwargs = m_rec.call_args
        assert args[0] == sid                    # 子会话 id
        assert args[1] == 'file_import'          # effect 类型
        assert args[2] == f'{sid}:result.txt'    # 幂等键
        assert kwargs.get('batch_id') == bid
        s_args, s_kwargs = m_set.call_args
        assert s_args[0] == 'eff-test'
        assert s_args[1] == 'committed'
        assert s_kwargs.get('external_ref')      # 新 data_file 的 id
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM data_files WHERE original_name = %s "
                        "AND uploaded_by = %s", ('result.txt', user_id))
            cur.execute("DELETE FROM dynamic_data WHERE id = %s", (rid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# P1-A9：BACKUP_TABLES 覆盖 AI Harness 新表
# ---------------------------------------------------------------------------

def test_backup_covers_ai_harness_tables():
    """P1-A9：BACKUP_TABLES 覆盖 AI Harness 新表。"""
    from utils.backup import BACKUP_TABLES
    table_names = {t[0] for t in BACKUP_TABLES}
    required = {
        'ai_orchestration_definitions', 'ai_orchestration_runs',
        'ai_orchestration_steps', 'artifacts', 'artifact_refs',
        'ai_runtime_manifests', 'ai_execution_attempts',
        'ai_execution_events', 'ai_execution_checkpoints',
        'ai_execution_effects', 'ai_execution_commands',
        'ai_batch_events', 'ai_delivery_outbox',
        'ai_execution_budgets', 'ai_execution_usage',
        'ai_chat_turns', 'agent_tool_calls', 'action_expectations',
    }
    missing = required - table_names
    assert not missing, f"BACKUP_TABLES 缺少: {missing}"

    # 无重复登记（BACKUP_TABLES 按列表遍历导出，重复行会导出两遍）
    names = [t[0] for t in BACKUP_TABLES]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"BACKUP_TABLES 重复登记: {dupes}"

    # JSONB 索引必须落在列范围内
    for name, cols, jsonb_idx, _label in BACKUP_TABLES:
        bad = [i for i in jsonb_idx if not 0 <= i < len(cols)]
        assert not bad, f"{name} JSONB 索引越界: {bad}"


# ---------------------------------------------------------------------------
# P1-B3：/metrics Prometheus 文本格式监控端点
# ---------------------------------------------------------------------------

def test_metrics_endpoint(app, mock_conn):
    """P1-B3：/metrics 端点返回 Prometheus 文本格式指标。"""
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def fake_db():
        yield mock_conn

    # 六个聚合指标各 fetchone 一次；lease 心跳 fetchall
    mock_conn.cursor.return_value.fetchone.return_value = (0,)
    mock_conn.cursor.return_value.fetchall.return_value = []
    # routes.metrics 在 import 时绑定了自己的 get_db，须对模块本身打补丁
    # （与 conftest._rebind_module_get_db_to_real 记录的绑定陷阱同因）
    with patch('routes.metrics.get_db', fake_db):
        r = app.test_client().get('/metrics')
    assert r.status_code == 200
    assert 'text/plain' in r.content_type
    body = r.get_data(as_text=True)
    assert 'ai_batch_queue_depth' in body
    assert 'ai_batch_running' in body
    assert 'ai_outbox_pending' in body
    assert 'ai_batch_needs_review' in body


# ---------------------------------------------------------------------------
# P2-A5/A6/A3：step 超时兜底 + join_policy 汇聚 + 定义字段透传/版本递增
# ---------------------------------------------------------------------------

def test_step_timeout_marks_failed(db_conn, user_id):
    """P2-A5：running step 超过节点 timeout_sec 后由调度标 failed + 写
    step.timeout 事件，run 不再永久 running。timeout_sec 必须 正整数
    （0/负数/字符串在校验层拒绝）。"""
    from utils import orchestration_defs as defs, orchestration_engine as eng2
    # 校验把关：timeout_sec 必须是正整数
    with pytest.raises(ValueError):
        defs.validate_definition(
            [{'id': 's1', 'kind': 'agent', 'prompt_template': 'x',
              'timeout_sec': 0}], [])
    with pytest.raises(ValueError):
        defs.validate_definition(
            [{'id': 's1', 'kind': 'agent', 'prompt_template': 'x',
              'timeout_sec': -5}], [])
    with pytest.raises(ValueError):
        defs.validate_definition(
            [{'id': 's1', 'kind': 'agent', 'prompt_template': 'x',
              'timeout_sec': '60'}], [])
    d = defs.publish_definition(
        f'gap-timeout-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[{'id': 's1', 'kind': 'agent', 'prompt_template': 'x',
                'timeout_sec': 1}],
        edges=[])
    run = eng2.create_run(d['id'], user_id, run_input={'task': 'x'})
    try:
        # advance 会 launch step（创建子会话 pending）→ step running
        eng2._advance_run(run['id'])
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, session_id FROM ai_orchestration_steps "
                        "WHERE run_id=%s", (run['id'],))
            status, sid = cur.fetchone()
        assert status == 'running' and sid
        # 模拟超时：把 started_at 拨到 1 小时前（远超 timeout_sec=1）
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_orchestration_steps SET "
                        "started_at = NOW() - interval '1 hour' "
                        "WHERE run_id=%s", (run['id'],))
        db_conn.commit()
        # 再 advance → 超时兜底标 failed
        eng2._advance_run(run['id'])
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, error_message FROM ai_orchestration_steps "
                        "WHERE run_id=%s", (run['id'],))
            status, err = cur.fetchone()
            assert status == 'failed'
            assert 'step timeout' in (err or '')
            cur.execute("SELECT count(*) FROM ai_batch_events "
                        "WHERE batch_id=%s AND event_type='step.timeout'",
                        (run['id'],))
            assert cur.fetchone()[0] == 1          # 超时事件已写
        assert eng2.get_run(run['id'])['status'] == 'failed'  # run 不再 running
    finally:
        # 子会话仍 pending——先收掉，避免共享库被并行 worker 认领
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions "
                        "WHERE orchestration_run_id=%s", (run['id'],))
        db_conn.commit()


def test_join_any_success_policy(db_conn, user_id):
    """P2-A6：join_policy='any_success'——菱形 DAG 一侧条件不命中被 skip
    不阻塞 join：a succeeded 后 join 正常通过、run completed。"""
    from utils import orchestration_defs as defs, orchestration_engine as eng2
    d = defs.publish_definition(
        f'gap-joinany-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[
            {'id': 'split', 'kind': 'agent', 'prompt_template': '评估'},
            {'id': 'a', 'kind': 'agent', 'prompt_template': 'A 路径'},
            {'id': 'b', 'kind': 'agent', 'prompt_template': 'B 路径'},
            {'id': 'merge', 'kind': 'join', 'join_policy': 'any_success'},
        ],
        edges=[
            {'source': 'split', 'target': 'a', 'kind': 'advance',
             'condition': {'field': 'size', 'op': '>', 'value': 100}},
            {'source': 'split', 'target': 'b', 'kind': 'advance'},
            {'source': 'a', 'target': 'merge', 'kind': 'join'},
            {'source': 'b', 'target': 'merge', 'kind': 'join'},
        ])
    run = eng2.create_run(d['id'], user_id)
    # 预置 split 成功且 size=200 → a 命中条件、b 不可达 → skipped
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_orchestration_steps SET status='succeeded', "
            "output='{\"size\": 200, \"text\": \"ok\"}'::jsonb, finished_at=NOW() "
            "WHERE run_id=%s AND node_id='split'", (run['id'],))
    db_conn.commit()
    eng2._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng2.get_run(run['id'])['steps']}
    assert step_map['b']['status'] == 'skipped'
    assert step_map['a']['status'] == 'running'
    # 修复前：b skipped 立即把 join 传播成 skipped（永久阻塞）；修复后等 a
    assert step_map['merge']['status'] == 'blocked'
    # a 完成 → join 在 any_success 下通过（不因 b skipped 阻塞）
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "output='{\"text\": \"a done\"}'::jsonb, finished_at=NOW() "
                    "WHERE run_id=%s AND node_id='a'", (run['id'],))
        # a 的子会话不再需要——终态化避免被共享库 worker 认领
        cur.execute("UPDATE ai_chat_sessions SET status='cancelled' "
                    "WHERE orchestration_run_id=%s", (run['id'],))
    db_conn.commit()
    eng2._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng2.get_run(run['id'])['steps']}
    assert step_map['merge']['status'] == 'succeeded'
    assert eng2.get_run(run['id'])['status'] == 'completed'


def test_join_default_policy_still_skips_on_dead_dep(db_conn, user_id):
    """P2-A6 回归护栏：默认 all_success join 的失败/skip 传播语义不变——
    依赖终态且无一成功 → join skipped（run 不卡死，H6 行为保留）。"""
    from utils import orchestration_defs as defs, orchestration_engine as eng2
    d = defs.publish_definition(
        f'gap-joinall-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[
            {'id': 'split', 'kind': 'agent', 'prompt_template': '评估'},
            {'id': 'a', 'kind': 'agent', 'prompt_template': 'A 路径'},
            {'id': 'b', 'kind': 'agent', 'prompt_template': 'B 路径'},
            {'id': 'merge', 'kind': 'join'},   # 未声明 join_policy → 默认
        ],
        edges=[
            {'source': 'split', 'target': 'a', 'kind': 'advance',
             'condition': {'field': 'size', 'op': '<', 'value': 1}},
            {'source': 'split', 'target': 'b', 'kind': 'advance'},
            {'source': 'a', 'target': 'merge', 'kind': 'join'},
            {'source': 'b', 'target': 'merge', 'kind': 'join'},
        ])
    run = eng2.create_run(d['id'], user_id)
    # split 成功但条件不命中 → a 不可达 → skipped，b 派发 running
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_orchestration_steps SET status='succeeded', "
            "output='{\"size\": 200, \"text\": \"ok\"}'::jsonb, finished_at=NOW() "
            "WHERE run_id=%s AND node_id='split'", (run['id'],))
    db_conn.commit()
    eng2._advance_run(run['id'])
    # skip 传播按调度 tick 收敛（b 的派发在首轮 runnable pass）→ 再推一轮
    eng2._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng2.get_run(run['id'])['steps']}
    assert step_map['a']['status'] == 'skipped'
    assert step_map['merge']['status'] == 'skipped'   # skip 向下游传播（不变）


def test_publish_version_increments(db_conn, user_id):
    """P2-A3：同 id 重复发布递增 version，旧版本保留可取、缺省取最新；
    未传 id 仍自动生成（既有调用不受影响）。"""
    from utils import orchestration_defs as defs
    did = f'gap-ver-{uuid.uuid4().hex[:8]}'
    v1 = defs.publish_definition('gap-ver-def', description=None,
                                 owner_user_id=user_id, def_id=did,
                                 nodes=[{'id': 's', 'kind': 'agent',
                                         'prompt_template': 'v1'}], edges=[])
    assert v1 == {'id': did, 'version': 1}
    v2 = defs.publish_definition('gap-ver-def', description=None,
                                 owner_user_id=user_id, def_id=did,
                                 nodes=[{'id': 's', 'kind': 'agent',
                                         'prompt_template': 'v2'}], edges=[])
    assert v2 == {'id': did, 'version': 2}
    # 版本不可变：v1 仍取到旧内容；缺省取最新版本
    assert defs.get_definition(did, version=1)['nodes'][0]['prompt_template'] == 'v1'
    latest = defs.get_definition(did)
    assert latest['version'] == 2
    assert latest['nodes'][0]['prompt_template'] == 'v2'
    # 不传 def_id：自动生成 id、从 version=1 起步
    auto = defs.publish_definition(f'gap-ver-auto-{uuid.uuid4().hex[:6]}',
                                   description=None, owner_user_id=user_id,
                                   nodes=[{'id': 's', 'kind': 'agent',
                                           'prompt_template': 'x'}], edges=[])
    assert auto['version'] == 1 and auto['id'] != did


def test_publish_preserves_all_fields(db_conn, user_id):
    """P2-A3：skills/input_refs/runtime/budget/timeout_sec/join_policy
    不再被归一化白名单丢弃（引擎从 step.node_def 读这些字段驱动
    超时/join 语义）。join_policy 枚举校验同时把关。"""
    from utils import orchestration_defs as defs
    d = defs.publish_definition(
        f'gap-fields-{uuid.uuid4().hex[:8]}', description=None,
        owner_user_id=user_id,
        nodes=[
            {'id': 's', 'kind': 'agent', 'prompt_template': 'x',
             'skills': ['sql'], 'input_refs': ['a.csv'],
             'runtime': {'kind': 'opencode_local'},
             'budget': {'max_cost': 1.5}, 'timeout_sec': 60},
            {'id': 'j', 'kind': 'join', 'join_policy': 'any_success'},
        ],
        edges=[{'source': 's', 'target': 'j', 'kind': 'join'},
               {'source': 's', 'target': 'j', 'kind': 'advance'}])
    got = defs.get_definition(d['id'])
    nodes = {n['id']: n for n in got['nodes']}
    assert nodes['s']['skills'] == ['sql']
    assert nodes['s']['input_refs'] == ['a.csv']
    assert nodes['s']['runtime'] == {'kind': 'opencode_local'}
    assert nodes['s']['budget'] == {'max_cost': 1.5}
    assert nodes['s']['timeout_sec'] == 60
    assert nodes['j']['join_policy'] == 'any_success'
    # 枚举校验：非法 join_policy 拒绝发布
    with pytest.raises(ValueError):
        defs.validate_definition(
            [{'id': 's', 'kind': 'agent', 'prompt_template': 'x'},
             {'id': 'j', 'kind': 'join', 'join_policy': 'bogus'}],
            [{'source': 's', 'target': 'j', 'kind': 'join'},
             {'source': 's', 'target': 'j', 'kind': 'advance'}])


# ---------------------------------------------------------------------------
# P2-B1：批调度并发度可配（ai_settings.batch_max_concurrent）
# ---------------------------------------------------------------------------

def test_max_concurrent_from_settings(db_conn):
    """P2-B1：并发度从 ai_settings 动态读取。"""
    from utils.batch_engine import BatchWorker
    w = BatchWorker()
    # 列可能尚未随 init_db 落库（幂等补齐，保证用例自洽）
    with db_conn.cursor() as cur:
        cur.execute("ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS "
                    "batch_max_concurrent INT NULL")
        cur.execute("UPDATE ai_settings SET batch_max_concurrent = NULL")
    db_conn.commit()
    # 不设 DB → env 默认 3
    assert w._effective_concurrency() == 3
    # 设 DB → 读 DB 值
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_settings SET batch_max_concurrent = 1")
    db_conn.commit()
    assert w._effective_concurrency() == 1
    # 清理
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_settings SET batch_max_concurrent = NULL")
    db_conn.commit()


# ---------------------------------------------------------------------------
# P2-B2：统一租约框架——后台调度器 start 入口含 execution_lease 保护
# ---------------------------------------------------------------------------

def test_all_schedulers_use_lease():
    """P2-B2：所有后台调度器 start 入口含 execution_lease 保护。"""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    schedulers = [
        'server/utils/ai_scan_scheduler.py',
        'server/utils/backup.py',
        'server/utils/status_badge_timeout_scheduler.py',
        'server/utils/field_index_scheduler.py',
        'server/utils/etl_scheduler.py',
    ]
    for p in schedulers:
        src = (repo_root / p).read_text(encoding='utf-8', errors='replace')
        assert 'execution_lease' in src, f'{p} 缺少租约保护'
    # audit retention 在 app.py 而非 skillopt.py：检查它被租约保护
    app_src = (repo_root / 'server' / 'app.py').read_text(
        encoding='utf-8', errors='replace')
    assert 'audit_retention' in app_src and 'execution_lease' in app_src, \
        'app.py 的 audit retention 调度器缺少租约保护'


# ---------------------------------------------------------------------------
# P2-B4：workspace TTL 回收 + 配额检查
# ---------------------------------------------------------------------------

def test_workspace_ttl_cleanup(db_conn, user_id, tmp_path, monkeypatch):
    """P2-B4：终态子会话 workspace 过 TTL 后被回收（目录删除 + 清列）；
    未过 TTL 的终态会话不动。"""
    import config as cfg
    import utils.orchestration_engine as orch
    monkeypatch.setattr(cfg, 'AI_SESSION_TTL_HOURS', 1)  # TTL=1h

    ws_stale = tmp_path / 'ws-stale'
    ws_stale.mkdir()
    (ws_stale / 'out.txt').write_text('x', encoding='utf-8')
    ws_fresh = tmp_path / 'ws-fresh'
    ws_fresh.mkdir()
    (ws_fresh / 'keep.txt').write_text('x', encoding='utf-8')

    bid_stale, sid_stale = str(uuid.uuid4()), str(uuid.uuid4())
    bid_fresh, sid_fresh = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        # 过 TTL：批次 2h 前终态，子会话终态带 workspace
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, "
            "  status, completed_at) "
            "VALUES (%s, %s, 'ttl-stale', 'p', 1, 'completed', "
            "  NOW() - interval '2 hours')", (bid_stale, user_id))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
            "  batch_seq, workspace_path) "
            "VALUES (%s, %s, 'completed', %s, 0, %s)",
            (sid_stale, user_id, bid_stale, str(ws_stale)))
        # 未过 TTL：批次刚终态
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, "
            "  status, completed_at) "
            "VALUES (%s, %s, 'ttl-fresh', 'p', 1, 'completed', NOW())",
            (bid_fresh, user_id))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
            "  batch_seq, workspace_path) "
            "VALUES (%s, %s, 'completed', %s, 0, %s)",
            (sid_fresh, user_id, bid_fresh, str(ws_fresh)))
    db_conn.commit()

    orch.OrchestrationScheduler()._cleanup_stale_workspaces()

    assert not ws_stale.exists()          # 目录被删除
    with db_conn.cursor() as cur:
        cur.execute("SELECT workspace_path FROM ai_chat_sessions WHERE id=%s",
                    (sid_stale,))
        assert cur.fetchone()[0] is None  # 列被清空（retry 不会再指向死路径）
        cur.execute("SELECT workspace_path FROM ai_chat_sessions WHERE id=%s",
                    (sid_fresh,))
        assert cur.fetchone()[0] == str(ws_fresh)
    assert ws_fresh.exists()              # 未过 TTL：目录保留


def test_workspace_quota_rejects(tmp_path, monkeypatch):
    """P2-B4：workspace 超配额时 _prepare_workspace 抛 PermissionError；
    配额默认 0（未显式设置）不启用、不阻断派发。"""
    import os
    import shutil
    import config as cfg
    import utils.batch_engine as eng
    uid = f'quota-{uuid.uuid4().hex[:8]}'
    monkeypatch.setattr(cfg, 'AI_WORKSPACE_QUOTA_MB', 1)  # 1MB
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(tmp_path))
    user_ws = tmp_path / uid / 'prev-session'
    user_ws.mkdir(parents=True)
    (user_ws / 'big.bin').write_bytes(b'x' * (2 * 1024 * 1024))  # 已占 2MB

    with pytest.raises(PermissionError):
        eng._prepare_workspace(uid, str(uuid.uuid4()), '')

    # 显式关闭（0）：同一占用下新会话照常创建
    monkeypatch.setattr(cfg, 'AI_WORKSPACE_QUOTA_MB', 0)
    ws = eng._prepare_workspace(uid, 'sess-quota-off', '')
    assert ws and os.path.isdir(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# P2-C5：进度落库实时 usage 累计
# ---------------------------------------------------------------------------

def test_progress_persist_updates_usage(db_conn, user_id, monkeypatch):
    """P2-C5：进度落库（_persist_conversation）后 ai_execution_usage 即时
    更新，不等回合收敛。重复持久化走累加（实时可见口径刻意偏大，终态由
    accumulate_usage 整行覆盖自校正）。"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker

    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
            "VALUES (%s, %s, 'usage-live', 'running')", (sid, user_id))
    db_conn.commit()

    def _msg(mid, tok_in, tok_out, cost):
        return {
            'info': {'id': mid, 'role': 'assistant',
                     'time': {'created': 1000, 'completed': 2000},
                     'tokens': {'input': tok_in, 'output': tok_out},
                     'cost': cost},
            'parts': [{'type': 'text', 'text': '完成'}],
        }

    fake_oc = MagicMock()
    fake_oc.get_messages.return_value = [_msg(f'{sid}:m1', 500, 100, 0.01)]
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)

    worker = BatchWorker()
    worker._persist_conversation(sid, '提示词', 'oc-usage-1', None)

    def _usage_row():
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT tokens_input, tokens_output, cost "
                "FROM ai_execution_usage WHERE session_id = %s", (sid,))
            return cur.fetchone()

    row = _usage_row()
    assert row is not None
    assert row[0] == 500 and row[1] == 100
    assert abs(float(row[2]) - 0.01) < 1e-6

    # 第二拍（消息列表新增 m2）：累加语义 —— m1 重复计入 + m2（实时可见，
    # 运行中数值允许偏大；终态 finally 的 accumulate_usage 会整行覆盖）
    fake_oc.get_messages.return_value = [
        _msg(f'{sid}:m1', 500, 100, 0.01),
        _msg(f'{sid}:m2', 200, 50, 0.005),
    ]
    worker._persist_conversation(sid, '提示词', 'oc-usage-1', None)
    row2 = _usage_row()
    assert row2[0] == 500 + 500 + 200
    assert row2[1] == 100 + 100 + 50


# ---------------------------------------------------------------------------
# P3-C1：PG LISTEN/NOTIFY 替代 dispatcher 轮询（触发器迁移 + LISTEN 接线）
# ---------------------------------------------------------------------------

def test_batch_notify_trigger_exists(db_conn):
    """P3-C1：NOTIFY 触发器迁移幂等可重跑，且函数/触发器已落库。"""
    import importlib.util as ilu
    import os as _os
    mp = _os.path.join(_os.path.dirname(__file__), '..', 'migrations',
                       '2026_09_26_batch_notify_trigger.py')
    spec = ilu.spec_from_file_location('_batch_notify_boot_test', mp)
    m = ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.run()  # 幂等：重跑不应报错
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_proc "
                    "WHERE proname = 'notify_batch_claim'")
        assert cur.fetchone()[0] >= 1, 'notify_batch_claim() 函数未创建'
        cur.execute("SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'notify_batch_claim_ready' "
                    "  AND tgisinternal = false")
        assert cur.fetchone()[0] >= 1, 'ai_chat_sessions 触发器未创建'


def test_batch_enqueue_notifies_claim_channel(db_conn, user_id):
    """P3-C1 端到端：子会话行 status→pending 提交后，batch_claim_ready
    通知到达 LISTEN 连接（dispatcher select 唤醒的数据源）；非 pending
    与 no-op 转移不产生通知。"""
    import select as _select
    import psycopg2
    from config import DB_CONFIG
    listener = psycopg2.connect(**DB_CONFIG)
    listener.autocommit = True
    sid = str(uuid.uuid4())
    try:
        with listener.cursor() as cur:
            cur.execute('LISTEN batch_claim_ready')
        # 非 pending 插入：不通知
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status) "
                "VALUES (%s, %s, 'failed')", (sid, user_id))
        db_conn.commit()
        assert not _select.select([listener], [], [], 0.5)[0], \
            '非 pending 转移不应通知'
        # failed → pending 转移：通知
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET status='pending' "
                        "WHERE id=%s", (sid,))
        db_conn.commit()
        got = False
        deadline = time.time() + 5
        while time.time() < deadline and not got:
            if _select.select([listener], [], [], 0.5)[0]:
                listener.poll()
                got = any(n.channel == 'batch_claim_ready'
                          for n in listener.notifies)
        assert got, 'status→pending 未触发 batch_claim_ready 通知'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
        db_conn.commit()
        listener.close()


def test_batch_engine_dispatcher_has_listen():
    """P3-C1 源码断言：dispatcher 等待原语接入 LISTEN/NOTIFY——select 唤醒、
    断线重连退避、stop/退出关闭连接，且保留 _wake.wait 轮询兜底。"""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    src = (repo_root / 'server' / 'utils' / 'batch_engine.py').read_text(
        encoding='utf-8', errors='replace')
    assert 'LISTEN {self.LISTEN_CHANNEL}' in src, '缺少 LISTEN 订阅'
    assert "LISTEN_CHANNEL = 'batch_claim_ready'" in src, '频道名与迁移不一致'
    assert 'select.select(' in src, 'dispatcher 未用 select 等待通知'
    assert 'LISTEN_RECONNECT_SEC' in src, '缺少断线重连退避'
    assert 'def _listen_close' in src, '缺少 LISTEN 连接关闭（stop 路径）'
    assert 'self._wait_for_wake(10)' in src, 'dispatcher 未接入等待原语'
    assert 'self._wake.wait(slice_sec)' in src, '轮询兜底被移除'


# ---------------------------------------------------------------------------
# P3-A8：Runtime Adapter 生产接线（batch_engine 经 get_runtime 取 client）
# ---------------------------------------------------------------------------

def test_batch_engine_uses_runtime_adapter():
    """P3-A8：batch_engine 的 OpenCode 调用经 runtime adapter。"""
    from utils.runtime import get_runtime
    rt = get_runtime()
    assert rt is not None, 'runtime adapter 应可用'
    assert hasattr(rt, 'get_client'), 'OpenCodeLocalRuntime 应有 get_client 方法'


def test_runtime_get_client_returns_opencode_client():
    """P3-A8：get_client() 返回 OpenCodeClient 实例（与直连构造同型，
    行为不变可回退）。"""
    from utils.runtime import get_runtime
    from utils.opencode_client import OpenCodeClient
    client = get_runtime().get_client()
    assert isinstance(client, OpenCodeClient)


def test_facade_client_prefers_runtime_and_falls_back(monkeypatch):
    """P3-A8：facade._client() 优先 runtime.get_client()；runtime 不可用
    （返回 None / 抛异常 / 无 get_client）时兜底直连，不 crash。"""
    from utils import runtime as rt_mod
    import utils.batch_engine as eng
    from utils.opencode_client import OpenCodeClient

    sentinel = object()

    class _StubRt:
        def get_client(self):
            return sentinel

    orig_default = rt_mod._default
    try:
        rt_mod._default = _StubRt()
        assert eng.opencode_client._client() is sentinel  # 经 adapter
    finally:
        rt_mod._default = orig_default

    def _boom():
        raise RuntimeError('runtime down')
    monkeypatch.setattr(rt_mod, 'get_runtime', _boom)
    assert isinstance(eng.opencode_client._client(), OpenCodeClient)  # 兜底直连

    monkeypatch.setattr(rt_mod, 'get_runtime', lambda: None)
    assert isinstance(eng.opencode_client._client(), OpenCodeClient)  # None 兜底


# ---------------------------------------------------------------------------
# P3-C2：管理面 attempt 生命周期时间线（/ai/chat/admin/batches/sessions/<sid>/
# attempt-timeline）+ /metrics 的 ai_attempts_running gauge
# ---------------------------------------------------------------------------

def _seed_attempt_session(db_conn, uid):
    """建一个无 batch 归属的交互会话（timeline 端点正是要覆盖这类会话）。"""
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, workspace_path, "
            "  session_token, token_expires_at) "
            "VALUES (%s, %s, 'active', '', %s, NOW() + interval '1 day')",
            (sid, uid, 'tok-' + sid[:16]))
    db_conn.commit()
    return sid


def _seed_attempts(db_conn, sid):
    """两条 attempt：第 1 条已完成（含 duration），第 2 条失败带恢复原因。"""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
            "  attempt_no, operation, status, agent_resolution, model_resolution, "
            "  started_at, finished_at) "
            "VALUES (%s, %s, 'interactive', 1, 'send', 'completed', "
            "  'requested', 'requested', "
            "  NOW() - interval '10 seconds', NOW() - interval '3 seconds')",
            (f'att-{sid}-1', sid))
        cur.execute(
            "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
            "  attempt_no, operation, status, agent_resolution, model_resolution, "
            "  started_at, error_code, error_message, parent_attempt_id, recovery_reason) "
            "VALUES (%s, %s, 'interactive', 2, 'retry', 'failed', "
            "  'fallback', 'requested', NOW() - interval '2 seconds', "
            "  'TIMEOUT', 'upstream timeout', %s, 'lease_expired')",
            (f'att-{sid}-2', sid, f'att-{sid}-1'))
    db_conn.commit()


def test_attempt_timeline_lists_attempts_in_order(db_conn, user_id, gap_internal_client):
    """P3-C2：时间线按 attempt_no 升序返回全字段；未结束的 attempt 以 NOW()
    兜底计 duration_s；时间戳 isoformat。"""
    client, headers = gap_internal_client
    sid = _seed_attempt_session(db_conn, user_id)
    _seed_attempts(db_conn, sid)

    resp = client.get(
        f'/ai/chat/admin/batches/sessions/{sid}/attempt-timeline', headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['sessionId'] == sid
    attempts = body['attempts']
    assert [a['attempt_no'] for a in attempts] == [1, 2]

    a1, a2 = attempts
    assert a1['status'] == 'completed'
    assert a1['agent_resolution'] == 'requested'
    assert isinstance(a1['duration_s'], int) and a1['duration_s'] >= 7  # 10s - 3s
    assert a1['started_at'].endswith('+00:00') or 'T' in a1['started_at']

    assert a2['status'] == 'failed'
    assert a2['operation'] == 'retry'
    assert a2['model_resolution'] == 'requested'
    assert a2['error_code'] == 'TIMEOUT'
    assert a2['error_message'] == 'upstream timeout'
    assert a2['parent_attempt_id'] == f'att-{sid}-1'
    assert a2['recovery_reason'] == 'lease_expired'
    # 未结束：finished_at 为空，duration 以 NOW() 兜底（>= 起点差）
    assert a2['finished_at'] is None
    assert isinstance(a2['duration_s'], int)


def test_attempt_timeline_unknown_session_404(db_conn, gap_internal_client):
    """不存在的会话 404（与同文件其余子任务端点的 404 语义一致）。"""
    client, headers = gap_internal_client
    resp = client.get(
        f'/ai/chat/admin/batches/sessions/{uuid.uuid4()}/attempt-timeline',
        headers=headers)
    assert resp.status_code == 404


def test_attempt_timeline_rejects_non_admin(app, dev_headers):
    """能力门：developer（admin_keys 为空）一律 403，不能跨用户读 attempt。"""
    resp = app.test_client().get(
        '/ai/chat/admin/batches/sessions/s-x/attempt-timeline', headers=dev_headers)
    assert resp.status_code == 403


def test_attempt_timeline_rejects_anonymous(app):
    resp = app.test_client().get(
        '/ai/chat/admin/batches/sessions/s-x/attempt-timeline')
    assert resp.status_code in (401, 403)


def test_metrics_includes_ai_attempts_running(app, mock_conn):
    """P3-C2：/metrics 暴露 ai_attempts_running gauge（claimed/running/recovering）。"""
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def fake_db():
        yield mock_conn

    mock_conn.cursor.return_value.fetchone.return_value = (0,)
    mock_conn.cursor.return_value.fetchall.return_value = []
    with patch('routes.metrics.get_db', fake_db):
        r = app.test_client().get('/metrics')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '# TYPE ai_attempts_running gauge' in body
    assert 'ai_attempts_running 0' in body


# ---------------------------------------------------------------------------
# P3-C3：action gate PreToolUse 拦截——deny list 过程阻断
# （走真实共享开发库，与 test_agent_action_gate 同约定；app 夹具的 mock
#   cursor 恒返回空集，无法表达"命中 deny 行"，故不用 app 夹具）
# ---------------------------------------------------------------------------

def _cleanup_expectations(*scope_ids):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM action_expectations "
                        "WHERE scope_id = ANY(%s)", (list(scope_ids),))


def test_pre_check_tool_denies():
    """P3-C3：pre 模式期望匹配 → deny。"""
    from utils.agent_ledger import register_session_expectations, pre_check_tool
    sid = f'gap-pre-{uuid.uuid4().hex[:10]}'
    try:
        register_session_expectations(sid, [
            {'name': 'no-delete', 'tool': 'bash', 'args_pattern': 'rm -rf',
             'mode': 'pre'}], source='pre-test', get_db=get_db)
        result = pre_check_tool(sid, 'bash', 'command=rm -rf /', get_db=get_db)
        assert result['allow'] is False
        assert 'no-delete' in result['reason']
    finally:
        _cleanup_expectations(sid)


def test_pre_check_tool_allows_non_matching():
    """pre 模式期望不匹配 → allow。"""
    from utils.agent_ledger import register_session_expectations, pre_check_tool
    sid = f'gap-pre-{uuid.uuid4().hex[:10]}'
    try:
        register_session_expectations(sid, [
            {'name': 'no-delete', 'tool': 'bash', 'args_pattern': 'rm -rf',
             'mode': 'pre'}], source='pre-test', get_db=get_db)
        result = pre_check_tool(sid, 'bash', 'command=echo hello',
                                get_db=get_db)
        assert result['allow'] is True
        assert result['rules'] == 1          # 有 pre 规则：插件不短路缓存
    finally:
        _cleanup_expectations(sid)


def test_pre_check_post_mode_ignored():
    """post 模式期望不参与 pre-check。"""
    from utils.agent_ledger import register_session_expectations, pre_check_tool
    sid = f'gap-pre-{uuid.uuid4().hex[:10]}'
    try:
        register_session_expectations(sid, [
            {'name': 'post-check', 'tool': 'bash', 'args_pattern': 'git clone',
             'mode': 'post'}], source='pre-test', get_db=get_db)
        result = pre_check_tool(sid, 'bash', 'command=git clone',
                                get_db=get_db)
        assert result['allow'] is True
        assert result['rules'] == 0          # 无 pre 规则：插件短路缓存
    finally:
        _cleanup_expectations(sid)


def test_validate_checks_mode_backward_compatible():
    """P3-C3：mode 缺省 'post'（既有创建路径零改动）；非法值拒绝；
    pre 只支持 tool 型检查（file/db_record/verifier 是效果断言，只能终态核对）。"""
    from utils.agent_ledger import validate_checks
    out = validate_checks(
        [{'name': 'm-default', 'tool': 'bash', 'args_pattern': 'x'}])
    assert out[0]['mode'] == 'post'
    out2 = validate_checks(
        [{'name': 'm-explicit', 'tool': 'bash', 'args_pattern': 'x',
          'mode': 'pre'}])
    assert out2[0]['mode'] == 'pre'
    with pytest.raises(ValueError, match='mode'):
        validate_checks(
            [{'name': 'm-bogus', 'tool': 'bash', 'args_pattern': 'x',
              'mode': 'bogus'}])
    with pytest.raises(ValueError, match=r'mode=pre'):
        validate_checks(
            [{'name': 'm-file-pre', 'check_type': 'file',
              'effect_spec': {'path': 'out/*.txt'}, 'mode': 'pre'}])


def test_gate_terminal_check_skips_pre_mode(db_conn, user_id):
    """P3-C3：mode='pre' 的行不参与终态核对（deny 规则在过程期拦截，
    终态"该做的做了没"对 deny 规则无意义）；post 行核对行为不变；
    inconclusive 的 expected 计数同样只数 post 行（仅 pre 期望的会话
    不再触发 fail-closed——过程拦截与终态核对互不纠缠）。"""
    from utils.agent_ledger import (register_session_expectations,
                                    check_session_gate)
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='running', opencode_session_id='oc-pre-x')
    try:
        register_session_expectations(sid, [
            {'name': 'deny-rm', 'tool': 'bash', 'args_pattern': 'rm -rf',
             'mode': 'pre', 'scope': 'session'},
            {'name': 'must-echo', 'tool': 'bash', 'args_pattern': 'echo done',
             'scope': 'session'},
        ], source='pre-test', get_db=get_db)
        gate = check_session_gate(sid, ledger_healthy=True, get_db=get_db)
        assert gate['status'] == 'failed'        # post 期望未命中 → 照旧 failed
        assert [r['name'] for r in gate['results']] == ['must-echo']
        gate2 = check_session_gate(sid, ledger_healthy=False, get_db=get_db)
        assert gate2['status'] == 'inconclusive'
        assert gate2.get('expected') == 1        # 只数 post 行
    finally:
        _cleanup_expectations(sid)


def test_pre_check_endpoint_requires_internal_token(app):
    """P3-C3：内部端点走 X-Internal-Token 鉴权（与 memory/subagent 内部
    通道同一信任边界）——无 token 一律 403（token 未配置时同样拒绝）。"""
    r = app.test_client().post('/ai/gate/internal/pre-check',
                               json={'sessionId': 'sess_x', 'tool': 'bash'})
    assert r.status_code == 403


def test_settle_by_key_session_scoped(db_conn, user_id):
    """遗留项 1：session 不匹配时 settle 返回 False。
    （ai_execution_effects.session_id 有 FK → sess-a 用真实会话；
    sess-b 只出现在 SELECT 条件里，用不存在的 id。）"""
    from utils.execution_effect import record_effect, settle_effect_by_key
    _, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]                       # sess-a 等价物（真实会话）
    other = str(uuid.uuid4())           # sess-b 等价物（不存在）
    eff = record_effect(sid, 'mcp_write', 'key-scoped', conn=None)
    assert eff is not None
    assert settle_effect_by_key('mcp_write', 'key-scoped', 'committed',
                                session_id=other) is False
    assert settle_effect_by_key('mcp_write', 'key-scoped', 'committed',
                                session_id=sid) is True
    # 清理
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_execution_effects WHERE session_id = %s", (sid,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# 遗留项 3：step 超时兜底同时 abort 子会话的 OpenCode 回合
# ---------------------------------------------------------------------------

def test_step_timeout_aborts_child_session(db_conn, user_id, monkeypatch):
    """遗留项 3：step 超时时 abort 对应的子会话——超时 CAS 标 failed 的同时
    按子会话 opencode_session_id best-effort abort（此前子会话滞留 running
    直到对账器接管），step.timeout 事件 payload 补 sessionId。"""
    from unittest.mock import MagicMock
    from utils import orchestration_defs as defs, orchestration_engine as eng2
    d = defs.publish_definition(
        f'gap-tabort-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[{'id': 's1', 'kind': 'agent', 'prompt_template': 'x',
                'timeout_sec': 1}],
        edges=[])
    run = eng2.create_run(d['id'], user_id, run_input={'task': 'x'})
    try:
        # advance 派发 agent step（创建子会话 pending、step running）
        eng2._advance_run(run['id'])
        with db_conn.cursor() as cur:
            cur.execute("SELECT session_id FROM ai_orchestration_steps "
                        "WHERE run_id=%s", (run['id'],))
            child_sid = cur.fetchone()[0]
        assert child_sid
        # 子会话绑假 oc id（模拟已发出 OpenCode 回合）+ 模拟超时
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET "
                        "opencode_session_id='oc-tabort-1' WHERE id=%s",
                        (child_sid,))
            cur.execute("UPDATE ai_orchestration_steps SET "
                        "started_at = NOW() - interval '1 hour' "
                        "WHERE run_id=%s", (run['id'],))
        db_conn.commit()
        aborted = []
        fake_oc = MagicMock()
        fake_oc.abort_session.side_effect = lambda oc, **k: aborted.append(oc)
        import utils.opencode_client as _ocmod
        monkeypatch.setattr(_ocmod, 'OpenCodeClient', lambda base_url: fake_oc)

        eng2._advance_run(run['id'])

        assert aborted == ['oc-tabort-1']       # 子会话回合被 abort
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_orchestration_steps "
                        "WHERE run_id=%s", (run['id'],))
            assert cur.fetchone()[0] == 'failed'
            cur.execute("SELECT payload FROM ai_batch_events "
                        "WHERE batch_id=%s AND event_type='step.timeout'",
                        (run['id'],))
            payload = cur.fetchone()[0]
        assert payload['sessionId'] == child_sid    # 事件 payload 补 sessionId
        assert payload['timeoutSec'] == 1
    finally:
        # 子会话仍非终态——先收掉，避免共享库被并行 worker 认领
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions "
                        "WHERE orchestration_run_id=%s", (run['id'],))
        db_conn.commit()


# ---------------------------------------------------------------------------
# 遗留项 5：M3 门禁扩展到 6 个变更入口（close/clear/run_script/
# delete-message/DELETE session/archive）；abort 刻意豁免——用户必须能停
# 失控任务
# ---------------------------------------------------------------------------

def test_m3_gates_on_mutation_endpoints(db_conn, gap_internal_client):
    """遗留项 5：非终态批子会话的 6 个变更入口一律 409
    BATCH_SESSION_CONTROLLED；abort 端点不挡（停失控任务的生命线）。"""
    uid = 'user-admin'
    bid, sid = _seed_running_batch(db_conn, uid, name='AITEST-m3mut')
    client, hdrs = gap_internal_client
    try:
        # close → 409
        r = client.post(f'/ai/chat/sessions/{sid}/close', headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'
        assert r.get_json()['error']['operation'] == 'close_session'
        # clear → 409
        r = client.post(f'/ai/chat/sessions/{sid}/clear', headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['operation'] == 'clear_session'
        # run_script → 409
        r = client.post(f'/ai/chat/sessions/{sid}/run', headers=hdrs,
                        json={'code': 'print(1)'})
        assert r.status_code == 409
        assert r.get_json()['error']['operation'] == 'run_script'
        # delete-message → 409
        r = client.delete(f'/ai/chat/sessions/{sid}/messages/msg-x',
                          headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['operation'] == 'delete_message'
        # DELETE session → 409
        r = client.delete(f'/ai/chat/sessions/{sid}', headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['operation'] == 'delete_session'
        # archive（admin-only）→ 409
        r = client.post(f'/ai/chat/sessions/{sid}/archive', headers=hdrs)
        assert r.status_code == 409
        assert r.get_json()['error']['operation'] == 'archive_session'
        # abort 不挡：仍可用（无 oc 回合 → 幂等 no-op 200）
        r = client.post(f'/ai/chat/sessions/{sid}/abort', headers=hdrs)
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'stopped': False}
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_batch_events WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# P3-B5：子代理独立查看与取消（列表 + abort 端点）
# ---------------------------------------------------------------------------

def test_list_subtasks(db_conn, gap_internal_client):
    """P3-B5：列出子代理——按 created_at 顺序返回全部字段；父会话不存在
    （不属于当前用户）→ 404，不能凭空枚举。"""
    uid = 'user-admin'
    sid = str(uuid.uuid4())
    st1 = 'ses_gap_' + uuid.uuid4().hex[:8]
    st2 = 'ses_gap_' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, workspace_path) "
            "VALUES (%s, %s, 'completed', '/ws/gap-list')", (sid, uid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status, "
            "  description, created_at, completed_at) "
            "VALUES (%s, %s, 'build', 'completed', '构建完成', "
            "  NOW() - interval '5 minutes', NOW() - interval '1 minute')",
            (st1, sid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status, "
            "  description) VALUES (%s, %s, 'scan', 'running', '扫描中')",
            (st2, sid))
    db_conn.commit()
    client, hdrs = gap_internal_client
    try:
        r = client.get(f'/ai/chat/sessions/{sid}/subtasks', headers=hdrs)
        assert r.status_code == 200
        items = r.get_json()
        assert [i['id'] for i in items] == [st1, st2]    # created_at 升序
        by_id = {i['id']: i for i in items}
        assert by_id[st1]['status'] == 'completed'
        assert by_id[st1]['agent'] == 'build'
        assert by_id[st1]['description'] == '构建完成'
        assert by_id[st1]['completed_at'] is not None
        assert by_id[st2]['status'] == 'running'
        assert by_id[st2]['error_message'] is None
        # 归属：会话不存在/不属于当前用户 → 404
        r2 = client.get(f'/ai/chat/sessions/{uuid.uuid4()}/subtasks', headers=hdrs)
        assert r2.status_code == 404
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
        db_conn.commit()


def test_abort_subtask(db_conn, gap_internal_client, monkeypatch):
    """P3-B5：abort 运行中的子代理——best-effort 调 OpenCode（带父会话
    workspace_path 作 directory）、DB 落 failed/'已被用户手动取消'；
    OpenCode 不可达同样 200（幂等 no-op）；非 running → 409，不存在的
    子任务 → 404。"""
    from unittest.mock import MagicMock
    uid = 'user-admin'
    sid = str(uuid.uuid4())
    stid = 'ses_gap_' + uuid.uuid4().hex[:8]
    stid2 = 'ses_gap_' + uuid.uuid4().hex[:8]
    stid3 = 'ses_gap_' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, workspace_path) "
            "VALUES (%s, %s, 'running', '/ws/gap-abort')", (sid, uid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
            "VALUES (%s, %s, 'build', 'running')", (stid, sid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
            "VALUES (%s, %s, 'scan', 'running')", (stid2, sid))
        cur.execute(
            "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
            "VALUES (%s, %s, 'plan', 'completed')", (stid3, sid))
    db_conn.commit()
    aborted = []
    fake_oc = MagicMock()
    fake_oc.abort_session.side_effect = lambda oc, **k: aborted.append((oc, k))
    import routes.ai_chat as _chatmod
    monkeypatch.setattr(_chatmod, 'OpenCodeClient', lambda base_url: fake_oc)
    client, hdrs = gap_internal_client
    try:
        # 运行中：正常取消
        r = client.post(f'/ai/chat/sessions/{sid}/subtasks/{stid}/abort',
                        headers=hdrs)
        assert r.status_code == 200
        assert r.get_json() == {'aborted': True, 'subtaskId': stid}
        assert aborted == [(stid, {'directory': '/ws/gap-abort'})]
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, error_message, completed_at "
                        "FROM ai_chat_subtasks WHERE id=%s", (stid,))
            status, err, completed = cur.fetchone()
        assert status == 'failed'
        assert '手动取消' in (err or '')
        assert completed is not None
        # best-effort：OpenCode 不可达 → 仍 200，DB 照常落终态
        import requests as _rq
        fake_oc.abort_session.side_effect = _rq.ConnectionError('boom')
        r2 = client.post(f'/ai/chat/sessions/{sid}/subtasks/{stid2}/abort',
                         headers=hdrs)
        assert r2.status_code == 200
        assert r2.get_json() == {'aborted': True, 'subtaskId': stid2}
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, error_message FROM ai_chat_subtasks "
                        "WHERE id=%s", (stid2,))
            status2, err2 = cur.fetchone()
        assert status2 == 'failed'
        assert '手动取消' in (err2 or '')
        # 已终态：409 SUBTASK_NOT_RUNNING
        r3 = client.post(f'/ai/chat/sessions/{sid}/subtasks/{stid3}/abort',
                         headers=hdrs)
        assert r3.status_code == 409
        assert r3.get_json()['error']['code'] == 'SUBTASK_NOT_RUNNING'
        # 子任务不存在（或不属于该会话）→ 404
        r4 = client.post(f'/ai/chat/sessions/{sid}/subtasks/ses_gap_nope/abort',
                         headers=hdrs)
        assert r4.status_code == 404
        assert r4.get_json()['code'] == 'SUBTASK_NOT_FOUND'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
        db_conn.commit()


# ---------------------------------------------------------------------------
# C4：effect 自动补偿——mcp_write POST 创建的记录可回退
# ---------------------------------------------------------------------------

def _seed_comp_record(db_conn, coll, rid):
    """补偿用例共用：dynamic_data 里一条待回退记录。"""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data) "
            "VALUES (%s, %s, %s::jsonb)", (rid, coll, json.dumps({'name': 'x'})))
    db_conn.commit()


def _force_effect_status(db_conn, eid, status):
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_execution_effects SET status=%s WHERE id=%s",
                    (status, eid))
    db_conn.commit()


def _cleanup_comp(db_conn, sid, coll, rid):
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_execution_effects WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM dynamic_data WHERE id=%s AND collection=%s",
                    (rid, coll))
    db_conn.commit()


def test_compensate_effect_deletes_record(db_conn, user_id):
    """C4：补偿 mcp_write POST 创建的记录 → dynamic_data 行被删除、
    effect 落 compensated（生产代码首个 compensated 写入点）。"""
    from utils.execution_effect import record_effect, compensate_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    coll = f'gap_comp_{uuid.uuid4().hex[:8]}'
    rid = f'rec-{uuid.uuid4().hex[:8]}'
    try:
        _seed_comp_record(db_conn, coll, rid)
        key = f'eff-comp-{uuid.uuid4().hex[:8]}'
        eff = record_effect(sid, 'mcp_write', key, batch_id=bid,
                            external_ref=json.dumps(
                                {'method': 'POST', 'path': f'/{coll}',
                                 'body': {'id': rid, 'name': 'x'}}))
        assert eff and eff['status'] == 'planned'
        # 登记时即写入补偿依据（此前 external_ref 只在 settle 时 COALESCE）
        assert eff['external_ref'] == json.dumps(
            {'method': 'POST', 'path': f'/{coll}', 'body': {'id': rid, 'name': 'x'}})
        _force_effect_status(db_conn, eff['id'], 'committed')
        out = compensate_effect(eff['id'])
        assert out and out['compensated'] is True
        assert out['collection'] == coll and out['record_id'] == rid
        assert out['deleted'] is True
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM dynamic_data "
                "WHERE id=%s AND collection=%s", (rid, coll))
            assert cur.fetchone()[0] == 0           # 记录已回退
            cur.execute("SELECT status FROM ai_execution_effects WHERE id=%s",
                        (eff['id'],))
            assert cur.fetchone()[0] == 'compensated'
        # 幂等：已 compensated 的 effect 不可再补偿
        assert compensate_effect(eff['id']) is None
    finally:
        _cleanup_comp(db_conn, sid, coll, rid)


def test_compensate_effect_skips_non_post(db_conn, user_id):
    """C4：非 POST 的 effect 不被补偿——PUT 旧值未知、DELETE 无逆操作，
    记录保留、status 保持 committed。"""
    from utils.execution_effect import record_effect, compensate_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    coll = f'gap_comp_{uuid.uuid4().hex[:8]}'
    rid = f'rec-{uuid.uuid4().hex[:8]}'
    try:
        _seed_comp_record(db_conn, coll, rid)
        for method, path in (('PUT', f'/{coll}/{rid}'),
                             ('DELETE', f'/{coll}/{rid}')):
            key = f'eff-skip-{method}-{uuid.uuid4().hex[:8]}'
            eff = record_effect(sid, 'mcp_write', key,
                                external_ref=json.dumps(
                                    {'method': method, 'path': path,
                                     'body': {'id': rid}}))
            _force_effect_status(db_conn, eff['id'], 'committed')
            assert compensate_effect(eff['id']) is None
            with db_conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_execution_effects "
                            "WHERE id=%s", (eff['id'],))
                assert cur.fetchone()[0] == 'committed'
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dynamic_data "
                        "WHERE id=%s AND collection=%s", (rid, coll))
            assert cur.fetchone()[0] == 1           # 记录未被误删
    finally:
        _cleanup_comp(db_conn, sid, coll, rid)


def test_compensate_effect_skips_unknown_and_bad_ref(db_conn, user_id):
    """C4：unknown 禁自动补偿（对齐"禁自动重放"语义）；external_ref 非
    补偿 JSON（旧数据 / 'POST /menus' 旧格式）一律跳过。"""
    from utils.execution_effect import record_effect, compensate_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    coll = f'gap_comp_{uuid.uuid4().hex[:8]}'
    rid = f'rec-{uuid.uuid4().hex[:8]}'
    try:
        _seed_comp_record(db_conn, coll, rid)
        # unknown + 完全可补偿的 POST ref → 仍拒绝
        key = f'eff-unk-{uuid.uuid4().hex[:8]}'
        eff = record_effect(sid, 'mcp_write', key,
                            external_ref=json.dumps(
                                {'method': 'POST', 'path': f'/{coll}',
                                 'body': {'id': rid}}))
        _force_effect_status(db_conn, eff['id'], 'unknown')
        assert compensate_effect(eff['id']) is None
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_execution_effects WHERE id=%s",
                        (eff['id'],))
            assert cur.fetchone()[0] == 'unknown'
        # external_ref 是旧格式（非补偿 JSON dict）→ 跳过
        key2 = f'eff-old-{uuid.uuid4().hex[:8]}'
        eff2 = record_effect(sid, 'mcp_write', key2,
                             external_ref='POST /menus')
        _force_effect_status(db_conn, eff2['id'], 'committed')
        assert compensate_effect(eff2['id']) is None
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dynamic_data WHERE id=%s", (rid,))
            assert cur.fetchone()[0] == 1           # 全程未被误删
    finally:
        _cleanup_comp(db_conn, sid, coll, rid)

