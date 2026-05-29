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
    # configured model is written so the agent uses it
    from config import OPENCODE_MODEL
    assert cfg['model'] == OPENCODE_MODEL


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
    # model is passed explicitly (config field alone isn't honored by OpenCode)
    args, kwargs = oc.send_prompt_async.call_args
    assert args[0] == 'oc_sess_42' and 'hello agent' in args[1]
    assert kwargs.get('model')
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
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchall.return_value = [
        ('sess_a', '会话A', None),
        ('sess_b', '会话B', None),
    ]
    resp = client.get('/ai/chat/sessions', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert [s['id'] for s in body['sessions']] == ['sess_a', 'sess_b']
    assert body['sessions'][0]['title'] == '会话A'


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
               return_value=([{'path': 'repo/new.txt', 'status': 'added'}], False)) as gc:
        resp = client.get('/ai/chat/sessions/sess_x/changes', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['changes'] == [{'path': 'repo/new.txt', 'status': 'added'}]
    assert body['truncated'] is False
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
