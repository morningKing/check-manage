"""HTTP tests for /ai/chat/batches CRUD."""
import io
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


@pytest.fixture
def setup_app(db_conn):
    """Setup Flask app talking to the real dev DB.

    Earlier-running tests (test_prompt_template_routes) patch `db.pool` to a
    MagicMock and mutate the shared `mock_cursor.fetchone.return_value`. After
    those patches stop, the module-level `db.pool` may have been restored to
    None, but if any other test in the same process eagerly initialised it,
    the saved-original is a real pool — or, in some interleavings, the cursor
    state lingers. To guarantee a clean baseline, force-reset `db.pool` to
    None here so the real connection pool is freshly created against the dev
    DB. Also seed a `user-admin` row so the FK on `ai_chat_batches.user_id`
    resolves; teardown deletes it (CASCADE cleans up batches/sessions).
    """
    # If `utils.batch_repo` (or `utils.batch_engine`) was first imported while
    # an earlier test's `patch('db.get_db', ...)` was active, the module-level
    # `get_db` reference inside those modules is the MOCK function — and
    # `patch.stop()` only restores `db.get_db`, not the dangling binding in
    # the importer. Rebind explicitly so we hit the real DB.
    import db as db_module
    import utils.batch_repo as batch_repo
    import utils.batch_engine as batch_engine
    db_module.pool = None  # force the real pool to be (re)created
    batch_repo.get_db = db_module.get_db
    batch_engine.get_db = db_module.get_db

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, role, display_name) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            ('test-user-batch-routes', 'user-admin-test', 'x', 'admin', 'admin-test'),
        )
    db_conn.commit()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'test-user-batch-routes', 'username': 'admin', 'role': 'admin'})

    yield (
        app.test_client(),
        {'Authorization': f'Bearer {admin}'},
    )

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = 'test-user-batch-routes'")
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = 'test-user-batch-routes'")
        cur.execute("DELETE FROM users WHERE id = 'test-user-batch-routes'")
    db_conn.commit()


def _stage_one(client, headers, content=b'hi', name='f.txt',
               upload_session_id='upload-sess-001'):
    data = {
        'file': (io.BytesIO(content), name),
        'upload_session_id': upload_session_id,
    }
    r = client.post('/ai/chat/batches/staging/upload',
                    data=data, content_type='multipart/form-data',
                    headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()  # {name, path}


def test_create_batch_returns_201_and_seeds_sessions(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u1')
    f2 = _stage_one(client, admin_headers, name='b.txt', upload_session_id='u1')

    r = client.post('/ai/chat/batches', json={
        'name': 'batch-test',
        'prompt': 'do the thing',
        'files': [f1, f2],
    }, headers=admin_headers)
    assert r.status_code == 201
    body = r.get_json()
    assert body['batch']['name'] == 'batch-test'
    assert body['batch']['total'] == 2
    assert body['batch']['status'] == 'pending'
    assert len(body['sessions']) == 2
    seqs = sorted(s['batch_seq'] for s in body['sessions'])
    assert seqs == [0, 1]
    # cleanup
    client.delete(f'/ai/chat/batches/{body["batch"]["id"]}', headers=admin_headers)


def test_create_rejects_empty_files(setup_app):
    client, admin_headers = setup_app
    r = client.post('/ai/chat/batches',
                    json={'name': 'x', 'prompt': 'p', 'files': []},
                    headers=admin_headers)
    assert r.status_code == 400


def test_create_rejects_too_many_files(setup_app):
    client, admin_headers = setup_app
    files = [{'name': f'{i}.txt', 'path': f'batch-staging/x/y/{i}.txt'}
             for i in range(51)]
    r = client.post('/ai/chat/batches',
                    json={'name': 'x', 'prompt': 'p', 'files': files},
                    headers=admin_headers)
    assert r.status_code == 400


def test_list_returns_user_batches(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-list')
    r = client.post('/ai/chat/batches', json={
        'name': 'list-me', 'prompt': 'p', 'files': [f1],
    }, headers=admin_headers)
    bid = r.get_json()['batch']['id']
    try:
        r2 = client.get('/ai/chat/batches?page=1&pageSize=20', headers=admin_headers)
        assert r2.status_code == 200
        items = r2.get_json()['items']
        assert any(b['id'] == bid for b in items)
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)


def test_detail_returns_children(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-d')
    f2 = _stage_one(client, admin_headers, name='b.txt', upload_session_id='u-d')
    r = client.post('/ai/chat/batches', json={
        'name': 'detail', 'prompt': 'p', 'files': [f1, f2],
    }, headers=admin_headers)
    bid = r.get_json()['batch']['id']
    try:
        r2 = client.get(f'/ai/chat/batches/{bid}', headers=admin_headers)
        assert r2.status_code == 200
        body = r2.get_json()
        assert body['batch']['id'] == bid
        assert len(body['sessions']) == 2
        assert [s['batch_seq'] for s in body['sessions']] == [0, 1]
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)


def test_delete_cascades_sessions(setup_app, db_conn, tmp_path, monkeypatch):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='c.txt', upload_session_id='u-del')
    r = client.post('/ai/chat/batches', json={
        'name': 'gone', 'prompt': 'p', 'files': [f1],
    }, headers=admin_headers)
    bid = r.get_json()['batch']['id']
    r2 = client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)
    assert r2.status_code == 204
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_sessions WHERE batch_id = %s", (bid,))
        assert cur.fetchone()[0] == 0


def test_retry_failed_resets_failed_to_pending(setup_app, db_conn, tmp_path, monkeypatch):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-r')
    r = client.post('/ai/chat/batches', json={
        'name': 'retry', 'prompt': 'p', 'files': [f1],
    }, headers=admin_headers)
    bid = r.get_json()['batch']['id']
    # Manually force the child to 'failed'
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='failed' WHERE batch_id = %s",
                    (bid,))
        cur.execute("UPDATE ai_chat_batches SET failed = 1, status='partial' WHERE id = %s",
                    (bid,))
    db_conn.commit()
    try:
        r2 = client.post(f'/ai/chat/batches/{bid}/retry-failed', headers=admin_headers)
        assert r2.status_code == 200
        assert r2.get_json()['retried'] == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE batch_id = %s", (bid,))
            assert cur.fetchone()[0] == 'pending'
            cur.execute("SELECT failed, status FROM ai_chat_batches WHERE id = %s", (bid,))
            failed, status = cur.fetchone()
            assert failed == 0
            assert status == 'pending'
    finally:
        client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)


def test_create_batch_stores_agent(setup_app, tmp_path, monkeypatch, db_conn):
    """agent field is persisted and returned in the batch response."""
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='x.txt', upload_session_id='u-agent-1')
    body = {
        'name': 'agent-test',
        'prompt': 'do something',
        'agent': 'my-agent',
        'files': [f],
    }
    resp = client.post('/ai/chat/batches', json=body, headers=admin_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['batch']['agent'] == 'my-agent'

    with db_conn.cursor() as cur:
        cur.execute("SELECT agent FROM ai_chat_batches WHERE id = %s", (data['batch']['id'],))
        assert cur.fetchone()[0] == 'my-agent'


def test_create_batch_stores_model(setup_app, tmp_path, monkeypatch, db_conn):
    """model field is persisted and returned in the batch response."""
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='x.txt', upload_session_id='u-model-1')
    body = {
        'name': 'model-test',
        'prompt': 'do something',
        'model': 'anthropic/claude',
        'files': [f],
    }
    resp = client.post('/ai/chat/batches', json=body, headers=admin_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['batch']['model'] == 'anthropic/claude'

    with db_conn.cursor() as cur:
        cur.execute("SELECT model FROM ai_chat_batches WHERE id = %s", (data['batch']['id'],))
        assert cur.fetchone()[0] == 'anthropic/claude'


def test_append_adds_children_and_resets_running(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-app-1')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f1]},
                      headers=admin_headers).get_json()['batch']['id']
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET status='completed', done=1 WHERE id=%s", (bid,))
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE batch_id=%s", (bid,))
        db_conn.commit()
    f2 = _stage_one(client, admin_headers, name='c.txt', upload_session_id='u-app-2')
    r = client.post(f'/ai/chat/batches/{bid}/append', json={'files': [f2]}, headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT total, status FROM ai_chat_batches WHERE id=%s", (bid,))
        total, status = cur.fetchone()
        assert total == 2 and status == 'running'
        cur.execute("SELECT max(batch_seq) FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        assert cur.fetchone()[0] == 1   # seq continued 0 -> 1


def test_append_other_users_batch_404(setup_app, tmp_path, monkeypatch):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-app-3')
    r = client.post('/ai/chat/batches/does-not-exist/append', json={'files': [f]}, headers=admin_headers)
    assert r.status_code == 404


def _make_terminal_batch(client, headers, db_conn, monkeypatch, tmp_path, *, usid, child_status):
    """Create a 2-child batch, mark BOTH children terminal (child_status) and the
    batch counters to match (done/failed = 2). Inserts an old message on the FIRST
    child. Returns (bid, first_sid)."""
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, headers, name='r1.txt', upload_session_id=usid)
    f2 = _stage_one(client, headers, name='r2.txt', upload_session_id=usid)
    detail = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f1, f2]},
                         headers=headers).get_json()
    bid = detail['batch']['id']
    sids = [s['id'] for s in sorted(detail['sessions'], key=lambda x: x['batch_seq'])]
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status=%s, opencode_session_id='oc-old', "
                    "last_message_preview='old', error_message='e' WHERE batch_id=%s",
                    (child_status, bid))
        cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES ('m-old-1', %s, 'user', '[]'::jsonb)", (sids[0],))
        done = 2 if child_status == 'completed' else 0
        failed = 2 if child_status == 'failed' else 0
        cur.execute("UPDATE ai_chat_batches SET status='completed', done=%s, failed=%s WHERE id=%s",
                    (done, failed, bid))
        db_conn.commit()
    return bid, sids[0]


def test_reexecute_completed_child_clears_context(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    bid, sid = _make_terminal_batch(client, admin_headers, db_conn, monkeypatch, tmp_path,
                                    usid='u-rx-1', child_status='completed')
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, opencode_session_id, last_message_preview, error_message "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        st, oc, prev, err = cur.fetchone()
        assert st == 'pending' and oc is None and prev is None and err is None
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT done, status FROM ai_chat_batches WHERE id=%s", (bid,))
        done, bstatus = cur.fetchone()
        assert done == 1 and bstatus == 'running'


def test_reexecute_failed_child_decrements_failed(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    bid, sid = _make_terminal_batch(client, admin_headers, db_conn, monkeypatch, tmp_path,
                                    usid='u-rx-2', child_status='failed')
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT failed, status FROM ai_chat_batches WHERE id=%s", (bid,))
        failed, bstatus = cur.fetchone()
        assert failed == 1 and bstatus == 'running'


def test_reexecute_running_child_409(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-rx-3')
    detail = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                         headers=admin_headers).get_json()
    bid = detail['batch']['id']; sid = detail['sessions'][0]['id']
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running' WHERE id=%s", (sid,)); db_conn.commit()
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 409


def test_reexecute_missing_child_404(setup_app):
    client, admin_headers = setup_app
    r = client.post('/ai/chat/batches/nope/sessions/nope/reexecute', headers=admin_headers)
    assert r.status_code == 404


def test_patch_updates_agent_and_model(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-cfg-1')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                      headers=admin_headers).get_json()['batch']['id']
    r = client.patch(f'/ai/chat/batches/{bid}',
                     json={'agent': 'plan', 'model': 'mimo/mimo-v2.5'}, headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json()['batch']['agent'] == 'plan'
    assert r.get_json()['batch']['model'] == 'mimo/mimo-v2.5'
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent, model FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() == ('plan', 'mimo/mimo-v2.5')


def test_patch_empty_clears_to_null(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-cfg-2')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'agent': 'plan',
                                                'files': [f]}, headers=admin_headers).get_json()['batch']['id']
    r = client.patch(f'/ai/chat/batches/{bid}', json={'agent': '', 'model': ''}, headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent, model FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() == (None, None)


def test_patch_missing_batch_404(setup_app):
    client, admin_headers = setup_app
    r = client.patch('/ai/chat/batches/nope', json={'model': 'x'}, headers=admin_headers)
    assert r.status_code == 404
