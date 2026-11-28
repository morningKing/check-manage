"""MCP token TTL 与会话 TTL 的解耦回归。

背景（2026-10-27 生产实锤）：AI_SESSION_TTL_HOURS 曾是 MCP token 签发/
续租的唯一来源，「历史全保留」部署把它设 0（0=永不回收，对会话/工作区
语义正确），但对 token 的 0 却是「签发即过期」——批子会话无人值守无续租，
平台 MCP 工具（query_collection/run_python/…）整体 401，账本里
check-manage_* 调用归零。

修复：拆出独立 AI_MCP_TOKEN_TTL_HOURS（0/负值钳制回 24；签发/续租点全部
换用；AI_SESSION_TTL_HOURS 保留给会话/工作区回收语义）。

这里钉住三件事：
  1. TTL=0 环境下：会话 TTL 保持 0（回收语义不变），token TTL 钳 24；
  2. 显式配置原样生效；
  3. 缺省=24，负值同钳。
"""
import importlib
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_CLAUSE = ('AI_SESSION_TTL_HOURS', 'AI_MCP_TOKEN_TTL_HOURS')


def _reload_config(monkeypatch, session_ttl=None, mcp_ttl=None):
    """在指定的 TTL 环境下重新导入 config。None=删除该环境变量。"""
    for name, val in zip(_CLAUSE, (session_ttl, mcp_ttl)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(val))
    import config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    """收尾恢复真实环境下的 config 模块，免得污染同进程后续测试。"""
    yield
    import config
    importlib.reload(config)


def test_session_ttl_zero_keeps_recycling_semantics_token_clamped(monkeypatch):
    """历史全保留部署（AI_SESSION_TTL_HOURS=0）：会话 TTL 保持 0 不变，
    token TTL 必须钳到 24——绝不允许「签发即过期」重演。"""
    cfg = _reload_config(monkeypatch, session_ttl=0)
    assert cfg.AI_SESSION_TTL_HOURS == 0            # 回收语义不受影响
    assert cfg.AI_MCP_TOKEN_TTL_HOURS == 24


def test_negative_token_ttl_clamped(monkeypatch):
    cfg = _reload_config(monkeypatch, mcp_ttl=-3)
    assert cfg.AI_MCP_TOKEN_TTL_HOURS == 24


def test_explicit_token_ttl_used_verbatim(monkeypatch):
    cfg = _reload_config(monkeypatch, mcp_ttl=48)
    assert cfg.AI_MCP_TOKEN_TTL_HOURS == 48


def test_default_token_ttl_24(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.AI_MCP_TOKEN_TTL_HOURS == 24


def test_token_issued_under_zero_session_ttl_stays_valid(monkeypatch):
    """端到端保障：AI_SESSION_TTL_HOURS=0 时按 config 走真实签发路径
    （generate_token），写库的过期时间仍须在未来——此前 = 签发即过期，
    即本缺陷的原始事故形态。"""
    cfg = _reload_config(monkeypatch, session_ttl=0)
    import utils.session_token as st

    fake_conn = MagicMock()
    monkeypatch.setattr(st, 'get_db', lambda: fake_conn)
    st.generate_token('sess_zero_ttl', cfg.AI_MCP_TOKEN_TTL_HOURS)
    exec_call = fake_conn.__enter__.return_value.cursor.return_value \
        .execute.call_args
    expires = exec_call.args[1][1]
    assert expires > datetime.now(timezone.utc), \
        'TTL=0 部署下签发的 token 已过期——MCP 工具将整体 401'
