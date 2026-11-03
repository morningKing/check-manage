"""Tests for tools.register_action_check(设计 §5.2 入口 C)。"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="sess-1", user_id="u1", role=role)


def _fake_db():
    captured = []
    conn = MagicMock()
    cur = MagicMock()
    cur.execute.side_effect = lambda sql, params=None: captured.append((sql, params))
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    return _get, captured


def test_registers_normalized_checks():
    from tools.register_action_check import handle
    get_db, captured = _fake_db()
    with patch('tools.register_action_check.get_db', get_db):
        res = handle({'checks': [
            {'name': '克隆目标仓库', 'tool': 'bash',
             'args_pattern': 'git clone\\s+\\S*acme/y'},
            {'name': '读知识库', 'tool': 'read',
             'args_pattern': 'docs/k.md', 'min_count': 2, 'scope': 'session'},
        ]}, _ctx())
    assert res['registered'] == 2
    assert res['session_id'] == 'sess-1'
    assert all('INSERT INTO action_expectations' in sql for sql, _ in captured)
    # source='mcp',scope/session/name 逐条生效
    assert captured[0][1][:3] == ('tree', 'sess-1', '克隆目标仓库')
    assert captured[1][1][:3] == ('session', 'sess-1', '读知识库')
    assert captured[0][1][5] == 1 and captured[1][1][5] == 2


def test_rejects_invalid_regex():
    from tools.register_action_check import handle, RegisterActionCheckError
    with pytest.raises(RegisterActionCheckError, match='合法正则'):
        handle({'checks': [{'name': 'x', 'tool': 'bash', 'args_pattern': '(['}]},
               _ctx())


def test_rejects_empty_checks():
    from tools.register_action_check import handle, RegisterActionCheckError
    with pytest.raises(RegisterActionCheckError):
        handle({'checks': []}, _ctx())


def test_kefu_guest_blocked_by_allowlist():
    """公开客服身份不在 KEFU_TOOL_ALLOWLIST,分发口自动拒绝。"""
    from rbac import tool_allowed
    assert not tool_allowed('register_action_check', 'kefu-guest')
    assert tool_allowed('register_action_check', 'developer')


# ---------------------------------------------------------------------------
# 默认关闭 + 批子会话永久禁用（2026-11-03：模型不能自动给批任务加门禁）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _deterministic_matrix(monkeypatch):
    import rbac
    rbac._reset_cache()
    monkeypatch.setattr(rbac, '_fetch_rules', lambda: {})
    yield
    rbac._reset_cache()


def test_disabled_by_default_for_interactive(monkeypatch):
    monkeypatch.delenv('MCP_ENABLE_REGISTER_ACTION_CHECK', raising=False)
    from rbac import tool_allowed_for_context
    assert not tool_allowed_for_context('register_action_check', _ctx())


def test_enabled_for_interactive_when_env_on(monkeypatch):
    monkeypatch.setenv('MCP_ENABLE_REGISTER_ACTION_CHECK', '1')
    import rbac
    monkeypatch.setattr(rbac, '_session_in_batch', lambda _sid: False)
    assert rbac.tool_allowed_for_context('register_action_check', _ctx())


def test_batch_child_denied_even_when_env_on(monkeypatch):
    monkeypatch.setenv('MCP_ENABLE_REGISTER_ACTION_CHECK', '1')
    import rbac
    monkeypatch.setattr(rbac, '_session_in_batch', lambda _sid: True)
    assert not rbac.tool_allowed_for_context('register_action_check', _ctx())


def test_dispatch_denied_by_default(monkeypatch):
    monkeypatch.delenv('MCP_ENABLE_REGISTER_ACTION_CHECK', raising=False)
    from tools import _dispatch_tool
    with pytest.raises(PermissionError):
        _dispatch_tool('register_action_check', {'checks': []}, _ctx())


def test_tool_meta_hides_disabled_tool(monkeypatch):
    monkeypatch.delenv('MCP_ENABLE_REGISTER_ACTION_CHECK', raising=False)
    from tools import tool_meta
    names = {m['name'] for m in tool_meta()}
    assert 'register_action_check' not in names


def test_tool_meta_shows_tool_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv('MCP_ENABLE_REGISTER_ACTION_CHECK', '1')
    from tools import tool_meta
    names = {m['name'] for m in tool_meta()}
    assert 'register_action_check' in names
