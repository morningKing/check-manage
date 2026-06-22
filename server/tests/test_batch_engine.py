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
