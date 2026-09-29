"""HTTP tests for /ai/chat/batches CRUD."""
import io
import os
import sys
import pytest
from pathlib import Path

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
    # P0 10.4: non-terminal batches refuse a bare delete...
    r409 = client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)
    assert r409.status_code == 409
    assert r409.get_json()['error']['code'] == 'BATCH_NOT_TERMINAL'
    # ...and accept "stop then delete" (children cascade away)
    r2 = client.delete(f'/ai/chat/batches/{bid}?stop=1', headers=admin_headers)
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
        # 两个子会话各自终态 + 唯一 token（session_token 全局唯一约束）
        cur.execute("UPDATE ai_chat_sessions SET status=%s, opencode_session_id='oc-old', "
                    "last_message_preview='old', error_message='e', "
                    "workspace_path=%s, session_token=%s WHERE id=%s",
                    (child_status, str(tmp_path / 'ws' / 'user' / sids[0]),
                     f'tok-rx-{usid}-0', sids[0]))
        cur.execute("UPDATE ai_chat_sessions SET status=%s, opencode_session_id='oc-old-2', "
                    "last_message_preview='old2', error_message='e2', "
                    "workspace_path=%s, session_token=%s WHERE id=%s",
                    (child_status, str(tmp_path / 'ws' / 'user' / sids[1]),
                     f'tok-rx-{usid}-1', sids[1]))
        cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES ('m-old-1', %s, 'user', '[]'::jsonb)", (sids[0],))
        # 上一轮残留：工作区文件（根散文件/outputs/）+ 变更登记 + 子代理
        # （含消息）+ 工具调用账本 + 门禁期望——重执行应全部清零
        ws = Path(tmp_path / 'ws' / 'user' / sids[0])
        (ws / 'outputs').mkdir(parents=True, exist_ok=True)
        (ws / 'uploads').mkdir(parents=True, exist_ok=True)
        (ws / 'junk-leftover.txt').write_text('上一轮残留', encoding='utf-8')
        (ws / 'outputs' / 'old-report.md').write_text('旧报告', encoding='utf-8')
        (ws / 'uploads' / 'r1.txt').write_text('输入应保留', encoding='utf-8')
        cur.execute("INSERT INTO ai_chat_session_files (session_id, path, status) "
                    "VALUES (%s, 'junk-leftover.txt', 'added')", (sids[0],))
        cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent) "
                    "VALUES (%s, %s, 'general')", (f'sub-{sids[0][:8]}', sids[0]))
        cur.execute("INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                    "VALUES (%s, %s, 'assistant', '[]'::jsonb)",
                    (f'subm-{sids[0][:8]}', f'sub-{sids[0][:8]}'))
        cur.execute("INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state) "
                    "VALUES ('oc-old', %s, %s, 'general', 'old-part', 'bash', 'x', 'completed')",
                    (sids[0], f'sub-{sids[0][:8]}'))
        cur.execute("INSERT INTO action_expectations (scope_type, scope_id, name, tool, "
                    "  args_pattern, min_count, source) "
                    "VALUES ('session', %s, '旧期望', 'bash', 'x', 1, 'batch')", (sids[0],))
        # 复用锚点：重新执行应清除，使新一轮委派新建子代理会话
        cur.execute("INSERT INTO ai_subagent_pins (id, root_session_id, batch_id, agent, task_id) "
                    "VALUES (%s, %s, %s, 'general', 'ses_old_sub')",
                    (f'spin-{sids[0][:8]}', sids[0], bid))
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
        cur.execute("SELECT status, opencode_session_id, last_message_preview, error_message, "
                    "retry_count, continue_prompt, active_turn_id "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        st, oc, prev, err, retry, cprompt, atid = cur.fetchone()
        assert st == 'pending' and oc is None and prev is None and err is None
        # 重新执行=全新一轮：自动重试预算重置，无残留继续词/活跃 turn
        assert retry == 0 and cprompt is None and atid is None
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT done, status FROM ai_chat_batches WHERE id=%s", (bid,))
        done, bstatus = cur.fetchone()
        assert done == 1 and bstatus == 'running'

    # 工作区清空并初始化（2026-09-29）：上一轮残留清零、骨架与输入恢复、
    # opencode.json（per-session MCP 配置）重写
    cur = db_conn.cursor()
    cur.execute("SELECT workspace_path FROM ai_chat_sessions WHERE id=%s", (sid,))
    ws = Path(cur.fetchone()[0])
    assert not (ws / 'junk-leftover.txt').exists(), '上一轮根残留应被清空'
    assert not (ws / 'outputs' / 'old-report.md').exists(), '上一轮产出应被清空'
    # uploads（用户输入）随备份-恢复保留：reexecute 后立即可用，不依赖
    # 批暂存区（staged 有 24h TTL，过期批次的输入也不能丢）
    assert (ws / 'uploads' / 'r1.txt').read_text(encoding='utf-8') == '输入应保留'
    assert (ws / '.git').exists() and (ws / 'AGENTS.md').exists()
    assert (ws / 'opencode.json').exists(), 'MCP 配置应随重置重写'
    # 上一轮的关联行清零：变更登记/子代理/账本/门禁期望
    cur.execute("SELECT count(*) FROM ai_chat_session_files WHERE session_id=%s", (sid,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM ai_chat_subtasks WHERE root_session_id=%s", (sid,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM ai_chat_subtask_messages WHERE subtask_id LIKE %s",
                (f'sub-{sid[:8]}%',))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM agent_tool_calls WHERE root_session_id=%s", (sid,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM action_expectations WHERE scope_id=%s", (sid,))
    assert cur.fetchone()[0] == 0
    # 复用锚点清除：新轮委派将新建子代理会话（真正重新执行）
    cur.execute("SELECT count(*) FROM ai_subagent_pins WHERE root_session_id=%s", (sid,))
    assert cur.fetchone()[0] == 0


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


def test_patch_persists_action_checks_and_syncs_children(setup_app, db_conn):
    """编辑动作门禁回归:PATCH 显式传 action_checks 必须同时落
    ai_chat_batches.action_checks 列(编辑对话框重开时的预填来源)并同步
    非终态子任务期望;只写 action_expectations 行而不落列,保存后再打开
    设置门禁就"消失"了。空数组=清除门禁,列也要回到 NULL。"""
    client, admin_headers = setup_app
    f = _stage_one(client, admin_headers, name='g.txt', upload_session_id='u-gate-1')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                      headers=admin_headers).get_json()['batch']['id']
    checks = [{'name': '执行脚本', 'tool': 'bash',
               'args_pattern': 'run\.sh', 'min_count': 1}]
    try:
        r = client.patch(f'/ai/chat/batches/{bid}',
                         json={'action_checks': checks}, headers=admin_headers)
        assert r.status_code == 200, r.get_data(as_text=True)
        # validate_checks 归一化后落库(补 scope/check_type/require_state 等默认),
        # 用户显式设置的字段必须原样回读
        saved = r.get_json()['batch']['action_checks']
        assert len(saved) == 1
        assert {k: saved[0][k] for k in ('name', 'tool', 'args_pattern', 'min_count')} == checks[0]
        with db_conn.cursor() as cur:
            cur.execute("SELECT action_checks FROM ai_chat_batches WHERE id=%s", (bid,))
            row = cur.fetchone()[0]
            assert {k: row[0][k] for k in ('name', 'tool', 'args_pattern', 'min_count')} == checks[0]
            # 创建即播种 pending 子会话 → 期望应同步登记到子会话上
            cur.execute(
                "SELECT DISTINCT e.name FROM action_expectations e "
                "JOIN ai_chat_sessions s ON e.scope_id = s.id "
                "WHERE s.batch_id = %s AND e.source = 'batch'", (bid,))
            assert [row[0] for row in cur.fetchall()] == ['执行脚本']

        # 再次保存为空数组 = 关闭门禁:列清 NULL,子任务期望同步删除
        r = client.patch(f'/ai/chat/batches/{bid}',
                         json={'action_checks': []}, headers=admin_headers)
        assert r.status_code == 200
        assert r.get_json()['batch']['action_checks'] is None
        with db_conn.cursor() as cur:
            cur.execute("SELECT action_checks FROM ai_chat_batches WHERE id=%s", (bid,))
            assert cur.fetchone()[0] is None
            cur.execute(
                "SELECT count(*) FROM action_expectations e "
                "JOIN ai_chat_sessions s ON e.scope_id = s.id "
                "WHERE s.batch_id = %s AND e.source = 'batch'", (bid,))
            assert cur.fetchone()[0] == 0
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM action_expectations e USING ai_chat_sessions s "
                        "WHERE e.scope_id = s.id AND s.batch_id = %s", (bid,))
        db_conn.commit()


def test_patch_invalid_action_checks_400_keeps_old_value(setup_app, db_conn):
    """校验失败(坏正则)必须 400 且不半更新:列保持原值,期望也不动。"""
    client, admin_headers = setup_app
    f = _stage_one(client, admin_headers, name='g2.txt', upload_session_id='u-gate-2')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                      headers=admin_headers).get_json()['batch']['id']
    good = [{'name': '读知识', 'tool': 'read', 'args_pattern': 'spec\.md'}]
    try:
        assert client.patch(f'/ai/chat/batches/{bid}', json={'action_checks': good},
                            headers=admin_headers).status_code == 200
        r = client.patch(f'/ai/chat/batches/{bid}',
                         json={'action_checks': [{'name': '坏', 'tool': 'bash',
                                                  'args_pattern': '([bad'}]},
                         headers=admin_headers)
        assert r.status_code == 400
        with db_conn.cursor() as cur:
            cur.execute("SELECT action_checks FROM ai_chat_batches WHERE id=%s", (bid,))
            row = cur.fetchone()[0]
            assert {k: row[0][k] for k in ('name', 'tool', 'args_pattern')} == good[0]
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM action_expectations e USING ai_chat_sessions s "
                        "WHERE e.scope_id = s.id AND s.batch_id = %s", (bid,))
        db_conn.commit()


def _mk_config_batch(client, headers, name='cfg-test',
                     upload_session_id='u-cfg-1'):
    f = _stage_one(client, headers, name='x.txt', upload_session_id=upload_session_id)
    resp = client.post('/ai/chat/batches', json={
        'name': name, 'prompt': '原始提示词', 'files': [f],
    }, headers=headers)
    assert resp.status_code == 201
    return resp.get_json()['batch']['id']


def test_patch_config_updates_name_and_prompt(setup_app, tmp_path, monkeypatch, db_conn):
    """编辑对话框（2026-09-29）：PATCH 支持改名称与提示词。"""
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    bid = _mk_config_batch(client, admin_headers, name='cfg-old',
                           upload_session_id='u-cfg-2')

    r = client.patch(f'/ai/chat/batches/{bid}', headers=admin_headers, json={
        'name': 'cfg-new-name', 'prompt': '全新提示词 v2',
        'agent': '', 'model': '',
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['batch']['name'] == 'cfg-new-name'
    assert r.get_json()['batch']['prompt'] == '全新提示词 v2'

    with db_conn.cursor() as cur:
        cur.execute("SELECT name, prompt FROM ai_chat_batches WHERE id = %s", (bid,))
        assert cur.fetchone() == ('cfg-new-name', '全新提示词 v2')


def test_patch_config_rejects_empty_name_and_prompt(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    bid = _mk_config_batch(client, admin_headers, name='cfg-keep',
                           upload_session_id='u-cfg-3')

    r1 = client.patch(f'/ai/chat/batches/{bid}', headers=admin_headers,
                      json={'name': '   '})
    assert r1.status_code == 400 and 'name' in r1.get_json()['error']
    r2 = client.patch(f'/ai/chat/batches/{bid}', headers=admin_headers,
                      json={'prompt': ''})
    assert r2.status_code == 400 and 'prompt' in r2.get_json()['error']
    # 名称超长
    r3 = client.patch(f'/ai/chat/batches/{bid}', headers=admin_headers,
                      json={'name': 'x' * 201})
    assert r3.status_code == 400

    # 校验失败不半更新：原值保持
    with db_conn.cursor() as cur:
        cur.execute("SELECT name, prompt FROM ai_chat_batches WHERE id = %s", (bid,))
        assert cur.fetchone() == ('cfg-keep', '原始提示词')


def test_patch_config_without_name_prompt_keeps_them(setup_app, tmp_path, monkeypatch, db_conn):
    """只改 agent 等配置时，未传的 name/prompt 保持原值（_UNSET 语义）。"""
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    bid = _mk_config_batch(client, admin_headers, name='cfg-untouched',
                           upload_session_id='u-cfg-4')
    r = client.patch(f'/ai/chat/batches/{bid}', headers=admin_headers,
                     json={'agent': 'some-agent', 'model': ''})
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT name, prompt FROM ai_chat_batches WHERE id = %s", (bid,))
        assert cur.fetchone() == ('cfg-untouched', '原始提示词')
