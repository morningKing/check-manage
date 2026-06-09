"""Tests for server/routes/ai_chat.py."""

import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor, tmp_path):
    fake_db = _make_mock_db(mock_conn)
    fake_client = MagicMock()
    fake_client.create_session.return_value = "oc_sess_42"
    fake_client.register_mcp.return_value = None

    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.ai_chat.get_db', fake_db),
        patch('utils.session_token.get_db', fake_db),
        patch('utils.chat_persist.get_db', fake_db),
        patch('routes.ai_chat.OpenCodeClient', return_value=fake_client),
        patch('config.AI_WORKSPACE_ROOT', str(tmp_path)),
        patch('routes.ai_chat.AI_WORKSPACE_ROOT', str(tmp_path)),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    dev = create_token({'id': 'user-1', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'user-2', 'username': 'g', 'role': 'guest'})

    yield (
        app.test_client(), mock_cursor, fake_client,
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
        tmp_path,
    )

    for p in patches:
        p.stop()


def test_create_session_201_writes_config_and_binds_directory(setup):
    import json as _json
    from pathlib import Path
    client, cursor, oc, dev_h, _, ws_root = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=dev_h)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['id'].startswith('sess_')
    assert body['title'] == '新会话'
    assert 'workspacePath' in body
    # OpenCode session created with directory bound to the workspace
    oc.create_session.assert_called_once()
    assert oc.create_session.call_args.kwargs.get('directory') == body['workspacePath']
    # MCP is wired via opencode.json (with the session token), not an API call
    assert not oc.register_mcp.called
    cfg = _json.loads((Path(body['workspacePath']) / 'opencode.json').read_text(encoding='utf-8'))
    assert 'token=' in cfg['mcp']['check-manage']['url']
    # If an explicit OPENCODE_MODEL is configured, it must show up in the
    # opencode.json; if it's empty (the default), the key is omitted so
    # OpenCode picks from the first connected provider.
    from config import OPENCODE_MODEL
    if OPENCODE_MODEL:
        assert cfg['model'] == OPENCODE_MODEL
    else:
        assert 'model' not in cfg


def test_create_session_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=guest_h)
    assert resp.status_code == 403


def test_send_message_persists_user_and_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # Make the session lookup succeed
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hello agent'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    # model is passed explicitly to the OpenCode call. The exact value depends
    # on body.model ∪ OPENCODE_MODEL config; both can be empty (then OpenCode
    # picks its own default). The important contract is that the `model`
    # kwarg is always present so OpenCode receives a deterministic value.
    args, kwargs = oc.send_prompt_async.call_args
    assert args[0] == 'oc_sess_42' and 'hello agent' in args[1]
    assert 'model' in kwargs
    # directory must be the session workspace so the agent's tools run there
    assert kwargs.get('directory') == '/tmp/ws'

    # An INSERT into ai_chat_messages must have happened
    inserts = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("INSERT INTO ai_chat_messages" in s for s in inserts)


def test_send_message_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None  # not found for this user
    resp = client.post(
        '/ai/chat/sessions/sess_other/messages',
        json={'content': 'hi'},
        headers=dev_h,
    )
    assert resp.status_code == 404


def test_send_message_uses_body_model_when_provided(setup):
    """When the composer dropdown picks a non-default model, that model id
    must flow into the OpenCode call (and override OPENCODE_MODEL)."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hi', 'model': 'anthropic/claude-3.5-sonnet'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs.get('model') == 'anthropic/claude-3.5-sonnet'


def test_list_models_flattens_connected_providers(setup):
    """GET /ai/chat/models returns provider/model pairs for connected providers,
    with the configured default surfaced separately."""
    client, _, oc, dev_h, _, _ = setup
    oc.list_providers.return_value = {
        'all': [
            {'id': 'p1', 'name': 'Provider One',
             'models': {'m1': {'name': 'Model 1'}, 'm2': {'name': 'Model 2'}}},
            {'id': 'p2', 'name': 'Provider Two',
             'models': {'mX': {'name': 'X'}}},
            {'id': 'p3', 'name': 'Not Connected',
             'models': {'mZ': {'name': 'Z'}}},  # connected map excludes p3
        ],
        'default': {'p1': 'm1', 'p2': 'mX'},
        'connected': {'p1': True, 'p2': True, 'p3': False},
    }
    resp = client.get('/ai/chat/models', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    ids = [m['id'] for m in body['models']]
    assert 'p1/m1' in ids and 'p1/m2' in ids and 'p2/mX' in ids
    assert 'p3/mZ' not in ids  # not connected
    # labels carry the human-readable form
    p1m1 = next(m for m in body['models'] if m['id'] == 'p1/m1')
    assert p1m1['label'] == 'Provider One / Model 1'


def test_get_messages_returns_history(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # owner check + history fetch
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    cursor.fetchall.return_value = [
        ('msg_1', 'user',      [{'type': 'text', 'text': 'hi'}],   None),
        ('msg_2', 'assistant', [{'type': 'text', 'text': 'hey'}],  None),
    ]
    resp = client.get('/ai/chat/sessions/sess_x/messages', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['messages']) == 2
    assert body['messages'][0]['role'] == 'user'


def test_loading_session_touches_last_active_and_extends_token(setup):
    """Every route that loads a session also bumps last_active_at and
    extends token_expires_at — keeps MCP token alive + powers the sidebar
    recency sort."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    cursor.fetchall.return_value = []
    client.get('/ai/chat/sessions/sess_x/messages', headers=dev_h)
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('UPDATE ai_chat_sessions' in s
               and 'last_active_at = NOW()' in s
               and 'token_expires_at = NOW()' in s for s in sqls), sqls


def test_unknown_session_does_not_touch(setup):
    """If _load_session_for_user finds no row, no UPDATE should fire."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    client.get('/ai/chat/sessions/sess_nope/messages', headers=dev_h)
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any('UPDATE ai_chat_sessions' in s and 'token_expires_at' in s for s in sqls)


def test_sse_events_maps_real_opencode_vocabulary_and_persists_on_idle(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')

    # Real OpenCode event shapes: data-only frames with {type, properties}.
    oc.subscribe_events.return_value = iter([
        {'event': 'message.updated', 'data': {'type': 'message.updated',
            'properties': {'info': {'id': 'm1', 'role': 'assistant', 'sessionID': 'oc_sess_42'}}}},
        {'event': 'message.part.updated', 'data': {'type': 'message.part.updated',
            'properties': {'part': {'id': 'p1', 'type': 'text', 'text': 'hi',
                                    'messageID': 'm1', 'sessionID': 'oc_sess_42'}}}},
        {'event': 'session.idle', 'data': {'type': 'session.idle',
            'properties': {'sessionID': 'oc_sess_42'}}},
    ])

    resp = client.get('/ai/chat/sessions/sess_x/events', headers=dev_h)
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('text/event-stream')
    body = b''.join(resp.response).decode('utf-8')
    assert 'event: message.part.updated' in body
    assert 'event: session.idle' in body
    assert '"text": "hi"' in body
    # events must be subscribed scoped to the session's workspace directory
    assert oc.subscribe_events.call_args.kwargs.get('directory') == '/tmp/ws'
    # session.idle persisted the accumulated assistant text
    inserts = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("INSERT INTO ai_chat_messages" in s for s in inserts)


def test_sse_events_persists_tool_parts_on_idle(setup):
    """Tool calls (e.g. query_collection) must be persisted alongside text so the
    rendered result survives a reload — not just the assistant's prose."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    oc.subscribe_events.return_value = iter([
        {'event': 'message.updated', 'data': {'type': 'message.updated',
            'properties': {'info': {'id': 'm1', 'role': 'assistant', 'sessionID': 'oc_sess_42'}}}},
        {'event': 'message.part.updated', 'data': {'type': 'message.part.updated',
            'properties': {'part': {'id': 'p1', 'type': 'text', 'text': '查到 2 条',
                                    'messageID': 'm1', 'sessionID': 'oc_sess_42'}}}},
        {'event': 'message.part.updated', 'data': {'type': 'message.part.updated',
            'properties': {'part': {'id': 'p2', 'type': 'tool', 'tool': 'query_collection',
                                    'messageID': 'm1', 'sessionID': 'oc_sess_42',
                                    'state': {'status': 'completed',
                                              'output': '{"mode":"table","total":2}'}}}}},
        {'event': 'session.idle', 'data': {'type': 'session.idle',
            'properties': {'sessionID': 'oc_sess_42'}}},
    ])
    resp = client.get('/ai/chat/sessions/sess_x/events', headers=dev_h)
    b''.join(resp.response)  # drain the generator so idle-persistence runs
    persisted = None
    for c in cursor.execute.call_args_list:
        sql = c.args[0]
        if "INSERT INTO ai_chat_messages" in sql and "'assistant'" in sql:
            persisted = json.loads(c.args[1][2])
    assert persisted is not None
    types = [p['type'] for p in persisted]
    assert 'text' in types and 'tool_use' in types
    tool = next(p for p in persisted if p['type'] == 'tool_use')
    assert tool['name'] == 'query_collection'
    assert tool['result'] == '{"mode":"table","total":2}'


def test_sse_events_auth_via_query_token(setup):
    """EventSource can't set headers, so the SSE route accepts ?access_token=."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    oc.subscribe_events.return_value = iter([])
    jwt = dev_h['Authorization'].split(' ', 1)[1]
    # no Authorization header — token only in the query string
    resp = client.get(f'/ai/chat/sessions/sess_x/events?access_token={jwt}')
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('text/event-stream')


def test_sse_events_401_without_any_auth(setup):
    client, *_ = setup
    resp = client.get('/ai/chat/sessions/sess_x/events')
    assert resp.status_code == 401


def test_sse_events_filters_other_sessions(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    oc.subscribe_events.return_value = iter([
        {'event': 'message.part.updated', 'data': {'type': 'message.part.updated',
            'properties': {'part': {'id': 'p9', 'type': 'text', 'text': 'OTHER',
                                    'messageID': 'mX', 'sessionID': 'someone_else'}}}},
    ])
    resp = client.get('/ai/chat/sessions/sess_x/events', headers=dev_h)
    body = b''.join(resp.response).decode('utf-8')
    assert 'OTHER' not in body


def test_delete_session_cleans_everything(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    # Pre-create the workspace dir so cleanup has something to remove
    target = ws_root / 'user-1' / 'sess_x' / 'uploads'
    target.mkdir(parents=True, exist_ok=True)

    resp = client.delete('/ai/chat/sessions/sess_x', headers=dev_h)
    assert resp.status_code == 204
    oc.delete_session.assert_called_once_with('oc_sess_42')
    assert not (ws_root / 'user-1' / 'sess_x').exists()

    # DB updates: revoke token + soft-flag (or DELETE)
    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('UPDATE ai_chat_sessions' in s and 'status' in s for s in statements) or \
           any('DELETE FROM ai_chat_sessions' in s for s in statements)


def test_list_sessions_returns_user_sessions(setup):
    """SQL selects (id, title, last_active_at, batch_id, batch_input_file) so
    that batch-children get synthesized "[批] <file>" titles. The mock row
    must match that 5-tuple shape."""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchall.return_value = [
        ('sess_a', '会话A', None, None, None),                          # regular session
        ('sess_b', None, None, 'batch-1', 'uploads/req-A.txt'),         # batch child → synthesized title
    ]
    resp = client.get('/ai/chat/sessions', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert [s['id'] for s in body['sessions']] == ['sess_a', 'sess_b']
    assert body['sessions'][0]['title'] == '会话A'
    assert body['sessions'][1]['title'] == '[批] req-A.txt'


def test_rename_session_updates_title(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    resp = client.patch('/ai/chat/sessions/sess_x', json={'title': '新标题'}, headers=dev_h)
    assert resp.status_code == 200
    assert resp.get_json()['title'] == '新标题'
    assert any('UPDATE ai_chat_sessions SET title' in c.args[0] for c in cursor.execute.call_args_list)


def test_upload_file_saves_into_uploads(setup):
    import io
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsupload'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.post(
        '/ai/chat/sessions/sess_x/files',
        data={'file': (io.BytesIO(b'print(1)'), 'script.py')},
        content_type='multipart/form-data',
        headers=dev_h,
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['path'] == 'uploads/script.py'
    assert (ws / 'uploads' / 'script.py').read_bytes() == b'print(1)'


def test_send_message_inlines_uploaded_text_for_agent(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsinline'
    (ws / 'uploads').mkdir(parents=True, exist_ok=True)
    (ws / 'uploads' / 'a.txt').write_text('SECRET-DATA', encoding='utf-8')
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', str(ws))
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看看这个文件', 'attachments': ['uploads/a.txt']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    # the prompt sent to the agent must include the inlined file content
    args, kwargs = oc.send_prompt_async.call_args
    assert 'SECRET-DATA' in args[1]
    assert '看看这个文件' in args[1]
    # stored user message keeps a file chip
    inserts = [c.args for c in cursor.execute.call_args_list if 'INSERT INTO ai_chat_messages' in c.args[0]]
    assert any('"type": "file"' in a[1][2] for a in inserts)


def test_list_files_returns_uploads_and_outputs(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wslist'
    (ws / 'uploads').mkdir(parents=True, exist_ok=True)
    (ws / 'outputs').mkdir(parents=True, exist_ok=True)
    (ws / 'uploads' / 'in.txt').write_text('hi', encoding='utf-8')
    (ws / 'outputs' / 'out.py').write_text('print(1)', encoding='utf-8')
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.get('/ai/chat/sessions/sess_x/files', headers=dev_h)
    assert resp.status_code == 200
    files = {f['path']: f for f in resp.get_json()['files']}
    assert files['uploads/in.txt']['dir'] == 'uploads'
    assert files['outputs/out.py']['dir'] == 'outputs'


def test_download_file_returns_content(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsdl'
    (ws / 'outputs').mkdir(parents=True, exist_ok=True)
    (ws / 'outputs' / 'report.txt').write_text('DOWNLOAD-OK', encoding='utf-8')
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    jwt = dev_h['Authorization'].split(' ', 1)[1]
    resp = client.get(f'/ai/chat/sessions/sess_x/files/download?path=outputs/report.txt&access_token={jwt}')
    assert resp.status_code == 200
    assert b'DOWNLOAD-OK' in resp.data


def test_download_file_rejects_path_traversal(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wstrav'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    jwt = dev_h['Authorization'].split(' ', 1)[1]
    resp = client.get(f'/ai/chat/sessions/sess_x/files/download?path=../../secret.txt&access_token={jwt}')
    assert resp.status_code == 400


def test_list_files_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None  # not owned by this user
    resp = client.get('/ai/chat/sessions/sess_other/files', headers=dev_h)
    assert resp.status_code == 404


def test_send_message_export_intent_fallback_writes_xlsx(setup):
    from unittest.mock import patch
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsexport'
    (ws / 'outputs').mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    # stub the export so we don't need real DB collections; assert it's invoked + noted
    fake = {'path': 'outputs/inspection-case-x.xlsx', 'rows': 2, 'columns': 6, 'label': '巡检用例'}
    with patch('routes.ai_chat.resolve_collection_from_text', return_value=('inspection-case', '巡检用例')), \
         patch('routes.ai_chat.export_collection_to_xlsx', return_value=fake) as exp:
        resp = client.post(
            '/ai/chat/sessions/sess_x/messages',
            json={'content': '请把巡检用例数据导出成 excel 文件'},
            headers=dev_h,
        )
    assert resp.status_code == 202
    exp.assert_called_once()
    # the agent prompt should mention the produced file so it tells the user
    args, _ = oc.send_prompt_async.call_args
    assert 'outputs/inspection-case-x.xlsx' in args[1]


def test_send_message_non_export_does_not_export(setup):
    from unittest.mock import patch
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsnoexport'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    with patch('routes.ai_chat.export_collection_to_xlsx') as exp:
        resp = client.post(
            '/ai/chat/sessions/sess_x/messages',
            json={'content': '你好，帮我解释一下什么是巡检'},
            headers=dev_h,
        )
    assert resp.status_code == 202
    exp.assert_not_called()


def test_run_script_executes_and_returns_outputs(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsrun'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    code = "import os\nos.makedirs('outputs',exist_ok=True)\nopen('outputs/r.txt','w').write('done')\nprint('ran')\n"
    resp = client.post('/ai/chat/sessions/sess_x/run', json={'code': code}, headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['exitCode'] == 0
    assert 'ran' in body['stdout']
    assert 'outputs/r.txt' in body['outputFiles']


def test_list_changes_returns_git_changes(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    with patch('routes.ai_chat.git_changes',
               return_value=([{'path': 'repo/new.txt', 'status': 'added'}], False, True)) as gc:
        resp = client.get('/ai/chat/sessions/sess_x/changes', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['changes'] == [{'path': 'repo/new.txt', 'status': 'added'}]
    assert body['truncated'] is False
    assert body['ok'] is True
    assert gc.call_args[0][0] == '/tmp/ws'  # called with the session workspace


def test_list_changes_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.get('/ai/chat/sessions/sess_other/changes', headers=dev_h)
    assert resp.status_code == 404


def test_run_script_requires_code(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsrun2'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.post('/ai/chat/sessions/sess_x/run', json={'code': '  '}, headers=dev_h)
    assert resp.status_code == 400


def test_run_script_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/run', json={'code': 'print(1)'}, headers=guest_h)
    assert resp.status_code == 403


def test_run_script_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.post('/ai/chat/sessions/sess_o/run', json={'code': 'print(1)'}, headers=dev_h)
    assert resp.status_code == 404


def test_delete_message_onwards_drops_from_created_at(setup):
    from datetime import datetime, timezone
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws'),
        (datetime(2026, 5, 30, tzinfo=timezone.utc),),
    ]
    cursor.rowcount = 3
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_dead', headers=dev_h)
    assert resp.status_code == 200
    assert resp.get_json() == {'deleted': 3}
    # asserts: (1) we looked up created_at for the target msg, (2) DELETE used it
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('SELECT created_at FROM ai_chat_messages' in s for s in sqls)
    assert any('DELETE FROM ai_chat_messages' in s and 'created_at' in s for s in sqls)
    oc.abort_session.assert_called_once()  # best-effort abort


def test_delete_message_unknown_msg_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws'),
        None,  # message not found
    ]
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_nope', headers=dev_h)
    assert resp.status_code == 404
    assert resp.get_json()['code'] == 'MESSAGE_NOT_FOUND'


def test_delete_message_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_x', headers=guest_h)
    assert resp.status_code == 403


def test_abort_session_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/abort', headers=dev_h)
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}
    a, k = oc.abort_session.call_args
    assert a[0] == 'oc_sess'
    assert k.get('directory') == '/tmp/ws'


def test_abort_session_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/abort', headers=guest_h)
    assert resp.status_code == 403


def test_abort_session_other_users_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.post('/ai/chat/sessions/sess_other/abort', headers=dev_h)
    assert resp.status_code == 404


def test_list_mcp_services_merges_servers_and_tools(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    oc.list_mcp.return_value = {'check-manage': {'status': 'connected'}}
    tools_resp = MagicMock()
    tools_resp.json.return_value = [{'name': 'list_collections', 'description': 'List collections.'}]
    with patch('routes.ai_chat.requests.get', return_value=tools_resp):
        resp = client.get('/ai/chat/sessions/sess_x/mcp', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['servers'] == [{
        'name': 'check-manage',
        'status': 'connected',
        'tools': [{'name': 'list_collections', 'description': 'List collections.'}],
    }]
    assert oc.list_mcp.call_args[0][0] == '/tmp/ws'  # scoped to the workspace


def test_list_mcp_services_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.get('/ai/chat/sessions/sess_other/mcp', headers=dev_h)
    assert resp.status_code == 404


def test_list_mcp_services_opencode_down_returns_empty(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_mcp.side_effect = Exception('boom')
    resp = client.get('/ai/chat/sessions/sess_x/mcp', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['servers'] == []
    assert body['error'] == 'opencode unavailable'


def test_list_mcp_services_tools_unavailable_yields_empty_tools(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_mcp.return_value = {'check-manage': {'status': 'connected'}}
    with patch('routes.ai_chat.requests.get', side_effect=Exception('boom')):
        resp = client.get('/ai/chat/sessions/sess_x/mcp', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['servers'] == [{'name': 'check-manage', 'status': 'connected', 'tools': []}]


def test_list_commands_merges_commands_and_skills(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_commands.return_value = [{'name': 'init', 'description': 'a', 'source': 'command', 'template': 't'}]
    oc.list_skills.return_value = [{'name': 'clawhub', 'description': 'b', 'location': 'L', 'content': 'C'}]
    resp = client.get('/ai/chat/sessions/sess_x/commands', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['commands'] == [{'name': 'init', 'description': 'a'}]
    assert body['skills'] == [{'name': 'clawhub', 'description': 'b'}]
    assert oc.list_commands.call_args[0][0] == '/tmp/ws'
    assert oc.list_skills.call_args[0][0] == '/tmp/ws'


def test_list_commands_degrades_on_error(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_commands.side_effect = Exception('boom')
    oc.list_skills.return_value = [{'name': 'clawhub', 'description': 'b'}]
    resp = client.get('/ai/chat/sessions/sess_x/commands', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['commands'] == []
    assert body['skills'] == [{'name': 'clawhub', 'description': 'b'}]


def test_run_command_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/command',
                       json={'command': 'init', 'arguments': 'go'}, headers=dev_h)
    assert resp.status_code == 202
    a, k = oc.run_command.call_args
    assert a[0] == 'oc_sess'
    assert a[1] == 'init'
    assert a[2] == 'go'  # positional: run_command(oc_sess, command, arguments, ...)
    assert k.get('directory') == '/tmp/ws'


def test_run_command_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/command', json={'command': 'init'}, headers=guest_h)
    assert resp.status_code == 403


def test_run_command_requires_command(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/command', json={'command': '  '}, headers=dev_h)
    assert resp.status_code == 400


def test_upload_skill_success(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    with patch('routes.ai_chat.extract_skill_zip',
               return_value={'name': 'hello', 'path': '.opencode/skills/hello'}) as ex:
        from io import BytesIO
        resp = client.post('/ai/chat/sessions/sess_x/skills',
                           data={'file': (BytesIO(b'fake-zip'), 'hello.zip')},
                           headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 201
    assert resp.get_json() == {'name': 'hello', 'path': '.opencode/skills/hello'}
    assert ex.call_args[0][0] == '/tmp/ws'   # workspace path passed first


def test_upload_skill_guest_403(setup):
    from io import BytesIO
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/skills',
                       data={'file': (BytesIO(b'z'), 'h.zip')},
                       headers=guest_h, content_type='multipart/form-data')
    assert resp.status_code == 403


def test_upload_skill_other_users_session_404(setup):
    from io import BytesIO
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.post('/ai/chat/sessions/sess_other/skills',
                       data={'file': (BytesIO(b'z'), 'h.zip')},
                       headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 404


def test_upload_skill_no_file_400(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/skills',
                       data={}, headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['code'] == 'BAD_FILE'


def test_upload_skill_util_error_400(setup):
    from io import BytesIO
    from utils.skill_upload import SkillUploadError
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    with patch('routes.ai_chat.extract_skill_zip',
               side_effect=SkillUploadError('INVALID_SKILL_ZIP', 'missing SKILL.md')):
        resp = client.post('/ai/chat/sessions/sess_x/skills',
                           data={'file': (BytesIO(b'z'), 'h.zip')},
                           headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['code'] == 'INVALID_SKILL_ZIP'
    assert body['error'] == 'missing SKILL.md'


def test_file_diff_modified_returns_diff_hunks(setup):
    """GET /sessions/:id/diff returns status='modified' and a unified diff with
    @@ hunks for a file that has been committed then modified."""
    import subprocess
    client, cursor, oc, dev_h, _, ws_root = setup
    # Create a real git repo inside the tmp workspace
    ws = ws_root / 'wsdiff'
    ws.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', str(ws)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(ws), 'config', 'user.email', 'test@test.com'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(ws), 'config', 'user.name', 'Test'], check=True, capture_output=True)
    # Create and commit a file
    target = ws / 'hello.txt'
    target.write_text('line1\nline2\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(ws), 'add', 'hello.txt'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(ws), 'commit', '-m', 'init'], check=True, capture_output=True)
    # Modify the file (creates a working-tree change)
    target.write_text('line1\nline2\nline3\n', encoding='utf-8')

    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.get('/ai/chat/sessions/sess_x/diff?path=hello.txt', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'modified'
    assert '@@' in body['diff']


def test_file_diff_rejects_path_traversal(setup):
    """GET /sessions/:id/diff with a path that escapes the workspace returns 400."""
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wstravdiff'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.get('/ai/chat/sessions/sess_x/diff?path=../../etc/passwd', headers=dev_h)
    assert resp.status_code == 400
    assert resp.get_json()['code'] == 'BAD_PATH'


def test_file_diff_missing_path_returns_400_path_required(setup):
    """GET /sessions/:id/diff with no ?path= query param returns 400 PATH_REQUIRED."""
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsnopathparam'
    ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', str(ws))
    resp = client.get('/ai/chat/sessions/sess_x/diff', headers=dev_h)
    assert resp.status_code == 400
    assert resp.get_json()['code'] == 'PATH_REQUIRED'


def test_send_message_starts_persist_listener(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsmsg'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_42', 'active', str(ws))
    with patch('routes.ai_chat.ensure_listener') as ens, \
         patch('routes.ai_chat.OpenCodeClient'):
        resp = client.post('/ai/chat/sessions/sess_x/messages',
                           json={'content': 'hi'}, headers=dev_h)
    assert resp.status_code == 202
    ens.assert_called_once()
    assert ens.call_args[0][0] == 'sess_x'
    assert ens.call_args[0][1] == 'oc_42'
    assert ens.call_args[0][2] == str(ws)


def test_run_command_starts_persist_listener(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wscmd'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_42', 'active', str(ws))
    with patch('routes.ai_chat.ensure_listener') as ens, \
         patch('routes.ai_chat.OpenCodeClient'):
        resp = client.post('/ai/chat/sessions/sess_x/command',
                           json={'command': 'help', 'arguments': ''}, headers=dev_h)
    assert resp.status_code == 202
    ens.assert_called_once()
    assert ens.call_args[0][0] == 'sess_x'
    assert ens.call_args[0][1] == 'oc_42'
    assert ens.call_args[0][2] == str(ws)


def test_delete_session_stops_persist_listener(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsdel'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_42', 'active', str(ws))
    with patch('routes.ai_chat.stop_listener') as stp, \
         patch('routes.ai_chat.OpenCodeClient'), \
         patch('routes.ai_chat.revoke_token'), \
         patch('routes.ai_chat.cleanup_session_workspace'):
        resp = client.delete('/ai/chat/sessions/sess_x', headers=dev_h)
    assert resp.status_code == 204
    stp.assert_called_once_with('sess_x')


def test_list_agents_filters_internal_and_subagents(setup):
    client, cursor, oc, dev_h, _, _ = setup
    oc.list_agents.return_value = [
        {"name": "build", "description": "default", "mode": "primary"},
        {"name": "plan", "description": "no edits", "mode": "primary"},
        {"name": "compaction", "description": "", "mode": "primary"},
        {"name": "title", "description": "", "mode": "primary"},
        {"name": "summary", "description": "", "mode": "primary"},
        {"name": "general", "description": "subagent", "mode": "subagent"},
        {"name": "explore", "description": "subagent", "mode": "subagent"},
    ]
    resp = client.get('/ai/chat/agents', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    names = [a['name'] for a in body['agents']]
    assert names == ['build', 'plan']
    assert body['default'] == 'build'


def test_list_agents_degrades_on_opencode_error(setup):
    client, cursor, oc, dev_h, _, _ = setup
    oc.list_agents.side_effect = Exception('boom')
    resp = client.get('/ai/chat/agents', headers=dev_h)
    assert resp.status_code == 502
    body = resp.get_json()
    assert body['agents'] == []
    assert body['subagents'] == []
    assert body['default'] is None


def test_send_message_passes_body_agent_to_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hi', 'attachments': [], 'agent': 'plan'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs.get('agent') == 'plan'
    assert resp.get_json().get('agent') == 'plan'


def test_list_agents_returns_subagents_separately(setup):
    client, cursor, oc, dev_h, _, _ = setup
    oc.list_agents.return_value = [
        {"name": "build", "description": "default", "mode": "primary"},
        {"name": "plan", "description": "no edits", "mode": "primary"},
        {"name": "compaction", "description": "", "mode": "primary"},
        {"name": "general", "description": "general subagent", "mode": "subagent"},
        {"name": "explore", "description": "explore subagent", "mode": "subagent"},
    ]
    resp = client.get('/ai/chat/agents', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert [a['name'] for a in body['agents']] == ['build', 'plan']
    assert [a['name'] for a in body['subagents']] == ['general', 'explore']
    assert body['default'] == 'build'
