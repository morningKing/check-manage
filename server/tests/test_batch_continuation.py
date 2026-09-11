"""Safe manual continuation tests for internal batch child sessions."""
import json
import os
import sys
import threading
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def continuation_user(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'continue_{uid[:8]}', 'x', 'Continuation test'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    db_conn.commit()


def _seed_terminal_child(db_conn, user_id, status='completed'):
    bid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, done, failed, status) "
            "VALUES (%s, %s, 'continue-test', 'initial', 1, %s, %s, %s)",
            (bid, user_id, 1 if status == 'completed' else 0,
             1 if status == 'failed' else 0, status),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, status, batch_id, batch_seq, opencode_session_id, workspace_path) "
            "VALUES (%s, %s, %s, %s, 0, 'oc-existing', '/tmp/continue-workspace')",
            (sid, user_id, status, bid),
        )
    db_conn.commit()
    return bid, sid


def test_continue_child_atomically_stores_message_and_reopens_parent(
        continuation_user, db_conn):
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user)
    result = continue_child(
        bid, sid, continuation_user, 'please refine it', ['uploads/notes.txt'],
        'reviewer', 'provider/model',
    )

    assert result['status'] == 'pending'
    assert result['message_id']
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, continue_prompt, error_message, agent, model "
            "FROM ai_chat_sessions WHERE id=%s", (sid,))
        status, raw_prompt, error, agent, model = cur.fetchone()
        assert status == 'pending'
        payload = json.loads(raw_prompt)
        assert payload['content'] == 'please refine it'
        assert payload['attachments'] == ['uploads/notes.txt']
        assert error is None
        assert (agent, model) == ('reviewer', 'provider/model')
        cur.execute(
            "SELECT role, content FROM ai_chat_messages WHERE id=%s",
            (result['message_id'],),
        )
        role, content = cur.fetchone()
        assert role == 'user'
        assert content[-1] == {'type': 'file', 'name': 'notes.txt',
                               'path': 'uploads/notes.txt'}
        cur.execute("SELECT done, failed, status FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() == (0, 0, 'pending')


def test_continue_child_rejects_running_child(continuation_user, db_conn):
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user, status='running')
    with pytest.raises(ValueError, match='terminal'):
        continue_child(bid, sid, continuation_user, 'again', [], None, None)


def test_continue_child_scopes_owner(continuation_user, db_conn):
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user)
    assert continue_child(bid, sid, str(uuid.uuid4()), 'again', [], None, None) is None


def test_continue_child_compare_and_set_allows_only_one_attempt(continuation_user, db_conn):
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user)
    results = []
    errors = []

    def attempt(number):
        try:
            results.append(continue_child(
                bid, sid, continuation_user, f'attempt {number}', [], None, None))
        except Exception as exc:  # noqa: BLE001 - assert the losing CAS outcome below
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert 'terminal' in str(errors[0])


def test_worker_continuation_uses_existing_workspace_and_shared_prompt_builder(
        continuation_user, db_conn, tmp_path, monkeypatch):
    import utils.batch_engine as engine
    from utils.batch_engine import BatchWorker
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user)
    workspace = tmp_path / 'child'
    workspace.mkdir()
    (workspace / 'notes.txt').write_text('attachment text', encoding='utf-8')
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET workspace_path=%s WHERE id=%s",
                    (str(workspace), sid))
    db_conn.commit()
    queued = continue_child(
        bid, sid, continuation_user, 'refine this', ['notes.txt'], 'reviewer', 'model/x')

    built = []
    monkeypatch.setattr(engine, 'build_session_prompt',
                        lambda **kwargs: (built.append(kwargs) or
                                          ('BUILT PROMPT', [{'type': 'text', 'text': 'refine this'}])))
    fake_oc = type('FakeOpenCode', (), {})()
    sends = []
    fake_oc.send_message = lambda oc, prompt, **kwargs: sends.append((oc, prompt, kwargs))
    fake_oc.list_messages = lambda *args, **kwargs: [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done'}]},
    ]
    fake_oc.get_messages = lambda *args, **kwargs: []
    monkeypatch.setattr(engine, 'opencode_client', fake_oc)
    monkeypatch.setattr(engine, 'render_history_block', lambda *args, **kwargs: '')

    worker = BatchWorker()
    claimed = worker._claim_pending_sessions(limit=1)
    worker._run_one(claimed[0])

    assert built and built[0]['content'] == 'refine this'
    assert built[0]['workspace_path'] == str(workspace)
    assert built[0]['attachments'] == ['notes.txt']
    assert sends == [('oc-existing', 'BUILT PROMPT', {
        'directory': str(workspace), 'agent': 'reviewer', 'model': 'model/x'})]
    assert queued['message_id']
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT content FROM ai_chat_messages "
            "WHERE session_id=%s AND role='user'", (sid,))
        rows = cur.fetchall()
        assert len(rows) == 1
        stored_content = rows[0][0]
        assert stored_content == [{'type': 'text', 'text': 'refine this'},
                                  {'type': 'file', 'name': 'notes.txt',
                                   'path': 'notes.txt'}]
        cur.execute("SELECT continue_prompt FROM ai_chat_sessions WHERE id=%s", (sid,))
        assert cur.fetchone()[0] is None


def test_worker_restart_preserves_continuation_after_process_crash(
        continuation_user, db_conn, tmp_path, monkeypatch):
    import utils.batch_engine as engine
    from utils.batch_engine import BatchWorker
    from utils.batch_repo import continue_child

    bid, sid = _seed_terminal_child(db_conn, continuation_user)
    workspace = tmp_path / 'child'
    workspace.mkdir()
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET workspace_path=%s WHERE id=%s",
                    (str(workspace), sid))
    db_conn.commit()
    continue_child(bid, sid, continuation_user, 'survive restart', [], None, None)

    fake_oc = type('FakeOpenCode', (), {})()
    fake_oc.send_message = lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    monkeypatch.setattr(engine, 'opencode_client', fake_oc)
    monkeypatch.setattr(engine, 'build_session_prompt',
                        lambda **kwargs: ('BUILT', []))
    worker = BatchWorker()
    claimed = worker._claim_pending_sessions(limit=1)
    with pytest.raises(KeyboardInterrupt):
        worker._run_one(claimed[0])

    worker._restart_audit()
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, continue_prompt FROM ai_chat_sessions WHERE id=%s", (sid,))
        status, envelope = cur.fetchone()
        assert status == 'pending'
        assert json.loads(envelope)['content'] == 'survive restart'

    claimed_again = worker._claim_pending_sessions(limit=1)
    assert claimed_again[0]['continue_prompt'] == envelope
    sends = []
    fake_oc.send_message = lambda oc, prompt, **kwargs: sends.append((oc, prompt, kwargs))
    fake_oc.list_messages = lambda *args, **kwargs: [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'recovered'}]},
    ]
    fake_oc.get_messages = lambda *args, **kwargs: []
    worker._run_one(claimed_again[0])
    assert sends == [('oc-existing', 'BUILT', {
        'directory': str(workspace), 'agent': '', 'model': ''})]
