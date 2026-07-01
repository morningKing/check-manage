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
