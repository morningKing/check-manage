"""Tests for the in-process BatchWorker. OpenCode is mocked."""
import os
import sys
import uuid
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its UUID, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'be_user_{uid[:8]}', 'x', f'BE User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    # Clean up in dependency order
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_batch(db_conn, user_id, n_sessions=3):
    """Insert a batch + n_sessions pending sessions, return (batch_id, [session_ids])."""
    bid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'engine-test', 'p', %s)",
            (bid, user_id, n_sessions),
        )
        sids = []
        for seq in range(n_sessions):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, seq, f'batch-staging/x/{seq}.txt'),
            )
            sids.append(sid)
    db_conn.commit()
    return bid, sids


# ---------------------------------------------------------------------------
# Test 1: claim respects limit
# ---------------------------------------------------------------------------

def test_claim_marks_batch_running(user_id, db_conn):
    """The batch flips pending -> running the moment its first child is claimed,
    so the sidebar shows 运行中 while children run (not 待运行 until one finishes)."""
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=2)   # batch defaults to 'pending'
    BatchWorker()._claim_pending_sessions(limit=1)
    db_conn.rollback()   # drop our snapshot so we see the worker's committed update
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_batches WHERE id = %s", (bid,))
        assert cur.fetchone()[0] == 'running'


def test_claim_pending_respects_limit(user_id, db_conn):
    from utils.batch_engine import BatchWorker
    bid, sids = _seed_batch(db_conn, user_id, n_sessions=5)
    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=2)
    assert len(claimed) == 2
    # Claimed rows are now status='running' in DB
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM ai_chat_sessions "
            "WHERE batch_id = %s AND status = 'running'",
            (bid,),
        )
        assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Standalone /v1/ai-sessions children (open_api_ai_sessions.py): a session
# with batch_id=NULL, api_key_id set, prompt stashed in continue_prompt.
# ---------------------------------------------------------------------------

def _seed_standalone_session(db_conn, user_id, *, prompt='hello', agent=None, model=None):
    """Insert a throwaway api_keys row + a pending, batch-less session.
    Returns (session_id, api_key_id); caller's `user_id` fixture cleans up the
    session (WHERE user_id=...), but the api_keys row needs its own cleanup."""
    key_id = f'ak-test-{uuid.uuid4().hex[:8]}'
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash) VALUES (%s, %s, %s)",
            (key_id, 'engine-test-key', f'fake-hash-{key_id}'),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, continue_prompt, agent, model, api_key_id) "
            "VALUES (%s, %s, 'pending', NULL, %s, %s, %s, %s)",
            (sid, user_id, prompt, agent, model, key_id),
        )
    db_conn.commit()
    return sid, key_id


def _cleanup_api_key(db_conn, key_id):
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    db_conn.commit()


def test_prepare_workspace_empty_staged_path_does_not_copy_workspace_root(tmp_path, monkeypatch):
    """回归测试：staged_file_path='' 曾经会把整个 workspace 根目录（含其它
    session 的文件）递归拷进这个 session 自己的 uploads/。"""
    import utils.batch_engine as eng

    root = tmp_path / 'ai-workspaces'
    other_session = root / 'someone-else' / 'other-sid'
    other_session.mkdir(parents=True)
    (other_session / 'secret.txt').write_text('should not leak')
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))

    ws = eng._prepare_workspace('u1', 'sid-standalone', '')

    uploads = list(Path(ws, 'uploads').iterdir()) if Path(ws, 'uploads').exists() else []
    assert uploads == []


def test_claim_picks_up_standalone_session(user_id, db_conn):
    """batch_id IS NULL 但 api_key_id 非空的行也要被 dispatcher 捞到——不是只有
    批任务子会话才能进 pending/running 状态机。"""
    from utils.batch_engine import BatchWorker
    sid, key_id = _seed_standalone_session(db_conn, user_id)
    try:
        w = BatchWorker()
        claimed = w._claim_pending_sessions(limit=1)
        assert len(claimed) == 1
        assert claimed[0]['id'] == sid
        assert claimed[0]['batch_id'] is None
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == 'running'
    finally:
        _cleanup_api_key(db_conn, key_id)


def test_run_one_standalone_session_happy_path(user_id, db_conn, monkeypatch, tmp_path):
    """无父批任务的独立会话：prompt 从 continue_prompt 取、agent/model 从会话
    行取、跑完 continue_prompt 被清空、_mark_done 不会因为 batch_id=None 报错。"""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    sid, key_id = _seed_standalone_session(
        db_conn, user_id, prompt='帮我写一句问候语', agent='build', model='m1')
    try:
        fake_oc = MagicMock()
        fake_oc.create_session.return_value = 'oc-standalone-1'
        fake_oc.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]
        fake_oc.send_message.return_value = {'id': 'msg-1'}
        fake_oc.list_messages.return_value = [
            {'role': 'assistant', 'finished': True,
             'content': [{'type': 'text', 'text': '你好！'}]}
        ]
        fake_oc.get_messages.return_value = []
        monkeypatch.setattr(eng, 'opencode_client', fake_oc)
        ws = str(tmp_path)
        monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

        w = BatchWorker()
        claimed = w._claim_pending_sessions(limit=1)
        w._run_one(claimed[0])

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT status, opencode_session_id, last_message_preview, continue_prompt "
                "FROM ai_chat_sessions WHERE id = %s",
                (sid,),
            )
            status, oc_id, preview, remaining_prompt = cur.fetchone()
            assert status == 'completed'
            assert oc_id == 'oc-standalone-1'
            assert preview is not None
            assert remaining_prompt is None  # cleared, not left dangling for a future claim

        # send_message got the sourced agent/model (not None from a missing batch row)
        assert fake_oc.send_message.call_args.kwargs['agent'] == 'build'
        assert fake_oc.send_message.call_args.kwargs['model'] == 'm1'
    finally:
        _cleanup_api_key(db_conn, key_id)


def test_run_one_standalone_session_failure_does_not_touch_batches_table(
        user_id, db_conn, monkeypatch, tmp_path):
    """batch_id=None 时 _mark_failed 不该再去碰 ai_chat_batches（之前是安全的
    空操作，现在直接跳过）——这里断言失败路径本身照常工作。"""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    sid, key_id = _seed_standalone_session(db_conn, user_id, prompt='p')
    try:
        fake_oc = MagicMock()
        fake_oc.create_session.side_effect = RuntimeError('opencode unreachable')
        monkeypatch.setattr(eng, 'opencode_client', fake_oc)
        monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

        w = BatchWorker()
        claimed = w._claim_pending_sessions(limit=1)
        w._run_one(claimed[0])  # must not raise

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT status, error_message FROM ai_chat_sessions WHERE id = %s", (sid,))
            status, error = cur.fetchone()
            assert status == 'failed'
            assert 'opencode unreachable' in (error or '')
    finally:
        _cleanup_api_key(db_conn, key_id)


# ---------------------------------------------------------------------------
# Test 2: happy path → completed
# ---------------------------------------------------------------------------

def test_run_one_happy_path_marks_completed(user_id, db_conn, monkeypatch, tmp_path):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    # Stub OpenCode interactions
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-session-1'
    fake_oc.send_message.return_value = {'id': 'msg-1'}
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done!'}]}
    ]
    fake_oc.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    ws = str(tmp_path)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, opencode_session_id, last_message_preview "
            "FROM ai_chat_sessions WHERE id = %s",
            (sids[0],),
        )
        status, oc_id, preview = cur.fetchone()
        assert status == 'completed'
        assert oc_id == 'oc-session-1'
        assert preview is not None

        cur.execute(
            "SELECT status, done, failed FROM ai_chat_batches WHERE id = %s",
            (bid,),
        )
        bstatus, done, failed = cur.fetchone()
        assert done == 1 and failed == 0
        assert bstatus == 'completed'


# ---------------------------------------------------------------------------
# Test 2b: mem0 injection into the prompt + extraction after completion
# ---------------------------------------------------------------------------

def test_run_one_injects_and_records_memory(user_id, db_conn, monkeypatch, tmp_path):
    """记忆注入：发给 OpenCode 的 prompt 应包含 render_memory_block 的输出；
    记忆抽取：成功完成后应异步调用 add_memory(user_id, [user, assistant])，
    且 user 消息用的是未拼记忆块的原始 prompt（不能把记忆块自己再存回记忆库）。"""
    import threading
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng
    import utils.memory as mem

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-session-1'
    sent_prompts = []

    def _capture_send_message(oc_sid, prompt, **kw):
        sent_prompts.append(prompt)
        return {'id': 'msg-1'}
    fake_oc.send_message.side_effect = _capture_send_message
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done!'}]}
    ]
    fake_oc.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    ws = str(tmp_path)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

    monkeypatch.setattr(
        mem, 'search_memory',
        lambda user_id, query, limit=5: [{'memory': '偏好用中文回复'}])

    called = threading.Event()
    captured = {}

    def _fake_add_memory(user_id, messages):
        captured['user_id'] = user_id
        captured['messages'] = messages
        called.set()
    monkeypatch.setattr(mem, 'add_memory', _fake_add_memory)

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    assert sent_prompts, 'send_message was never called'
    assert '偏好用中文回复' in sent_prompts[0]
    assert '关于当前用户的长期记忆' in sent_prompts[0]

    assert called.wait(timeout=5), 'add_memory was not called'
    assert captured['user_id'] == user_id
    assert captured['messages'][0]['role'] == 'user'
    assert '长期记忆' not in captured['messages'][0]['content']
    assert captured['messages'][1]['role'] == 'assistant'
    assert 'done!' in captured['messages'][1]['content']


# ---------------------------------------------------------------------------
# Test 3: HTTP error → failed
# ---------------------------------------------------------------------------

def test_run_one_http_error_marks_failed(user_id, db_conn, monkeypatch, tmp_path):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    fake_oc = MagicMock()
    fake_oc.create_session.side_effect = RuntimeError("opencode 500")
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    ws = str(tmp_path)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM ai_chat_sessions WHERE id = %s",
            (sids[0],),
        )
        status, err = cur.fetchone()
        assert status == 'failed'
        assert 'opencode 500' in (err or '')

        cur.execute(
            "SELECT status, done, failed FROM ai_chat_batches WHERE id = %s",
            (bid,),
        )
        bstatus, done, failed = cur.fetchone()
        assert done == 0 and failed == 1
        assert bstatus == 'failed'


# ---------------------------------------------------------------------------
# Test 4: timeout → failed
# ---------------------------------------------------------------------------

def test_run_one_timeout_marks_failed(user_id, db_conn, monkeypatch, tmp_path):
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)

    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-session-T'
    fake_oc.send_message.return_value = {'id': 'msg-1'}
    # Never finishes — finished=False always
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': False, 'content': []}
    ]
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    ws = str(tmp_path)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

    w = BatchWorker()
    w.SESSION_TIMEOUT_SEC = 1   # speed up the test
    w.POLL_INTERVAL_SEC = 0.2
    claimed = w._claim_pending_sessions(limit=1)
    w._run_one(claimed[0])

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM ai_chat_sessions WHERE id = %s",
            (sids[0],),
        )
        status, err = cur.fetchone()
        assert status == 'failed'
        assert 'timeout' in (err or '').lower()


# ---------------------------------------------------------------------------
# Test 5: partial status when mix of done + failed
# ---------------------------------------------------------------------------

def test_batch_status_partial_when_mix(user_id, db_conn):
    """When _recompute_batch_status sees done>0 and failed>0 and all terminal,
    parent batch.status = 'partial'."""
    from utils.batch_engine import _recompute_batch_status

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=3)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'completed' WHERE id = %s",
            (sids[0],),
        )
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'failed' WHERE id = %s",
            (sids[1],),
        )
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'completed' WHERE id = %s",
            (sids[2],),
        )
        cur.execute(
            "UPDATE ai_chat_batches SET done = 2, failed = 1 WHERE id = %s",
            (bid,),
        )
    db_conn.commit()

    _recompute_batch_status(bid)

    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_batches WHERE id = %s", (bid,))
        assert cur.fetchone()[0] == 'partial'


# ---------------------------------------------------------------------------
# Test 5b: terminal status fires the open-API completion callback
# ---------------------------------------------------------------------------

def test_recompute_batch_status_fires_callback_on_terminal(user_id, db_conn, monkeypatch):
    """批任务收敛为终态且配置了 callback_url 时，应通过 webhook_engine 异步通知。"""
    import threading
    import utils.webhook_engine as wh
    from utils.batch_engine import _recompute_batch_status

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_chat_batches SET done = 1, callback_url = %s, callback_secret = %s "
            "WHERE id = %s",
            ('https://example.com/hook', 'sec', bid),
        )
    db_conn.commit()

    called = threading.Event()
    captured = {}

    def _fake_fire(**kwargs):
        captured.update(kwargs)
        called.set()
        return {'success': True}

    monkeypatch.setattr(wh, '_fire_single_webhook', _fake_fire)

    _recompute_batch_status(bid)

    assert called.wait(timeout=5), 'callback webhook was not fired'
    assert captured['webhook_url'] == 'https://example.com/hook'
    assert captured['secret'] == 'sec'
    assert captured['event_type'] == 'ai_batch_completed'
    assert captured['payload']['batchId'] == bid
    assert captured['payload']['status'] == 'completed'


def test_recompute_batch_status_no_callback_when_url_unset(user_id, db_conn, monkeypatch):
    """没配置 callback_url 的批任务收敛为终态时，不应触发任何 webhook 调用。"""
    import utils.webhook_engine as wh
    from utils.batch_engine import _recompute_batch_status

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET done = 1 WHERE id = %s", (bid,))
    db_conn.commit()

    fire = MagicMock()
    monkeypatch.setattr(wh, '_fire_single_webhook', fire)

    _recompute_batch_status(bid)
    time.sleep(0.2)  # give any (unexpected) background thread a chance to run

    fire.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: concurrency cap ≤ 3
# ---------------------------------------------------------------------------

def test_concurrency_cap_3(user_id, db_conn, monkeypatch, tmp_path):
    """5 pending sessions → only 3 ever in _running_session_ids simultaneously."""
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=5)
    in_flight_peak = [0]

    fake_oc = MagicMock()

    def slow_create(*a, **kw):
        time.sleep(0.05)   # spread out work so ≤3 truly overlap
        return 'oc'

    fake_oc.create_session.side_effect = slow_create
    fake_oc.send_message.return_value = {'id': 'm'}
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'ok'}]}
    ]
    fake_oc.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)
    ws = str(tmp_path)
    monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: ws)

    w = BatchWorker()
    original_run_one = w._run_one

    def watching_run_one(s):
        # Measure BEFORE running so in-flight count includes this session
        with w._lock:
            in_flight_peak[0] = max(in_flight_peak[0], len(w._running_session_ids))
        original_run_one(s)

    w._run_one = watching_run_one
    w.start()

    # Wake the dispatcher immediately after seeding (10s default wait is too long)
    w.notify()

    # Wait for all 5 to drain (done + failed == 5), max 12 seconds
    for _ in range(60):
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT done + failed FROM ai_chat_batches WHERE id = %s",
                (bid,),
            )
            row = cur.fetchone()
            if row and row[0] == 5:
                break
        time.sleep(0.2)

    w.stop()

    assert in_flight_peak[0] <= 3


# ---------------------------------------------------------------------------
# Test 7: restart audit resets orphaned 'running' batch sessions
# ---------------------------------------------------------------------------

def test_restart_audit_resets_orphaned_running(user_id, db_conn):
    """A 'running' batch session left over from a previous Flask process should
    be reset to 'pending' when the worker starts."""
    from utils.batch_engine import BatchWorker

    bid, sids = _seed_batch(db_conn, user_id, n_sessions=1)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'running' WHERE id = %s",
            (sids[0],),
        )
    db_conn.commit()

    w = BatchWorker()
    w._restart_audit()

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM ai_chat_sessions WHERE id = %s",
            (sids[0],),
        )
        assert cur.fetchone()[0] == 'pending'


def test_create_batch_stores_provision_and_context_returns_it(user_id, db_conn):
    """create_batch persists provision_repo/ref and the worker reads them via
    _fetch_batch_context (so the provisioning step gets them per run)."""
    from utils.batch_repo import create_batch
    from utils.batch_engine import BatchWorker
    data = create_batch(
        user_id, name='prov-test', prompt='do', template_id=None,
        files=[{'name': 'r.txt', 'path': 'batch-staging/x/r.txt'}],
        agent='my-agent', provision_repo='https://example.com/agents.git',
        provision_ref='main')
    bid = data['batch']['id']
    assert data['batch']['provision_repo'] == 'https://example.com/agents.git'
    assert data['batch']['provision_ref'] == 'main'
    ctx = BatchWorker()._fetch_batch_context(bid)
    prompt, agent, model, repo, ref = ctx
    assert (agent, repo, ref) == ('my-agent', 'https://example.com/agents.git', 'main')


def test_await_finished_no_session_cap_but_stall_still_guards(monkeypatch):
    """With SESSION_TIMEOUT_SEC=0 (no hard cap, the requested 'no timeout'), a
    working turn isn't clock-killed, but a genuinely idle/frozen turn is still
    caught by the stall watchdog."""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    from utils.batch_engine import _SessionTimeout
    import pytest as _pytest
    w = eng.BatchWorker()
    w.SESSION_TIMEOUT_SEC = 0      # no hard cap
    w.STALL_TIMEOUT_SEC = 0.3
    w.POLL_INTERVAL_SEC = 0.02
    idle = {'role': 'assistant', 'finished': False, 'finish': None,
            'running_tool': False, 'content': []}
    fake = MagicMock(); fake.list_messages.return_value = [idle]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    with _pytest.raises(_SessionTimeout) as ei:
        w._await_finished('oc')
    assert 'stalled' in str(ei.value)


def test_await_finished_calls_on_progress_for_live_persist(monkeypatch):
    """The worker persists the conversation off its own REST poll (so the live
    view works without SSE): on_progress is invoked while the turn runs."""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    w = eng.BatchWorker()
    w.POLL_INTERVAL_SEC = 0.02
    w.PROGRESS_PERSIST_SEC = 0
    running = {'role': 'assistant', 'finished': False, 'finish': 'tool-calls',
               'running_tool': True, 'content': []}
    done = {'role': 'assistant', 'finished': True, 'finish': 'stop',
            'running_tool': False, 'content': [{'type': 'text', 'text': 'ok'}]}
    seq = [[running]] * 3 + [[done]]
    calls = {'i': 0}
    fake = MagicMock()
    def lm(oc, directory=''):
        i = min(calls['i'], len(seq) - 1); calls['i'] += 1; return seq[i]
    fake.list_messages.side_effect = lm
    monkeypatch.setattr(eng, 'opencode_client', fake)
    progress = {'n': 0}
    w._await_finished('oc', on_progress=lambda: progress.__setitem__('n', progress['n'] + 1))
    assert progress['n'] >= 2   # persisted live during the run, not just at the end


def test_progress_signature_counts_tool_activity():
    """A delegating subagent shows as a long-running `task` tool with no new
    text — the signature must still change as the tool advances, else the stall
    watchdog falsely fires."""
    from utils.batch_engine import BatchWorker
    sig = BatchWorker._progress_signature
    pending = [{'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'task', 'status': 'pending', 'output_len': 0}]}]
    running = [{'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'task', 'status': 'running', 'output_len': 0}]}]
    grew = [{'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'task', 'status': 'running', 'output_len': 120}]}]
    # status change and output growth both register as progress
    assert sig(pending) != sig(running)
    assert sig(running) != sig(grew)


def test_await_finished_no_stall_while_tool_running(monkeypatch):
    """A turn that's mid tool-call (subagent) with frozen TEXT must NOT be killed
    by the stall watchdog — only the 30-min SESSION_TIMEOUT bounds it."""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    w = eng.BatchWorker()
    w.STALL_TIMEOUT_SEC = 0.5
    w.POLL_INTERVAL_SEC = 0.02
    w.SESSION_TIMEOUT_SEC = 5
    running = {'role': 'assistant', 'finished': False, 'finish': 'tool-calls',
               'running_tool': True,
               'content': [{'type': 'tool_use', 'name': 'task', 'status': 'running', 'output_len': 0}]}
    done = {'role': 'assistant', 'finished': True, 'finish': 'stop',
            'running_tool': False, 'content': [{'type': 'text', 'text': 'done'}]}
    seq = [[running]] * 60 + [[done]]   # ~1.2s of frozen-text running > STALL 0.5s
    calls = {'i': 0}
    fake = MagicMock()
    def lm(oc, directory=''):
        i = min(calls['i'], len(seq) - 1)
        calls['i'] += 1
        return seq[i]
    fake.list_messages.side_effect = lm
    monkeypatch.setattr(eng, 'opencode_client', fake)
    preview, msg = w._await_finished('oc')
    assert msg['finished'] is True          # completed, NOT stalled


def test_await_finished_still_stalls_when_truly_idle(monkeypatch):
    """A genuinely frozen turn (no text, no tool in flight) still trips the stall."""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    from utils.batch_engine import _SessionTimeout
    import pytest as _pytest
    w = eng.BatchWorker()
    w.STALL_TIMEOUT_SEC = 0.3
    w.POLL_INTERVAL_SEC = 0.02
    w.SESSION_TIMEOUT_SEC = 5
    idle = {'role': 'assistant', 'finished': False, 'finish': None,
            'running_tool': False, 'content': []}
    fake = MagicMock()
    fake.list_messages.return_value = [idle]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    with _pytest.raises(_SessionTimeout) as ei:
        w._await_finished('oc')
    assert 'stalled' in str(ei.value)


def test_dispatch_tick_survives_claim_error():
    """A transient error while claiming must NOT propagate out of the dispatcher.

    Regression guard for "批任务一直待运行": before this, an exception in
    _claim_pending_sessions propagated out of _dispatcher_loop and killed the
    worker thread permanently, so every future batch hung in 'pending' with no
    error and no recovery until Flask restarted."""
    from utils.batch_engine import BatchWorker
    w = BatchWorker()
    calls = {'n': 0}

    def boom(limit):
        calls['n'] += 1
        raise RuntimeError('db hiccup')

    w._claim_pending_sessions = boom
    # Must NOT raise; reports failure so the loop backs off and keeps going.
    assert w._dispatch_tick() is False
    assert calls['n'] == 1


def test_dispatch_tick_submits_claimed_sessions():
    """A healthy tick claims pending sessions and submits each to the executor,
    reserving its slot in _running_session_ids."""
    from utils.batch_engine import BatchWorker
    w = BatchWorker()
    w._claim_pending_sessions = lambda limit: [{'id': 'sid-1'}]
    submitted = []

    class FakeExec:
        def submit(self, fn, arg):
            submitted.append(arg)

    w._executor = FakeExec()
    assert w._dispatch_tick() is True
    assert submitted == [{'id': 'sid-1'}]
    assert 'sid-1' in w._running_session_ids


def test_dispatch_tick_noop_when_no_free_slots():
    """When all concurrency slots are busy, the tick claims nothing."""
    from utils.batch_engine import BatchWorker
    w = BatchWorker()
    w._running_session_ids = {'a', 'b', 'c'}  # == MAX_CONCURRENT
    claimed = {'called': False}
    w._claim_pending_sessions = lambda limit: claimed.__setitem__('called', True) or []
    assert w._dispatch_tick() is True
    assert claimed['called'] is False


def test_run_one_passes_agent_to_opencode(user_id, db_conn, monkeypatch, tmp_path):
    """When batch has agent set, send_message receives that agent."""
    import utils.batch_engine as eng
    from utils.batch_repo import create_batch
    from unittest.mock import MagicMock

    staging = tmp_path / 'batch-staging' / 'x'
    staging.mkdir(parents=True)
    (staging / 'r.txt').write_text('hello')

    batch_data = create_batch(
        user_id,
        name='agent-engine-test',
        prompt='do stuff',
        template_id=None,
        files=[{'name': 'r.txt', 'path': 'batch-staging/x/r.txt'}],
        agent='my-agent',
    )
    session_row = batch_data['sessions'][0]

    sent_agents = []
    fake_oc = MagicMock()
    fake_oc.create_session.return_value = 'oc-sess-1'
    fake_oc.list_agents.return_value = [{'name': 'my-agent', 'mode': 'primary'}]
    fake_oc.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'ok'}]}
    ]
    fake_oc.get_messages.return_value = []
    def capture_send(oc_sid, prompt, directory='', agent='', model=''):
        sent_agents.append(agent)
    fake_oc.send_message.side_effect = capture_send

    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(eng, 'opencode_client', fake_oc)

    worker = eng.BatchWorker()
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='pending' WHERE id=%s", (session_row['id'],))
    db_conn.commit()

    claimed = worker._claim_pending_sessions(limit=1)
    assert claimed
    worker._run_one(claimed[0])

    assert sent_agents == ['my-agent']


# --- OpenCode 报错必须被透出，而不是被静默成 180 秒后的「无进展」 -------------
# 背景：OpenCode 的 AssistantMessage 有一个 `error` 字段（其 OpenAPI 里定义为
# ProviderAuthError / ContextOverflowError / APIError 等七选一）。出错的那条消息
# 通常没有文本片段、没有运行中的工具，而 `finish` 与 `time.completed` 在规格里都
# 不是必填、出错时一般也不带。若不读 error，这条消息在我们的映射里就是「什么都没
# 发生」——进度签名恒定 → 180 秒后抛 stalled (no progress)，把一个精确且可立即
# 行动的原因，换成了一个通用且误导的超时。

def _raw_errored_msg(name='ProviderAuthError', provider='anthropic',
                     message='API key not found'):
    """OpenCode /session/<id>/message 返回的原始形状（出错的一轮）。"""
    return {'info': {'id': 'm1', 'role': 'assistant', 'time': {'created': 1},
                     'error': {'name': name,
                               'data': {'providerID': provider, 'message': message}}},
            'parts': []}


def test_facade_surfaces_opencode_error(monkeypatch):
    """list_messages 必须把 info.error 带出来，否则上层无从判断。"""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    client = MagicMock()
    client.get_messages.return_value = [_raw_errored_msg()]
    monkeypatch.setattr(eng._OpenCodeFacade, '_client', lambda self: client)

    out = eng._OpenCodeFacade().list_messages('oc')

    assert len(out) == 1
    assert out[0]['error'] is not None
    assert out[0]['error']['name'] == 'ProviderAuthError'


def test_await_finished_fails_fast_on_opencode_error(monkeypatch):
    """出错的一轮必须**立刻**失败，且错误信息里带上 OpenCode 给的原因。

    鉴别力所在：如果错误检查被去掉，这里会退化成等满 STALL_TIMEOUT 再抛
    _SessionTimeout('stalled')，断言的类型、内容、耗时三项会同时红。
    """
    import time as _time
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    import pytest as _pytest
    w = eng.BatchWorker()
    w.STALL_TIMEOUT_SEC = 5          # 远大于本用例应有的耗时
    w.POLL_INTERVAL_SEC = 0.02
    w.SESSION_TIMEOUT_SEC = 30
    errored = {'role': 'assistant', 'finished': False, 'finish': None,
               'running_tool': False, 'content': [],
               'error': {'name': 'ProviderAuthError',
                         'data': {'providerID': 'anthropic',
                                  'message': 'API key not found'}}}
    fake = MagicMock()
    fake.list_messages.return_value = [errored]
    monkeypatch.setattr(eng, 'opencode_client', fake)

    t0 = _time.time()
    with _pytest.raises(eng._TurnFailed) as ei:
        w._await_finished('oc')
    elapsed = _time.time() - t0

    msg = str(ei.value)
    assert 'ProviderAuthError' in msg          # 错误种类
    assert 'anthropic' in msg                  # 是哪个 provider
    assert 'API key not found' in msg          # OpenCode 的原始说明
    assert elapsed < 2                         # 立刻失败，不是等满 stall


def test_await_finished_ignores_error_none(monkeypatch):
    """error 为 None 的正常消息不受影响 —— 不能把没出错的轮次误判成失败。"""
    import utils.batch_engine as eng
    from unittest.mock import MagicMock
    w = eng.BatchWorker()
    w.POLL_INTERVAL_SEC = 0.02
    done = {'role': 'assistant', 'finished': True, 'finish': 'stop',
            'running_tool': False, 'error': None,
            'content': [{'type': 'text', 'text': '正常输出'}]}
    fake = MagicMock()
    fake.list_messages.return_value = [done]
    monkeypatch.setattr(eng, 'opencode_client', fake)

    preview, msg = w._await_finished('oc')

    assert msg['finished'] is True


# ---------------------------------------------------------------------------
# Subtask discovery / status / two-phase persistence (Task 5)
# ---------------------------------------------------------------------------

def test_discover_subtasks_from_messages_finds_new_child():
    """扫描一批消息，发现其中的 subtask part，返回新发现的 sessionID 集合。
    子代理的真实 sessionID 在配套的 tool:'task' part 的 state.metadata.sessionId
    里，不在 subtask part 的 sessionID（那是父会话）。"""
    import utils.batch_engine as eng
    msgs = [
        {'info': {'role': 'assistant', 'id': 'm1'},
         'parts': [{'type': 'text', 'text': 'hi'},
                  {'type': 'tool', 'tool': 'task',
                   'state': {'status': 'completed',
                             'metadata': {'sessionId': 'ses_child1'}}},
                  {'type': 'subtask', 'sessionID': 'ses_parent',
                   'id': 'prt_subtask1',
                   'agent': 'build', 'description': 'x'}]},
    ]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=0, parent_sid=None)
    assert found['ses_child1']['depth'] == 1
    assert found['ses_child1']['parent_id'] is None
    assert found['ses_child1']['agent'] == 'build'
    assert found['ses_child1']['description'] == 'x'
    assert found['ses_child1']['_part_id'] == 'prt_subtask1'


def test_discover_subtasks_unpaired_task_tool_discovers_child():
    """自然语言委托只有 tool:'task' part、没有配套 subtask part——也要能发现
    子会话，agent/description 从工具输入取，_part_id 用 tool part 的 id。"""
    import utils.batch_engine as eng
    msgs = [
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'ses_parent'},
         'parts': [{'type': 'text', 'text': 'delegating'},
                  {'type': 'tool', 'tool': 'task', 'id': 'prt_task_nl',
                   'state': {'status': 'completed',
                             'input': {'subagent_type': 'general',
                                       'description': 'count lines'},
                             'metadata': {'sessionId': 'ses_child_nl'}}}]},
    ]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=0, parent_sid=None)
    assert found['ses_child_nl']['depth'] == 1
    assert found['ses_child_nl']['agent'] == 'general'
    assert found['ses_child_nl']['description'] == 'count lines'
    assert found['ses_child_nl']['_part_id'] == 'prt_task_nl'
    assert found['ses_child_nl']['_parent_session_id'] == 'ses_parent'


def test_discover_subtasks_sets_parent_sid_for_nested_scan():
    """递归扫描某个子代理自己的消息时，parent_sid 传该子代理自己的 id，
    发现的孙代的 parent_id 要是它，不是 None。"""
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'cm1'},
            'parts': [{'type': 'tool', 'tool': 'task',
                       'state': {'status': 'completed',
                                 'metadata': {'sessionId': 'ses_grandchild'}}},
                      {'type': 'subtask', 'sessionID': 'ses_child1',
                       'id': 'prt_gc',
                       'agent': 'review', 'description': 'y'}]}]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=1, parent_sid='ses_child1')
    assert found['ses_grandchild']['depth'] == 2
    assert found['ses_grandchild']['parent_id'] == 'ses_child1'


def test_discover_subtasks_skips_already_known():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'tool', 'tool': 'task',
                       'state': {'status': 'completed',
                                 'metadata': {'sessionId': 'ses_child1'}}},
                      {'type': 'subtask', 'sessionID': 'ses_parent',
                       'id': 'prt_st1',
                       'agent': 'build', 'description': 'x'}]}]
    already = {'ses_child1': {'depth': 1, 'parent_id': None}}
    found = eng.discover_subtasks(msgs, known=already, parent_depth=0, parent_sid=None)
    assert found == {}


def test_discover_subtasks_respects_depth_cap():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'tool', 'tool': 'task',
                       'state': {'status': 'completed',
                                 'metadata': {'sessionId': 'ses_too_deep'}}},
                      {'type': 'subtask', 'sessionID': 'ses_parent',
                       'id': 'prt_deep',
                       'agent': 'x', 'description': 'y'}]}]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=eng.MAX_SUBTASK_DEPTH,
                                  parent_sid='whatever')
    assert found == {}


def test_subtask_status_from_messages_detects_error():
    """子代理自己的消息列表里出现 error -> failed；出现 finished 的 assistant
    消息且无 error -> completed；否则 running。跟批任务顶层复用同一套
    finish/error 判定，不是重新发明。"""
    import utils.batch_engine as eng
    errored = [{'info': {'role': 'assistant', 'id': 'm1',
                        'error': {'name': 'ProviderAuthError',
                                 'data': {'providerID': 'a', 'message': 'no key'}}}}]
    status, err = eng.subtask_status_from_messages(errored)
    assert status == 'failed'
    assert 'ProviderAuthError' in err

    running = [{'info': {'role': 'assistant', 'id': 'm1'},
               'parts': [{'type': 'text', 'text': 'wip'}]}]
    status, err = eng.subtask_status_from_messages(running)
    assert status == 'running'
    assert err is None

    finished = [{'info': {'role': 'assistant', 'id': 'm1', 'finish': 'stop',
                         'time': {'created': 1, 'completed': 2}}}]
    status, err = eng.subtask_status_from_messages(finished)
    assert status == 'completed'


def test_persist_conversation_persists_discovered_subtask_and_refreshes_parent_stub(monkeypatch):
    """_persist_conversation 拉到的顶层消息里含 subtask part 时，要：
    (1) 递归拉取该子代理自己的消息并落库到 ai_chat_subtasks/ai_chat_subtask_messages；
    (2) 顶层自己持久化的 ai_chat_messages 内容里，也要有一条 subtask_use 占位，
        且它的 status 反映子代理**当前**的真实状态（这条子代理已经 finish 了，
        占位不能还停在 'running'）——这是本任务的核心断言，不是只落子代理
        自己的数据就算完事。"""
    import uuid
    import json
    import utils.batch_engine as eng
    from db import get_db
    from unittest.mock import MagicMock

    uid = 'u-be-sub-' + uuid.uuid4().hex[:6]
    sid = 's-be-sub-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    try:
        top_msgs = [
            {'info': {'role': 'assistant', 'id': 'm1'},
             'parts': [{'type': 'tool', 'tool': 'task',
                        'state': {'status': 'completed',
                                  'metadata': {'sessionId': 'ses_child_be'}}},
                       {'type': 'subtask', 'sessionID': 'ses_parent',
                        'id': 'prt_sub_be',
                        'agent': 'build', 'description': 'do y'}]},
        ]
        # 子代理自己已经跑完了（finish='stop' + time.completed）
        child_msgs = [
            {'info': {'role': 'assistant', 'id': 'cm1', 'finish': 'stop',
                      'time': {'created': 1, 'completed': 2}},
             'parts': [{'type': 'text', 'text': 'child hi'}]},
        ]
        fake = MagicMock()
        fake.get_messages.side_effect = lambda oc_id, directory='': (
            top_msgs if oc_id == 'oc-top' else child_msgs)
        monkeypatch.setattr(eng, 'opencode_client', fake)

        w = eng.BatchWorker()
        w._persist_conversation(sid, 'prompt', 'oc-top', None, directory='/tmp/x')

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_chat_subtasks WHERE id = %s",
                           ('ses_child_be',))
                assert cur.fetchone() == ('completed',)
                cur.execute("SELECT content FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_be',))
                content = cur.fetchone()[0]
                assert content == [{'type': 'text', 'text': 'child hi'}]
                # 顶层自己的消息里必须有一条状态已刷新的占位
                cur.execute("SELECT content FROM ai_chat_messages WHERE session_id = %s "
                           "AND role = 'assistant'", (sid,))
                top_content = cur.fetchone()[0]
                stubs = [p for p in top_content if p['type'] == 'subtask_use']
                assert stubs == [{'type': 'subtask_use', 'subtaskId': 'ses_child_be',
                                  'agent': 'build', 'description': 'do y',
                                  'status': 'completed'}]
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()


def test_persist_conversation_child_trace_includes_user_reasoning_and_tools(monkeypatch):
    """批任务路径落子代理消息时，要保留完整执行轨迹：user（委托输入）、
    reasoning（思考过程）、tool_use（工具调用）、text（输出），不能只剩 text。"""
    import uuid
    import utils.batch_engine as eng
    from db import get_db
    from unittest.mock import MagicMock

    uid = 'u-be-trace-' + uuid.uuid4().hex[:6]
    sid = 's-be-trace-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    try:
        top_msgs = [
            {'info': {'role': 'assistant', 'id': 'm1'},
             'parts': [{'type': 'tool', 'tool': 'task', 'id': 'prt_task_t',
                        'state': {'status': 'completed',
                                  'input': {'subagent_type': 'general',
                                            'description': 'trace'},
                                  'metadata': {'sessionId': 'ses_child_t'}}}]},
        ]
        child_msgs = [
            {'info': {'role': 'user', 'id': 'cu1'},
             'parts': [{'type': 'text', 'text': 'delegation input'}]},
            {'info': {'role': 'assistant', 'id': 'cm1', 'finish': 'stop',
                      'time': {'created': 1, 'completed': 2}},
             'parts': [{'type': 'reasoning', 'text': 'thinking about it'},
                      {'type': 'tool', 'tool': 'bash',
                       'state': {'status': 'completed', 'title': 'run',
                                 'input': {'command': 'ls'}}},
                      {'type': 'text', 'text': 'final answer'}]},
        ]
        fake = MagicMock()
        fake.get_messages.side_effect = lambda oc_id, directory='': (
            top_msgs if oc_id == 'oc-top' else child_msgs)
        monkeypatch.setattr(eng, 'opencode_client', fake)

        w = eng.BatchWorker()
        w._persist_conversation(sid, 'prompt', 'oc-top', None, directory='/tmp/x')

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT role, content FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s ORDER BY seq", ('ses_child_t',))
                rows = cur.fetchall()
                assert rows[0][0] == 'user'
                assert rows[0][1] == [{'type': 'text', 'text': 'delegation input'}]
                assert rows[1][0] == 'assistant'
                types = [p['type'] for p in rows[1][1]]
                assert types == ['reasoning', 'tool_use', 'text']
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()