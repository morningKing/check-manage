# -*- coding: utf-8 -*-
"""P0 执行安全基线回归（ai-harness-p0 spec §11.1/§12）。

覆盖：
  - ownership：非终态批子会话走普通发送 → 409 BATCH_SESSION_CONTROLLED；
    他人会话 → 404；终态走内部 continue → 202；
  - turn：claim 建 turn、部分唯一索引保证单 active turn、终态收口 turn；
  - CAS：_mark_done 只计一次；stale generation 写回被拒；cancel > pause；
  - generation：reexecute/continue 递增；旧代数消息不落库；
  - 删除：Open API 非终态 DELETE 409 / 终态删除；
  - 门禁 fail-closed：登记异常 → failed；apply_to 校验与全链路过滤；
  - F5 paused 单独取消、F6 batch_seq 唯一、F7 retry 重算含 paused、
    F8 软删口径、F9 attempt 收口、F12 retention 单注册、lease 单实例。
"""
import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token, hash_api_key  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'p0_user_{uid[:8]}', 'x', f'P0 User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _clear_other_pending(db_conn, keep_bid):
    """认领是全局的（共享开发库）：清掉本批次之外的 pending 残留行，
    保证种子被确定性认领（同 test_batch_engine 的做法）。"""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions "
                    "WHERE status='pending' AND batch_id IS NOT NULL "
                    "  AND batch_id <> %s", (keep_bid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, n=1, *, batch_checks=None, root=None):
    """root 传入时（_run_one 全链路用例），真实落暂存文件——否则
    _prepare_workspace 以 FileNotFoundError 快速失败。"""
    import json as _json
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, action_checks) "
            "VALUES (%s, %s, 'p0-test', 'p', %s, %s)",
            (bid, user_id, n,
             _json.dumps(batch_checks) if batch_checks else None),
        )
        for i in range(n):
            sid = str(uuid.uuid4())
            if root is not None:
                rel = f'batch-staging/{user_id}/u1/f{i}.csv'
                dest = os.path.join(str(root), rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'w', encoding='utf-8') as fh:
                    fh.write(f'content-{i}')
            else:
                rel = f'f{i}.csv'
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, i, rel),
            )
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _set_child(db_conn, sid, **cols):
    sets = ', '.join(f'{k} = %s' for k in cols)
    with db_conn.cursor() as cur:
        cur.execute(f"UPDATE ai_chat_sessions SET {sets} WHERE id = %s",
                    (*cols.values(), sid))
    db_conn.commit()


def _child(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, execution_generation, active_turn_id, "
                    "gate_status, cancel_requested, pause_requested, "
                    "continue_prompt, error_message "
                    "FROM ai_chat_sessions WHERE id = %s", (sid,))
        r = cur.fetchone()
    if not r:
        return None
    keys = ('status', 'generation', 'active_turn_id', 'gate_status',
            'cancel_requested', 'pause_requested', 'continue_prompt',
            'error_message')
    return dict(zip(keys, r))


def _batch(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, done, failed, total FROM ai_chat_batches "
                    "WHERE id = %s", (bid,))
        return cur.fetchone()


def _fake_opencode(monkeypatch, finished_text='done'):
    """MagicMock 版 OpenCode facade：一轮即完成。"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    fake = MagicMock()
    fake.create_session.return_value = 'oc-' + uuid.uuid4().hex[:8]
    fake.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]
    fake.send_message.return_value = {'id': 'msg-1'}
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': finished_text}]}]
    fake.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake)
    return fake


def _rebind_real_db():
    """把所有已导入模块的 get_db 重绑回真实实现（同 test_open_api_batch_integration）。"""
    import db as _db_module
    _db_module.pool = None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass


# ---------------------------------------------------------------------------
# 1. ownership：普通发送入口的批子会话门禁（P0-1）
# ---------------------------------------------------------------------------

@pytest.fixture
def chat_client(db_conn):
    _rebind_real_db()
    uid = 'test-user-p0-owner'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, role, display_name) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (uid, 'p0-owner-test', 'x', 'admin', 'p0-owner'),
        )
    db_conn.commit()
    from app import app
    app.config['TESTING'] = True
    token = create_token({'id': uid, 'username': 'p0-owner', 'role': 'admin'})
    yield app.test_client(), {'Authorization': f'Bearer {token}'}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_send_message_rejected_on_running_batch_child(db_conn, chat_client):
    """运行中批子会话走普通发送 → 409 BATCH_SESSION_CONTROLLED，无消息落库。"""
    client, headers = chat_client
    uid = 'test-user-p0-owner'
    bid, sids = _seed_batch(db_conn, uid, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='running')
    r = client.post(f'/ai/chat/sessions/{sid}/messages',
                    json={'content': '插队干活'}, headers=headers)
    assert r.status_code == 409
    assert r.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 0  # 未写消息


def test_send_message_rejected_on_pending_and_paused_child(db_conn, chat_client):
    client, headers = chat_client
    uid = 'test-user-p0-owner'
    bid, sids = _seed_batch(db_conn, uid, 2)
    _set_child(db_conn, sids[0], status='pending')
    _set_child(db_conn, sids[1], status='paused')
    for sid in sids:
        r = client.post(f'/ai/chat/sessions/{sid}/messages',
                        json={'content': 'x'}, headers=headers)
        assert r.status_code == 409
        assert r.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'


def test_send_message_other_users_session_404(chat_client):
    client, headers = chat_client
    r = client.post('/ai/chat/sessions/no-such-session/messages',
                    json={'content': 'x'}, headers=headers)
    assert r.status_code == 404


def test_internal_continue_endpoint(db_conn, chat_client):
    """终态子会话走内部 continue 端点 → 202 + pending + continue_prompt +
    generation 递增；非终态 → 409。"""
    client, headers = chat_client
    uid = 'test-user-p0-owner'
    bid, sids = _seed_batch(db_conn, uid, 2)
    done_sid, running_sid = sids
    _set_child(db_conn, done_sid, status='completed')
    _set_child(db_conn, running_sid, status='running')

    r = client.post(f'/ai/chat/batches/{bid}/sessions/{done_sid}/continue',
                    json={'prompt': '补充一下结论'}, headers=headers)
    assert r.status_code == 202
    row = _child(db_conn, done_sid)
    assert row['status'] == 'pending'
    assert row['continue_prompt'] == '补充一下结论'
    assert row['generation'] == 1

    r2 = client.post(f'/ai/chat/batches/{bid}/sessions/{running_sid}/continue',
                     json={'prompt': 'x'}, headers=headers)
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# 2. turn：单 active turn + claim 建 turn + 终态收口
# ---------------------------------------------------------------------------

def test_claim_creates_turn_and_closes_stale(user_id, db_conn):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_turns (id, session_id, batch_id, user_id, "
            "client_request_id, operation, status) "
            "VALUES ('turn-stale-'||%s, %s, %s, %s, 'stale-1', 'send', 'running')",
            (uuid.uuid4().hex[:8], sid, bid, user_id))
    db_conn.commit()
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    assert len(claimed) == 1
    assert claimed[0]['turn_id']
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_turns "
                    "WHERE session_id=%s AND status='cancelled'", (sid,))
        assert cur.fetchone()[0] == 'cancelled'
        cur.execute("SELECT count(*) FROM ai_chat_turns WHERE session_id=%s "
                    "AND status IN ('accepted','running','recovering')", (sid,))
        assert cur.fetchone()[0] == 1  # 关旧建新 → 恰一个 active
        cur.execute("SELECT active_turn_id FROM ai_chat_sessions WHERE id=%s", (sid,))
        assert cur.fetchone()[0] == claimed[0]['turn_id']


def test_active_turn_unique_index_blocks_second(user_id, db_conn):
    """数据库级保证：同会话第二个 active turn 直接违反部分唯一索引。"""
    import psycopg2
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    cur = db_conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_turns (id, session_id, user_id, client_request_id, "
        "operation, status) VALUES ('t1-'||%s, %s, %s, 'c1', 'send', 'running')",
        (uuid.uuid4().hex[:8], sid, user_id))
    with pytest.raises(psycopg2.errors.IntegrityError):
        cur.execute(
            "INSERT INTO ai_chat_turns (id, session_id, user_id, "
            "client_request_id, operation, status) "
            "VALUES ('t2-'||%s, %s, %s, 'c2', 'send', 'running')",
            (uuid.uuid4().hex[:8], sid, user_id))
    db_conn.rollback()


def test_mark_done_closes_turn(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._mark_done(claimed[0]['id'], bid, last_preview='ok',
                 generation=claimed[0]['execution_generation'])
    row = _child(db_conn, claimed[0]['id'])
    assert row['status'] == 'completed'
    assert row['active_turn_id'] is None
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_turns WHERE id=%s",
                    (claimed[0]['turn_id'],))
        assert cur.fetchone()[0] == 'completed'


# ---------------------------------------------------------------------------
# 3. CAS：终态只写一次、计数不重复、stale 写回被拒（P0-2）
# ---------------------------------------------------------------------------

def test_mark_done_counted_exactly_once(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    gen = claimed[0]['execution_generation']
    sid = claimed[0]['id']
    w._mark_done(sid, bid, last_preview='ok', generation=gen)
    w._mark_done(sid, bid, last_preview='ok', generation=gen)  # 并发重复收口
    assert _batch(db_conn, bid)[1] == 1  # done 只 +1
    assert _child(db_conn, sid)['status'] == 'completed'


def test_stale_generation_write_rejected(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    sid = claimed[0]['id']
    old_gen = claimed[0]['execution_generation']
    _set_child(db_conn, sid, status='pending', execution_generation=old_gen + 1)
    _set_child(db_conn, sid, status='running')
    # 旧 generation 的写回必须 0 行：状态/计数都不变
    w._mark_done(sid, bid, last_preview='stale', generation=old_gen)
    row = _child(db_conn, sid)
    assert row['status'] == 'running'
    assert row['generation'] == old_gen + 1
    assert _batch(db_conn, bid)[1] == 0
    w._mark_done(sid, bid, last_preview='fresh', generation=old_gen + 1)
    assert _batch(db_conn, bid)[1] == 1


def test_pause_write_blocked_when_cancel_flag_set(db_conn, user_id):
    """cancel 优先于 pause（spec §4.2）：pause 的 CAS 要求 cancel 未置位。"""
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    sid = claimed[0]['id']
    gen = claimed[0]['execution_generation']
    _set_child(db_conn, sid, cancel_requested=True, pause_requested=True)
    w._mark_paused(sid, bid, generation=gen)
    assert _child(db_conn, sid)['status'] == 'running'
    w._mark_cancelled(sid, bid, generation=gen)
    row = _child(db_conn, sid)
    assert row['status'] == 'cancelled'
    assert _batch(db_conn, bid)[2] == 1  # cancelled 计入 failed


def test_mark_done_redirects_to_pause_flag(db_conn, user_id):
    """done 写回时若 pause 标志已在竞态窗口置位 → 按控制标志重定向落 paused。"""
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    sid = claimed[0]['id']
    gen = claimed[0]['execution_generation']
    _set_child(db_conn, sid, pause_requested=True)
    w._mark_done(sid, bid, last_preview='late', generation=gen)
    assert _child(db_conn, sid)['status'] == 'paused'
    assert _batch(db_conn, bid)[1] == 0  # 不计 done


# ---------------------------------------------------------------------------
# 4. generation 语义
# ---------------------------------------------------------------------------

def test_reexecute_and_continue_bump_generation(db_conn, user_id):
    from utils.batch_repo import reexecute_child, continue_child
    bid, sids = _seed_batch(db_conn, user_id, 2)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE id=%s",
                    (sids[0],))
        cur.execute("UPDATE ai_chat_sessions SET status='failed', error_message='e' "
                    "WHERE id=%s", (sids[1],))
        cur.execute("UPDATE ai_chat_batches SET done=1, failed=1 WHERE id=%s", (bid,))
    db_conn.commit()
    reexecute_child(user_id, bid, sids[0])
    continue_child(user_id, bid, sids[1], '继续')
    assert _child(db_conn, sids[0])['generation'] == 1
    assert _child(db_conn, sids[1])['generation'] == 1


def test_persist_skipped_for_stale_generation(db_conn, user_id, monkeypatch):
    """旧 turn 的消息不落入换代后的会话（spec §6.3 消息防线）。"""
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _fake_opencode(monkeypatch)
    _set_child(db_conn, sid, status='running', execution_generation=5,
               workspace_path='/tmp/nowhere')
    w = BatchWorker()
    w._persist_conversation(sid, 'old prompt', 'oc-x', None, generation=3)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 0
    _set_child(db_conn, sid, execution_generation=3)
    w._persist_conversation(sid, 'new prompt', 'oc-x', None, generation=3)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        # user 消息 + assistant 兜底行（fake get_messages 为空时的 final fallback）
        assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 5. Open API 删除保护（P0-3）与 actionChecks 校验（F4）
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(db_conn):
    _rebind_real_db()
    uid = str(uuid.uuid4())
    key_id = 'ak-p0-' + uuid.uuid4().hex[:8]
    raw_key = 'p0k_' + uuid.uuid4().hex
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'p0_api_{uid[:8]}', 'x', 'P0 API'),)
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash, is_active, owner_user_id) "
            "VALUES (%s, 'p0', %s, TRUE, %s)",
            (key_id, hash_api_key(raw_key), uid))
    db_conn.commit()
    from app import app
    app.config['TESTING'] = True
    client = app.test_client()
    yield client, {'X-API-Key': raw_key}, uid, key_id
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_openapi_delete_nonterminal_rejected(api_client, db_conn):
    client, headers, uid, key_id = api_client
    bid, sids = _seed_batch(db_conn, uid, 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET api_key_id=%s WHERE id=%s",
                    (key_id, bid))
        cur.execute("UPDATE ai_chat_sessions SET status='running' WHERE id=%s",
                    (sids[0],))
    db_conn.commit()
    r = client.delete(f'/v1/ai-batches/{bid}', headers=headers)
    assert r.status_code == 409
    assert r.get_json()['error']['code'] == 'BATCH_NOT_TERMINAL'
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] == 1  # 未删


def test_openapi_delete_terminal_ok(api_client, db_conn):
    client, headers, uid, key_id = api_client
    bid, _sids = _seed_batch(db_conn, uid, 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET api_key_id=%s, status='completed' "
                    "WHERE id=%s", (key_id, bid))
    db_conn.commit()
    r = client.delete(f'/v1/ai-batches/{bid}', headers=headers)
    assert r.status_code == 200
    assert r.get_json() == {'deleted': True}


def test_openapi_create_invalid_action_checks_400(api_client, db_conn):
    """F4：非法 actionChecks → 400 ACTION_CHECK_INVALID，不落库。"""
    client, headers, uid, key_id = api_client
    r = client.post('/v1/ai-batches', headers=headers, json={
        'name': 'bad-checks', 'prompt': 'p', 'files': [],
        'actionChecks': [{'name': 'bad', 'tool': 'w', 'args_pattern': '('}],
    })
    assert r.status_code == 400
    assert r.get_json()['error']['code'] == 'ACTION_CHECK_INVALID'
    r = client.get('/v1/ai-batches', headers=headers)
    assert all(b['name'] != 'bad-checks' for b in r.get_json()['items'])


def test_openapi_patch_action_checks_validated(api_client, db_conn):
    """PATCH actionChecks：合法更新落库；非法 400。"""
    import json as _json
    client, headers, uid, key_id = api_client
    bid, _sids = _seed_batch(db_conn, uid, 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET api_key_id=%s WHERE id=%s",
                    (key_id, bid))
    db_conn.commit()
    good = [{'name': 'c1', 'tool': 'w', 'args_pattern': 'x'}]
    r = client.patch(f'/v1/ai-batches/{bid}', headers=headers,
                     json={'actionChecks': good})
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT action_checks FROM ai_chat_batches WHERE id=%s", (bid,))
        stored = cur.fetchone()[0]
    assert stored[0]['name'] == 'c1' and stored[0]['args_pattern'] == 'x'
    assert stored[0]['apply_to'] is None  # 规范化输出保留 apply_to 键
    r = client.patch(f'/v1/ai-batches/{bid}', headers=headers,
                     json={'actionChecks': [{'name': 'c', 'tool': 'w',
                                             'args_pattern': '('}]})
    assert r.status_code == 400
    assert r.get_json()['error']['code'] == 'ACTION_CHECK_INVALID'


# ---------------------------------------------------------------------------
# 6. 门禁 fail-closed（F3）与 apply_to 全链路（F2）
# ---------------------------------------------------------------------------

def test_gate_registration_failure_returns_error(db_conn, user_id):
    """登记异常可见（不再静默吞）：error 非空 → 调用方 fail-closed。"""
    from utils.batch_engine import BatchWorker
    bad = [{'name': 'c1', 'tool': 'w', 'args_pattern': '('}]  # 非法正则
    bid, sids = _seed_batch(db_conn, user_id, 1, batch_checks=bad)
    w = BatchWorker()
    reg = w._register_action_expectations(sids[0], bid, {'batch_seq': 0})
    assert reg['error']
    assert reg['registered'] == 0


def test_run_one_fail_closed_on_broken_checks(db_conn, user_id, monkeypatch,
                                              tmp_path):
    """端到端：批上存了非法 checks（绕过入口校验直接落库）→ 子任务不
    completed，落 failed（gate_status=inconclusive），错误含『门禁无法证实』。"""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    root = tmp_path
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))
    bad = [{'name': 'c1', 'tool': 'w', 'args_pattern': '('}]
    bid, sids = _seed_batch(db_conn, user_id, 1, batch_checks=bad, root=tmp_path)
    _fake_opencode(monkeypatch)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])
    row = _child(db_conn, claimed[0]['id'])
    assert row['status'] == 'failed'
    assert row['gate_status'] == 'inconclusive'
    assert '门禁无法证实' in (row['error_message'] or '')


def test_run_one_completes_without_checks(db_conn, user_id, monkeypatch, tmp_path):
    """无生效 checks → 门禁 skipped，正常 completed。"""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    root = tmp_path
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))
    bid, sids = _seed_batch(db_conn, user_id, 1, batch_checks=None, root=tmp_path)
    _fake_opencode(monkeypatch)
    _clear_other_pending(db_conn, bid)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])
    row = _child(db_conn, claimed[0]['id'])
    assert row['status'] == 'completed'
    assert row['gate_status'] == 'skipped'


def test_apply_to_invalid_rejected():
    from utils import agent_ledger
    with pytest.raises(ValueError):
        agent_ledger.validate_checks([
            {'name': 'c', 'tool': 'w', 'args_pattern': 'x',
             'apply_to': {'batch_seq': ['a']}}])
    with pytest.raises(ValueError):
        agent_ledger.validate_checks([
            {'name': 'c', 'tool': 'w', 'args_pattern': 'x',
             'apply_to': {'input_file_glob': ''}}])
    with pytest.raises(ValueError):
        agent_ledger.validate_checks([
            {'name': 'c', 'tool': 'w', 'args_pattern': 'x',
             'apply_to': {}}])


def test_apply_to_preserved_through_validation():
    """F2：validate_checks 保留 apply_to（此前被剥离 → 落库前就丢）。"""
    from utils import agent_ledger
    checks = agent_ledger.validate_checks([
        {'name': 'only-first', 'tool': 'w', 'args_pattern': 'x',
         'apply_to': {'batch_seq': [0]}}])
    assert checks[0]['apply_to'] == {'batch_seq': [0]}


def test_apply_to_end_to_end_registration(db_conn, user_id):
    """定向期望：apply_to.batch_seq 不匹配的子任务不登记该期望（误报清零）。"""
    from utils.batch_engine import BatchWorker
    checks = [{'name': 'only0', 'tool': 'w', 'args_pattern': 'x',
               'apply_to': {'batch_seq': [0]}}]
    bid, sids = _seed_batch(db_conn, user_id, 2, batch_checks=checks)
    w = BatchWorker()
    reg0 = w._register_action_expectations(sids[0], bid, {'batch_seq': 0})
    reg1 = w._register_action_expectations(sids[1], bid, {'batch_seq': 1})
    assert reg0['applicable'] == 1 and reg0['registered'] == 1
    assert reg1['applicable'] == 0  # 不匹配 → 门禁 skipped，不误报


# ---------------------------------------------------------------------------
# 7. F5/F6/F7/F8/F9/F12
# ---------------------------------------------------------------------------

def test_f5_cancel_child_on_paused_lands_cancelled(db_conn, user_id):
    from utils.batch_repo import cancel_child
    bid, sids = _seed_batch(db_conn, user_id, 1)
    sid = sids[0]
    _set_child(db_conn, sid, status='paused')
    res = cancel_child(user_id, bid, sid)
    assert res['status'] == 'cancelled'
    status, done, failed, total = _batch(db_conn, bid)
    assert failed == 1 and status == 'failed'
    with pytest.raises(ValueError):
        cancel_child(user_id, bid, sid)  # 已终态


def test_f6_batch_seq_unique_index(db_conn, user_id):
    import psycopg2
    bid, sids = _seed_batch(db_conn, user_id, 1)
    cur = db_conn.cursor()
    with pytest.raises(psycopg2.errors.IntegrityError):
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
            "batch_seq, batch_input_file) VALUES (%s, %s, 'pending', %s, 0, 'x')",
            (str(uuid.uuid4()), user_id, bid))
    db_conn.rollback()


def test_f6_append_allocates_sequential_seqs(db_conn, user_id):
    from utils.batch_repo import append_to_batch
    bid, sids = _seed_batch(db_conn, user_id, 1)
    d = append_to_batch(user_id, bid,
                        [{'name': 'a', 'path': f'batch-staging/{user_id}/x/a.txt'},
                         {'name': 'b', 'path': f'batch-staging/{user_id}/x/b.txt'}])
    seqs = sorted(s['batch_seq'] for s in d['sessions'])
    assert seqs == [0, 1, 2]  # 原子任务 0 + 追加 1/2，无重复


def test_f7_retry_failed_keeps_paused_status(db_conn, user_id):
    from utils.batch_repo import reset_failed_to_pending
    bid, sids = _seed_batch(db_conn, user_id, 2)
    _set_child(db_conn, sids[0], status='failed')
    _set_child(db_conn, sids[1], status='paused')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET failed=1 WHERE id=%s", (bid,))
    db_conn.commit()
    n = reset_failed_to_pending(user_id, bid)
    assert n == 1
    status, done, failed, total = _batch(db_conn, bid)
    assert status == 'paused'  # 旧内联 CASE 会错写成 'pending'
    assert failed == 0
    row = _child(db_conn, sids[0])
    assert row['generation'] == 1  # 重试换代


def test_f8_results_exclude_soft_deleted(db_conn, user_id):
    from utils.batch_repo import get_batch_results
    bid, sids = _seed_batch(db_conn, user_id, 2)
    _set_child(db_conn, sids[0], status='completed', deleted_at=datetime.now())
    _set_child(db_conn, sids[1], status='failed', error_message='x')
    results = get_batch_results(bid)
    assert [r['name'] for r in results] == ['f1.csv']


def test_f9_retry_closes_attempt_with_parent(db_conn, user_id, monkeypatch,
                                             tmp_path):
    """可重试失败重排后：旧 attempt 落 recovering、新 attempt 带
    parent_attempt_id、终态后无悬挂 running。"""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    root = tmp_path
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))
    fake = _fake_opencode(monkeypatch)
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': False, 'content': []}]
    w = BatchWorker()
    w.MAX_AUTO_RETRY = 1
    w.STALL_TIMEOUT_SEC = 0  # 第二轮轮询立刻判停滞
    monkeypatch.setattr(BatchWorker, 'POLL_INTERVAL_SEC', 0)
    bid, sids = _seed_batch(db_conn, user_id, 1, root=tmp_path)
    _clear_other_pending(db_conn, bid)
    claimed = w._claim_pending_sessions(limit=1)
    sid = claimed[0]['id']
    w._run_one(claimed[0])   # 停滞 → auto retry（旧 attempt → recovering）
    assert _child(db_conn, sid)['status'] == 'pending'
    claimed2 = w._claim_pending_sessions(limit=1)
    assert claimed2 and claimed2[0]['id'] == sid
    w._run_one(claimed2[0])  # 再次停滞 → 预算用尽 → failed
    row = _child(db_conn, sid)
    assert row['status'] == 'failed'
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM ai_execution_attempts "
                    "WHERE session_id=%s GROUP BY status", (sid,))
        stats = dict(cur.fetchall())
    assert stats.get('running', 0) == 0            # 无悬挂
    assert stats.get('recovering', 0) == 1         # 旧轮收口
    assert stats.get('failed', 0) == 1             # 新轮终态
    cur = db_conn.cursor()
    cur.execute("SELECT parent_attempt_id FROM ai_execution_attempts "
                "WHERE session_id=%s AND status='failed'", (sid,))
    assert cur.fetchone()[0] is not None           # 审计链不断


def test_f12_retention_registered_once():
    """F12：app 源码层面不再有第二个 retention 注册。"""
    import app as app_module
    src = open(app_module.__file__, encoding='utf-8').read()
    assert 'def _start_audit_retention_job' not in src
    assert '_start_audit_retention_job()' not in src
    assert src.count("id='execution-audit-retention'") == 1


# ---------------------------------------------------------------------------
# 8. lease：单实例保护（P0-9）
# ---------------------------------------------------------------------------

def test_lease_single_owner(db_conn):
    from utils import execution_lease
    key = 'batch-test-' + uuid.uuid4().hex[:8]
    owner_a = execution_lease.owner_id()
    owner_b = execution_lease.owner_id()
    try:
        ok_a, token_a = execution_lease.acquire(key, owner_a)
        assert ok_a and token_a >= 1
        ok_b, _ = execution_lease.acquire(key, owner_b)
        assert not ok_b  # 未过期 → 抢不到
        ok_a2, token_a2 = execution_lease.acquire(key, owner_a)
        assert ok_a2 and token_a2 == token_a + 1  # 自己续占 token 递增
        assert execution_lease.heartbeat(key, owner_a)
        assert not execution_lease.heartbeat(key, owner_b)
        assert execution_lease.release(key, owner_a)
    finally:
        execution_lease.release(key, owner_a)


def test_worker_start_without_lease_disables_dispatcher(db_conn):
    """抢占不到租约 → dispatcher 不启动（多进程只有一个实例跑 worker）。"""
    from utils.batch_engine import BatchWorker
    from utils import execution_lease
    owner = execution_lease.owner_id()
    ok, _ = execution_lease.acquire('batch', owner, ttl_sec=300)
    assert ok
    try:
        w = BatchWorker()
        w.start()
        assert w._holds_lease is False
        assert w._dispatcher is None
    finally:
        execution_lease.release('batch', owner)
