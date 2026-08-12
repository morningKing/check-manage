"""Tests for the in-process BatchWorker. OpenCode is mocked."""
import os
import sys
import uuid
import time
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
    """扫描一批消息，发现其中的 subtask part，返回新发现的 sessionID 集合。"""
    import utils.batch_engine as eng
    msgs = [
        {'info': {'role': 'assistant', 'id': 'm1'},
         'parts': [{'type': 'text', 'text': 'hi'},
                  {'type': 'subtask', 'sessionID': 'ses_child1',
                   'agent': 'build', 'description': 'x'}]},
    ]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=0, parent_sid=None)
    assert found['ses_child1']['depth'] == 1
    assert found['ses_child1']['parent_id'] is None
    assert found['ses_child1']['agent'] == 'build'
    assert found['ses_child1']['description'] == 'x'


def test_discover_subtasks_sets_parent_sid_for_nested_scan():
    """递归扫描某个子代理自己的消息时，parent_sid 传该子代理自己的 id，
    发现的孙代的 parent_id 要是它，不是 None。"""
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'cm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_grandchild',
                      'agent': 'review', 'description': 'y'}]}]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=1, parent_sid='ses_child1')
    assert found['ses_grandchild']['depth'] == 2
    assert found['ses_grandchild']['parent_id'] == 'ses_child1'


def test_discover_subtasks_skips_already_known():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_child1',
                      'agent': 'build', 'description': 'x'}]}]
    already = {'ses_child1': {'depth': 1, 'parent_id': None}}
    found = eng.discover_subtasks(msgs, known=already, parent_depth=0, parent_sid=None)
    assert found == {}


def test_discover_subtasks_respects_depth_cap():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_too_deep',
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
             'parts': [{'type': 'subtask', 'sessionID': 'ses_child_be',
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
