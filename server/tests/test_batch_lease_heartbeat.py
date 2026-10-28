"""执行租约心跳线程与租约失守语义（pending-flip 修复 F3+F4）。

生产缺陷（多进程/发版场景）：子会话租约只在 _await_finished 轮询循环内
续租——claim→send 之间（冷目录 create_session 可达 120s）、子代理收敛等待、
门禁核对等阶段全部裸奔。租约 TTL 默认 90s，一次慢 dispatch 就让租约过期，
另一进程的对账器把仍在工作的会话重排回 pending（状态说谎 + 同回合双发）。

另：旧实现续租失败（执行权被接管）会先 abort OpenCode 会话再抛
_LeaseLost，与 _LeaseLost 处理器注释「不 abort（接管者可能正在复用该
OpenCode 会话）」矛盾——旧 owner 的 abort 会误杀新 owner 刚发出的回合。

锁定契约：
  F4  租约失守 → 静默放手：不 abort、不写终态，行保持 running；
  F3  心跳独立于轮询循环：send_message 阻塞超过租约 TTL 期间，租约仍被
      持续续上，另一实例的对账器因 lease_until 未过期而跳过——会话在
      工作期间绝不被翻成 pending。
"""
import os
import sys
import threading
import time
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
            (uid, f'hb_user_{uid[:8]}', 'x', f'HB User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_running(db_conn, user_id, *, lease_owner, ttl_sec=90, fencing=7):
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'hb-test', 'p', 1)", (bid, user_id))
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
            "   opencode_session_id, fencing_token, lease_owner, lease_until) "
            "VALUES (%s, %s, 'running', %s, 0, 'f.csv', 'oc-hb', %s, %s, "
            "        NOW() + (%s || ' seconds')::interval)",
            (sid, user_id, bid, fencing, lease_owner, str(ttl_sec)))
    db_conn.commit()
    return bid, sid


def _status(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sid,))
        return cur.fetchone()[0]


def test_lease_lost_drops_without_abort(db_conn, user_id, claim_guard,
                                        monkeypatch, tmp_path):
    """F4：续租失败（执行权易主）→ 不 abort、不写终态、行保持 running。"""
    import utils.batch_engine as eng
    bid, sid = _seed_running(db_conn, user_id, lease_owner='other-owner')
    claim_guard(sid)
    monkeypatch.setenv('AI_BATCH_LEASE_TTL_SEC', '3')
    abort_calls = []
    monkeypatch.setattr(eng, 'opencode_client', type('F', (), {
        'create_session': staticmethod(lambda directory='', title='': 'oc-hb'),
        'send_message': staticmethod(lambda *a, **k: None),
        'list_messages': staticmethod(lambda oc, directory='': [
            {'role': 'assistant', 'finished': False, 'finish': None,
             'running_tool': False, 'content': [], 'id': 'm1', 'error': None}]),
        'abort_session': staticmethod(
            lambda *a, **k: abort_calls.append(time.time())),
    })())
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))
    worker = eng.BatchWorker()
    worker._lease_owner = 'hb-owner'
    worker.SESSION_TIMEOUT_SEC = 0
    worker.STALL_TIMEOUT_SEC = 30          # 租约失守先于 STALL 触发
    worker.POLL_INTERVAL_SEC = 0.02
    worker.QUESTION_CHECK_INTERVAL = 999
    # 续租一律失败：模拟执行权已被另一实例接管（fencing/owner 不匹配）
    monkeypatch.setattr(eng.BatchWorker, '_renew_child_lease',
                        staticmethod(lambda *a, **k: False))

    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': 'oc-hb',
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': '', 'fencing_token': 7})

    assert abort_calls == []               # 旧 owner 不得 abort（接管者可能正用）
    assert _status(db_conn, sid) == 'running'   # 不写终态，交给新 owner 收口


def test_heartbeat_keeps_lease_through_slow_dispatch(db_conn, user_id,
                                                     claim_guard, monkeypatch,
                                                     tmp_path):
    """F3：send_message 阻塞超过租约 TTL 期间心跳仍续租——另一实例的对账器
    不得把仍在工作的会话重排回 pending（生产「运行中→待运行」直接成因）。"""
    import utils.batch_engine as eng
    ttl, slow_sec = 4, 6
    bid, sid = _seed_running(db_conn, user_id, lease_owner='hb-owner',
                             ttl_sec=ttl)
    claim_guard(sid)
    monkeypatch.setenv('AI_BATCH_LEASE_TTL_SEC', str(ttl))
    finished_msg = [{'role': 'assistant', 'finished': True, 'finish': 'stop',
                     'running_tool': False,
                     'content': [{'type': 'text', 'text': 'done'}],
                     'id': 'm1', 'error': None}]

    def slow_send(*a, **k):
        time.sleep(slow_sec)               # dispatch 阶段阻塞 > 租约 TTL

    monkeypatch.setattr(eng, 'opencode_client', type('F', (), {
        'create_session': staticmethod(lambda directory='', title='': 'oc-hb'),
        'send_message': staticmethod(slow_send),
        'list_messages': staticmethod(lambda oc, directory='': finished_msg),
        'get_messages': staticmethod(lambda oc, directory='': []),
    })())
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))
    worker = eng.BatchWorker()
    worker._lease_owner = 'hb-owner'
    worker.SESSION_TIMEOUT_SEC = 0
    worker.STALL_TIMEOUT_SEC = 30
    worker.POLL_INTERVAL_SEC = 0.02
    worker.QUESTION_CHECK_INTERVAL = 999

    t0 = time.monotonic()
    runner = threading.Thread(
        target=worker._run_one,
        args=({'id': sid, 'user_id': user_id, 'batch_id': bid,
               'batch_input_file': 'x.csv', 'input_files': None,
               'scan_task_id': None, 'opencode_session_id': 'oc-hb',
               'workspace_path': str(tmp_path), 'continue_prompt': None,
               'agent': '', 'model': '', 'fencing_token': 7},))
    runner.start()
    # 旧实现（无 dispatch 期心跳）此刻租约已过期：对账器会立即重排
    time.sleep(ttl + 0.5)
    other = eng.BatchWorker()              # 另一实例：tracked 为空
    other._reconcile_stale_running()
    runner.join(timeout=60)
    assert not runner.is_alive()

    assert _status(db_conn, sid) == 'completed'
    assert time.monotonic() - t0 >= slow_sec   # 确实经历了慢 dispatch 窗口
