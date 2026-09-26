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
        # ai_batch_events 无 FK，且 event_id 全局唯一——必须随用例回收
        cur.execute(
            "DELETE FROM ai_batch_events WHERE batch_id IN "
            "(SELECT id FROM ai_chat_batches WHERE user_id = %s)", (uid,))
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
