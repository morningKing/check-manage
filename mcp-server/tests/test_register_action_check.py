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
