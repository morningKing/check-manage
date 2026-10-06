# -*- coding: utf-8 -*-
"""P1 持久化执行回归（ai-harness-p1 spec §12.1）。

覆盖：lease claim/心跳/接管、恢复决策（unknown effect → needs_review /
lease 未过期跳过）、事件原子 seq 与 afterSeq、outbox 幂等/投递/重放、
命令幂等、预算判定与 claim 阻断、attempt 唯一性与审计事件无丢失、
effect 幂等键。
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'p1_user_{uid[:8]}', 'x', f'P1 User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_execution_budgets WHERE scope_type='batch' "
                    "AND scope_id IN (SELECT id FROM ai_chat_batches WHERE user_id=%s)", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, n=1, *, status='pending'):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'p1-test', 'p', %s)", (bid, user_id, n))
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                "   created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW() - interval '1 hour')",
                (sid, user_id, status, bid, i, f'f{i}.csv'))
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _clear_other_pending(db_conn, keep_bid):
    """M8 治理：仅清理「测试命名」批次的 pending 残留行，不碰真实用户数据。

    专属测试库（casemanage_test，见 tests/conftest.py）上没有真实数据，
    放宽为清理其他批次的**陈旧**（>10 分钟）pending 残留——名称前缀过滤
    是共享 dev 库时代的保守设计，会漏掉 admin API 用例创建的任意命名批次。
    只清陈旧行是 xdist 并行安全的前提：并发 worker 刚种的新鲜行不能碰，
    否则会删掉别的 worker 正在认领/断言的行（本轮已实证）。"""
    from config import DB_CONFIG
    on_test_db = DB_CONFIG.get('dbname') == 'casemanage_test'
    with db_conn.cursor() as cur:
        if on_test_db:
            cur.execute("DELETE FROM ai_chat_sessions s USING ai_chat_batches b "
                        "WHERE s.batch_id = b.id AND s.status='pending' "
                        "  AND b.id <> %s "
                        "  AND s.created_at < NOW() - interval '10 minutes'",
                        (keep_bid,))
        else:
            cur.execute("DELETE FROM ai_chat_sessions s USING ai_chat_batches b "
                        "WHERE s.batch_id = b.id AND s.status='pending' "
                        "  AND b.id <> %s "
                        "  AND (b.name LIKE 'AITEST-%%' OR b.name IN ('p0-test', "
                        "       'p1-test', 'engine-test', 'pause-test', 'gap-test'))",
                        (keep_bid,))
    db_conn.commit()


def _set_child(db_conn, sid, **cols):
    sets = ', '.join(f'{k} = %s' for k in cols)
    with db_conn.cursor() as cur:
        cur.execute(f"UPDATE ai_chat_sessions SET {sets} WHERE id = %s",
                    (*cols.values(), sid))
    db_conn.commit()


# ---------------------------------------------------------------------------
# 1. lease：claim 写租约 + fencing 递增 + 过期接管
# ---------------------------------------------------------------------------

def test_claim_sets_lease_and_increments_fencing(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    w._lease_owner = 'p1-owner-A'
    claimed = w._claim_pending_sessions(limit=1)
    row = claimed[0]
    assert row['lease_owner'] == 'p1-owner-A'
    assert row['lease_until'] is not None
    assert row['fencing_token'] == 1
    # 再 claim（先回 pending 模拟重排）→ fencing 递增
    _set_child(db_conn, row['id'], status='pending')
    claimed2 = w._claim_pending_sessions(limit=1)
    assert claimed2[0]['fencing_token'] == 2


def test_expired_lease_taken_over_by_new_owner(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='pending', "
                    "fencing_token=5, lease_owner='old-owner', "
                    "lease_until = NOW() - interval '10 seconds' WHERE id=%s",
                    (sids[0],))
    db_conn.commit()
    w = BatchWorker()
    w._lease_owner = 'new-owner-B'
    claimed = w._claim_pending_sessions(limit=1)
    assert claimed[0]['lease_owner'] == 'new-owner-B'
    assert claimed[0]['fencing_token'] == 6  # 接管时 fencing 递增


def test_child_heartbeat_renewal(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    w = BatchWorker()
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], lease_owner='hb-owner', fencing_token=3)
    assert BatchWorker._renew_child_lease(sids[0], 'hb-owner', 3) is True
    # owner/fencing 不匹配 → False
    assert BatchWorker._renew_child_lease(sids[0], 'other', 3) is False
    assert BatchWorker._renew_child_lease(sids[0], 'hb-owner', 99) is False


# ---------------------------------------------------------------------------
# 2. 恢复决策（spec §4.2）
# ---------------------------------------------------------------------------

def test_reconcile_skips_unexpired_lease(db_conn, user_id, monkeypatch):
    """lease 未过期 → 等待（不重排、不误杀）。"""
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], status='running', lease_owner='someone-else',
               lease_until=datetime.now() + timedelta(seconds=60))
    w = BatchWorker()
    w._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sids[0],))
        assert cur.fetchone()[0] == 'running'  # 未被动过


def test_reconcile_unknown_effect_needs_review(db_conn, user_id, monkeypatch):
    """存在 unknown 副作用 → needs_review（禁止自动重放）。"""
    from utils.batch_engine import BatchWorker
    from utils import execution_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], status='running', lease_owner='gone',
               lease_until=datetime.now() - timedelta(seconds=60),
               opencode_session_id='oc-x', execution_generation=2)
    eff = execution_effect.record_effect(sids[0], 'mcp_write', 'k1',
                                         batch_id=bid)
    execution_effect.settle_effect(eff['id'], 'unknown')
    w = BatchWorker()
    w._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions "
                    "WHERE id=%s", (sids[0],))
        status, err = cur.fetchone()
    assert status == 'needs_review'
    assert '人工复核' in (err or '')


def test_reconcile_alive_session_requeues_continue(db_conn, user_id, monkeypatch):
    """lease 过期 + oc 会话活着 + 有进度证据（消息）→ 原地续跑。
    （批次 2 起：无 checkpoint/消息/副作用的裸场景改走 failed(retryable)，
    见 test_reconcile_no_progress_evidence_fails。）"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], status='running', lease_owner='gone',
               lease_until=datetime.now() - timedelta(seconds=60),
               opencode_session_id='oc-alive', execution_generation=1)
    # 进度证据：至少一条已持久化消息（对账器据此选择续跑而非失败）
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s)",
            (f'{sids[0]}:asst1', sids[0], '[{"type":"text","text":"进行中"}]'))
    db_conn.commit()
    fake = MagicMock()
    fake.get_messages.return_value = [{'info': {'role': 'assistant'}}]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    w = BatchWorker()
    w._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, continue_prompt, execution_generation "
                    "FROM ai_chat_sessions WHERE id=%s", (sids[0],))
        status, cp, gen = cur.fetchone()
    assert status == 'pending'
    assert cp and '继续' in cp
    assert gen == 2  # 重排换代（写回校验用），continue 语义不变


def test_reconcile_no_progress_evidence_fails(db_conn, user_id, monkeypatch):
    """批次 2（P1 §4.2 末行）：lease 过期 + oc 存活 + 无 checkpoint/消息/
    副作用 → failed(retryable)，不再盲目原地续跑（无进度证据的静默重放
    是 spec 明确禁止的）。"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], status='running', lease_owner='gone',
               lease_until=datetime.now() - timedelta(seconds=60),
               opencode_session_id='oc-noproof', execution_generation=1)
    fake = MagicMock()
    fake.get_messages.return_value = [{'info': {'role': 'assistant'}}]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    BatchWorker()._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM ai_chat_sessions "
                    "WHERE id=%s", (sids[0],))
        status, err = cur.fetchone()
    assert status == 'failed'
    assert '无任何进度证据' in (err or '')


# ---------------------------------------------------------------------------
# 2.5 故障注入（P1 §12.2）：send 后 kill / 投递中 kill / reconcile OC 不可达
# ---------------------------------------------------------------------------

def test_fi_send_crash_after_delivery_requeues_with_continue(
        db_conn, user_id, monkeypatch, tmp_path):
    """send_message 成功后 executor 崩溃（轮询阶段抛 _TurnFailed/异常）→
    自动重试换代续跑，不是静默丢失（spec §12.2「请求已送达但客户端超时」）。"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    root = tmp_path
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))
    # 真实落暂存文件（p1 的 _seed_batch 不带 root 参数）
    import os as _os
    _rel = f'batch-staging/{user_id}/u1/f0.csv'
    _dest = _os.path.join(str(root), _rel)
    _os.makedirs(_os.path.dirname(_dest), exist_ok=True)
    with open(_dest, 'w', encoding='utf-8') as _fh:
        _fh.write('content-0')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET batch_input_file=%s WHERE id=%s",
                    (_rel, sids[0]))
    _set_child(db_conn, sids[0], status='pending')
    _clear_other_pending(db_conn, bid)
    fake = MagicMock()
    fake.create_session.return_value = 'oc-fi-1'
    # dispatch 成功；轮询阶段连接断掉（模拟「请求已送达但客户端超时/崩溃」，
    # spec §12.2）——RequestException 属可重试类 → auto-retry 换代续跑
    fake.send_message.return_value = {'id': 'm1'}
    fake.list_messages.return_value = [{'info': {'role': 'assistant'}}]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    w = BatchWorker()
    monkeypatch.setattr(w, 'MAX_AUTO_RETRY', 3)
    monkeypatch.setattr(
        w, '_await_finished',
        lambda *a, **k: (_ for _ in ()).throw(
            __import__('requests').exceptions.ConnectionError('client died')))
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])   # 不应向外抛：auto-retry 吸收
    # auto-retry 已换代重排（pending + retry_count≥1）
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, retry_count FROM ai_chat_sessions "
                    "WHERE id=%s", (sids[0],))
        row = cur.fetchone()
    assert row[0] in ('pending', 'running'), row
    assert row[1] >= 1


def test_fi_delivery_crash_leaves_sending_row_reclaimed(db_conn, user_id,
                                                        monkeypatch):
    """投递中 kill 进程 → 行停留 sending；重投递循环按 M12 回收语义收敛
    （最终 delivered），不永久卡死。"""
    from utils import delivery_outbox
    bid, sids = _seed_batch(db_conn, user_id, 1)
    oid = 'obx-fi-' + uuid.uuid4().hex[:8]
    eid = 'evt-fi-' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, event_type, "
            "  target_url, payload, signature, idempotency_key, status, "
            "  next_retry_at, attempt_count) "
            "VALUES (%s, %s, %s, 't', 'http://cb/x', '{}', '', %s, 'sending', "
            "  NOW() - interval '20 minutes', 3)", (oid, eid, bid, f'k-{eid}'))
    db_conn.commit()
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: True)
    delivery_outbox.drain_due_once()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_delivery_outbox WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == 'delivered'
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_delivery_outbox WHERE id=%s", (oid,))
    db_conn.commit()


def test_fi_reconcile_skips_when_opencode_down(db_conn, user_id, monkeypatch):
    """对账期间 OpenCode 整体不可达 → 跳过本轮（不批量误杀），下一轮再收口。"""
    import requests as _rq
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _set_child(db_conn, sids[0], status='running', lease_owner='gone',
               lease_until=datetime.now() - timedelta(seconds=60),
               opencode_session_id='oc-down', execution_generation=1)
    fake = MagicMock()
    fake.get_messages.side_effect = _rq.exceptions.ConnectionError('down')
    monkeypatch.setattr(eng, 'opencode_client', fake)
    BatchWorker()._reconcile_stale_running()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sids[0],))
        assert cur.fetchone()[0] == 'running'   # 未被误杀


# ---------------------------------------------------------------------------
# 3. 事件流：原子 seq + afterSeq
# ---------------------------------------------------------------------------

def test_events_concurrent_append_no_loss(db_conn, user_id):
    """并发写同一批次：seq 严格单调、无冲突无丢失（P1-5 根因修复）。"""
    from concurrent.futures import ThreadPoolExecutor
    from utils import batch_events
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    with ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(lambda i: batch_events.append_event(
            bid, 'child.status', aggregate_type='child',
            payload={'i': i}), range(24)))
    assert all(ids)
    rows = batch_events.read_events(bid, after_seq=0, limit=100)
    seqs = [r['event_seq'] for r in rows]
    assert len(seqs) == 24
    assert seqs == sorted(seqs) and len(set(seqs)) == 24  # 单调且唯一


def test_events_after_seq_semantics(db_conn, user_id):
    from utils import batch_events
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    for i in range(5):
        batch_events.append_event(bid, 'child.status', payload={'i': i})
    page = batch_events.read_events(bid, after_seq=2, limit=2)
    assert [r['event_seq'] for r in page] == [3, 4]


# ---------------------------------------------------------------------------
# 4. outbox：幂等入队 / 投递 / 重放
# ---------------------------------------------------------------------------

def test_outbox_enqueue_idempotent(db_conn, user_id):
    from utils import delivery_outbox
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    evt = f'evt-1-{uuid.uuid4().hex[:8]}'
    o1 = delivery_outbox.enqueue(bid, target_url='http://cb.example/hook',
                                 payload={'a': 1}, secret='s', event_id=evt)
    o2 = delivery_outbox.enqueue(bid, target_url='http://cb.example/hook',
                                 payload={'a': 1}, secret='s', event_id=evt)
    assert o1
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_delivery_outbox "
                    "WHERE batch_id=%s", (bid,))
        assert cur.fetchone()[0] == 1  # (event_id, target_url) 去重


def test_outbox_delivery_and_replay(db_conn, user_id, monkeypatch):
    from utils import delivery_outbox
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    delivery_outbox.enqueue(bid, target_url='http://cb.example/hook',
                            payload={'a': 1}, secret='s', event_id=f'evt-d1-{uuid.uuid4().hex[:8]}')
    # 退避归零：连续 drain 不被 next_retry_at 挡住；前两次投递失败
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one',
                        lambda row: row['attempt_count'] >= 2)
    delivery_outbox.drain_due_once()   # attempt 1 → failed
    delivery_outbox.drain_due_once()   # attempt 2 → failed
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, attempt_count FROM ai_delivery_outbox "
                    "WHERE batch_id=%s", (bid,))
        status, n = cur.fetchone()
    assert status == 'failed'
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: True)
    delivery_outbox.drain_due_once()   # 退避已到 → delivered
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_delivery_outbox WHERE batch_id=%s",
                    (bid,))
        assert cur.fetchone()[0] == 'delivered'


def test_outbox_dead_letter_and_manual_replay(db_conn, user_id, monkeypatch):
    from utils import delivery_outbox
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    delivery_outbox.enqueue(bid, target_url='http://cb.example/dead',
                            payload={}, secret='', event_id=f'evt-dead-{uuid.uuid4().hex[:8]}')
    monkeypatch.setattr(delivery_outbox, 'BACKOFF_SECONDS', [0, 0, 0, 0, 0])
    monkeypatch.setattr(delivery_outbox, 'deliver_one', lambda row: False)
    for _ in range(delivery_outbox.MAX_ATTEMPTS):
        delivery_outbox.drain_due_once()
    rows = delivery_outbox.list_outbox(bid)
    assert rows[0]['status'] == 'dead_letter'
    assert rows[0]['attempt_count'] == delivery_outbox.MAX_ATTEMPTS
    assert delivery_outbox.replay_dead_letter(rows[0]['id'])
    rows = delivery_outbox.list_outbox(bid)
    assert rows[0]['status'] == 'pending' and rows[0]['attempt_count'] == 0


# ---------------------------------------------------------------------------
# 5. 命令幂等（spec §4.4）
# ---------------------------------------------------------------------------

def test_command_idempotency(db_conn, user_id):
    from utils import execution_commands
    bid, _sids = _seed_batch(db_conn, user_id, 1)
    c1 = execution_commands.submit_command('pause', batch_id=bid,
                                           requested_by='u1',
                                           idempotency_key='idem-1')
    c2 = execution_commands.submit_command('pause', batch_id=bid,
                                           requested_by='u1',
                                           idempotency_key='idem-1')
    assert c1['id'] == c2['id'] and c2['duplicate'] is True
    execution_commands.finish_command(c1['id'], 'applied',
                                      result_snapshot={'status': 'paused'})
    got = execution_commands.get_command(c1['id'])
    assert got['status'] == 'applied'
    with pytest.raises(ValueError):
        execution_commands.submit_command('explode', batch_id=bid)


# ---------------------------------------------------------------------------
# 6. 预算（spec §9）
# ---------------------------------------------------------------------------

def test_budget_evaluate_and_claim_block(db_conn, user_id):
    from utils import execution_budget
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    execution_budget.upsert_budget('batch', bid, max_tokens=100,
                                   on_exceed='drain')
    # 无 usage → 不超限 → 可 claim
    assert execution_budget.batch_budget_blocks_claim(bid) is False
    execution_budget.accumulate_usage(
        sids[0], batch_id=bid,
        usage={'tokensInput': 80, 'tokensOutput': 50, 'cost': 1.0})
    verdict = execution_budget.evaluate_batch_budget(bid)
    assert verdict['exceeded'] and verdict['dimension'] == 'tokens'
    assert verdict['action'] == 'drain'
    # 事件可观测
    from utils import batch_events
    evs = batch_events.read_events(bid, after_seq=0)
    assert any(e['event_type'] == 'budget.exceeded' for e in evs)
    # drain：dispatcher 不再认领该批次的 pending
    _set_child(db_conn, sids[0], status='pending')
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    w._lease_owner = 'p1-budget'
    assert w._claim_pending_sessions(limit=1) == []


# ---------------------------------------------------------------------------
# 7. attempt 唯一性 + 审计事件无丢失
# ---------------------------------------------------------------------------

def test_attempt_running_unique_per_session(db_conn, user_id):
    import psycopg2
    from utils import execution_audit
    bid, sids = _seed_batch(db_conn, user_id, 1)
    a1 = execution_audit.create_attempt(sids[0], source_type='batch')
    a2 = execution_audit.create_attempt(sids[0], source_type='batch')
    # 第二个 running attempt 违反部分唯一索引 → _safe 吞掉返回 None
    assert a1 and a2 is None
    execution_audit.finish_latest_running(sids[0], 'completed')
    a3 = execution_audit.create_attempt(sids[0], source_type='batch')
    assert a3  # 收口后可再建


def test_attempt_events_survive_concurrency(db_conn, user_id):
    """并发写同一 attempt 的事件：无丢失（FOR UPDATE 串行化，P1-5）。"""
    from concurrent.futures import ThreadPoolExecutor
    from utils import execution_audit
    bid, sids = _seed_batch(db_conn, user_id, 1)
    attempt = execution_audit.create_attempt(sids[0], source_type='batch')
    assert attempt
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda i: execution_audit.record_event(
            attempt, 'dispatch.ok', payload={'i': i}), range(12)))
    evs = execution_audit.get_events(attempt, limit=100)
    seqs = [e['event_seq'] for e in evs]
    assert len(seqs) == 12 and seqs == sorted(seqs) and len(set(seqs)) == 12


# ---------------------------------------------------------------------------
# 8. effect 幂等（spec §4.3）
# ---------------------------------------------------------------------------

def test_effect_key_dedup_and_unknown(db_conn, user_id):
    from utils import execution_effect
    bid, sids = _seed_batch(db_conn, user_id, 1)
    e1 = execution_effect.record_effect(sids[0], 'callback', 'cb-1',
                                        batch_id=bid)
    e2 = execution_effect.record_effect(sids[0], 'callback', 'cb-1',
                                        batch_id=bid)
    assert e1['id'] == e2['id']  # 同 key 复用同一行
    assert execution_effect.settle_effect(e1['id'], 'committed')
    # 终态后 settle 不再改
    assert not execution_effect.settle_effect(e1['id'], 'unknown')
    assert not execution_effect.has_unknown_effects(sids[0])
    e3 = execution_effect.record_effect(sids[0], 'mcp_write', 'k-unknown')
    assert execution_effect.settle_effect(e3['id'], 'unknown')
    assert execution_effect.has_unknown_effects(sids[0])


# ---------------------------------------------------------------------------
# 9. checkpoint（spec §5.3）
# ---------------------------------------------------------------------------

def test_checkpoint_latest_swap(db_conn, user_id):
    from utils import execution_checkpoint
    bid, sids = _seed_batch(db_conn, user_id, 1)
    c1 = execution_checkpoint.write_checkpoint(sids[0],
                                               checkpoint_type='dispatch',
                                               execution_generation=1)
    c2 = execution_checkpoint.write_checkpoint(sids[0],
                                               checkpoint_type='turn_complete',
                                               execution_generation=1)
    latest = execution_checkpoint.latest_checkpoint(sids[0])
    assert latest['id'] == c2
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_execution_checkpoints "
                    "WHERE session_id=%s AND is_latest", (sids[0],))
        assert cur.fetchone()[0] == 1
