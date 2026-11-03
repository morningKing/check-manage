import pytest
from context import ToolContext


@pytest.fixture(autouse=True)
def _deterministic_matrix(monkeypatch):
    """矩阵是全局 DB 状态：固定 kefu 白名单行为，隔离真库矩阵的不确定性。"""
    import rbac
    rbac._reset_cache()
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {
        ("kefu-guest", "query_collection"): True,
        ("kefu-guest", "list_collections"): True,
        ("kefu-guest", "read_upload"): True,
    })
    yield
    rbac._reset_cache()


def test_dispatch_blocks_public_kefu_run_python():
    from tools import _dispatch_tool
    ctx = ToolContext(session_id="s1", user_id="kefu-bot", role="kefu-guest")
    with pytest.raises(PermissionError):
        _dispatch_tool("run_python", {}, ctx)


def test_dispatch_allows_public_kefu_query(monkeypatch):
    from tools import _dispatch_tool, _TOOLS
    called = {}
    monkeypatch.setitem(_TOOLS, "query_collection",
                        (_TOOLS["query_collection"][0], lambda a, c: called.setdefault("ok", True) or {"rows": []}))
    ctx = ToolContext(session_id="s1", user_id="kefu-bot", role="kefu-guest")
    _dispatch_tool("query_collection", {}, ctx)
    assert called.get("ok")


# ---------------------------------------------------------------------------
# list_tools filtering
# ---------------------------------------------------------------------------

def _filter_specs(role: str, session_id: str = "s1"):
    """Mirror the filtering logic in _list_tools for deterministic unit testing."""
    from tools import _TOOLS
    from rbac import tool_allowed_for_context
    ctx = ToolContext(session_id=session_id, user_id="u1", role=role)
    return [spec for spec, _ in _TOOLS.values()
            if tool_allowed_for_context(spec.name, ctx)]


def test_list_tools_hides_run_python_for_kefu_guest():
    """kefu-guest's filtered spec list must not include run_python."""
    specs = _filter_specs("kefu-guest")
    names = {s.name for s in specs}
    assert "run_python" not in names, "run_python must be hidden from kefu-guest"
    # Allowlisted tools must still appear.
    for allowed in ("query_collection", "list_collections", "read_upload"):
        assert allowed in names, f"{allowed} must be visible to kefu-guest"


def test_list_tools_shows_all_for_developer(monkeypatch):
    """developer 能看到全部注册工具，但默认关闭的 register_action_check 除外。"""
    from tools import _TOOLS
    monkeypatch.delenv("MCP_ENABLE_REGISTER_ACTION_CHECK", raising=False)
    all_specs = _filter_specs("developer")
    names = {s.name for s in all_specs}
    assert "run_python" in names
    assert "register_action_check" not in names
    assert len(names) == len(_TOOLS) - 1
