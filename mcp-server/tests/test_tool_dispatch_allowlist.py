import pytest
from context import ToolContext


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

def _filter_specs(role: str):
    """Mirror the filtering logic in _list_tools for deterministic unit testing."""
    from tools import _TOOLS
    from rbac import tool_allowed
    return [spec for spec, _ in _TOOLS.values() if tool_allowed(spec.name, role)]


def test_list_tools_hides_run_python_for_kefu_guest():
    """kefu-guest's filtered spec list must not include run_python."""
    specs = _filter_specs("kefu-guest")
    names = {s.name for s in specs}
    assert "run_python" not in names, "run_python must be hidden from kefu-guest"
    # Allowlisted tools must still appear.
    for allowed in ("query_collection", "list_collections", "read_upload"):
        assert allowed in names, f"{allowed} must be visible to kefu-guest"


def test_list_tools_shows_all_for_developer():
    """developer role must see every registered tool."""
    from tools import _TOOLS
    all_specs = _filter_specs("developer")
    assert len(all_specs) == len(_TOOLS), "developer must see all tools"
    names = {s.name for s in all_specs}
    assert "run_python" in names
