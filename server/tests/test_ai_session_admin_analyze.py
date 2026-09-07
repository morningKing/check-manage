"""Regression tests for POST /ai/chat/admin/sessions/v2/<sid>/analyze (轨迹分析).

The endpoint 500'd in production with `NameError: name 'internal_enabled' is
not defined` — a typo shipped with the internal-MCP toggle (commit 64a4495)
because no test exercised the path past `write_opencode_config`. These tests
drive the full success path with every external dependency patched, so a
name-level mistake in the handler fails here instead of in production.

Patching note: analyze_session imports its collaborators INSIDE the function
body (`from utils.workspace import ...` etc.), so those must be patched at
their SOURCE modules — a `routes.ai_session_admin.write_opencode_config`
patch would never be seen. Only `admin_get_session_detail`/`get_db` are
module-level from-imports of the route module and are patched there.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


def _fake_db(conn):
    @contextmanager
    def _ctx():
        yield conn
    return _ctx


def _ok_urlopen():
    """urlopen mock that satisfies `with urlopen(...) as resp: resp.status == 200`."""
    m = MagicMock()
    m.return_value.__enter__.return_value.status = 200
    return m


DETAIL = {
    'id': 'sess_target',
    'user_id': 'u1',
    'username': 'alice',
    'title': '目标会话',
    'status': 'completed',
    'workspace_path': '/tmp/ws',
    'created_at': None,
    'last_active_at': None,
}


def test_analyze_success(client, admin_headers, mock_conn):
    oc_client = MagicMock()
    oc_client.create_session.return_value = 'oc_sess_1'
    with patch('urllib.request.urlopen', _ok_urlopen()), \
         patch('utils.mcp_servers.internal_mcp_enabled', return_value=True), \
         patch('utils.mcp_servers.enabled_mcp_config', return_value={}), \
         patch('routes.ai_session_admin.admin_get_session_detail', return_value=dict(DETAIL)), \
         patch('routes.ai_session_admin.get_db', _fake_db(mock_conn)), \
         patch('utils.workspace.create_session_workspace', return_value='/tmp/ws2'), \
         patch('utils.workspace.write_opencode_config') as write_cfg, \
         patch('utils.session_token.generate_token', return_value='tok123'), \
         patch('config.get_default_chat_model', return_value={'provider': 'p', 'model': 'm'}), \
         patch('utils.global_skills.inject_global_skills'), \
         patch('utils.opencode_client.OpenCodeClient', return_value=oc_client), \
         patch('utils.chat_persist.ensure_listener') as ensure_listener:
        resp = client.post('/ai/chat/admin/sessions/v2/sess_target/analyze',
                           headers=admin_headers)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['analysisSessionId'].startswith('sess_')
    # The exact regression point: the old code passed an undefined name here
    # and died with NameError -> 500 before ever reaching OpenCode.
    assert write_cfg.call_count == 1
    assert write_cfg.call_args.kwargs['include_internal'] is True
    assert write_cfg.call_args.kwargs['mcp_name'] == 'check-manage'
    # listener attaches before the prompt is dispatched (ordering fix C-2 family)
    assert ensure_listener.call_count == 1
    oc_client.send_prompt_async.assert_called_once()
    # prompt persisted + oc session id recorded
    executed = [c.args[0] for c in mock_conn.cursor().execute.call_args_list]
    assert any('INSERT INTO ai_chat_messages' in sql for sql in executed)
    assert any('UPDATE ai_chat_sessions SET opencode_session_id' in sql for sql in executed)


def test_analyze_502_when_mcp_down(client, admin_headers):
    with patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
        resp = client.post('/ai/chat/admin/sessions/v2/sess_x/analyze',
                           headers=admin_headers)
    assert resp.status_code == 502
    assert 'MCP' in resp.get_json()['error']


def test_analyze_409_when_internal_mcp_disabled(client, admin_headers):
    with patch('urllib.request.urlopen', _ok_urlopen()), \
         patch('utils.mcp_servers.internal_mcp_enabled', return_value=False):
        resp = client.post('/ai/chat/admin/sessions/v2/sess_x/analyze',
                           headers=admin_headers)
    assert resp.status_code == 409
    assert '内置 MCP' in resp.get_json()['error']


def test_analyze_404_unknown_session(client, admin_headers):
    with patch('urllib.request.urlopen', _ok_urlopen()), \
         patch('utils.mcp_servers.internal_mcp_enabled', return_value=True), \
         patch('routes.ai_session_admin.admin_get_session_detail', return_value=None):
        resp = client.post('/ai/chat/admin/sessions/v2/nope/analyze',
                           headers=admin_headers)
    assert resp.status_code == 404
