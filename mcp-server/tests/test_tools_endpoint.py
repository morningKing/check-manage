"""GET /tools exposes the MCP tool registry (name + description) — no auth,
no business data. Used by the chat's MCP 服务 block."""

from fastapi.testclient import TestClient


def test_tool_specs_lists_registered_tools():
    from tools import tool_specs
    names = {n for n, _ in tool_specs()}
    assert {"list_collections", "query_collection", "run_python"} <= names
    # every entry carries a (possibly empty) string description
    assert all(isinstance(d, str) for _, d in tool_specs())


def test_tools_endpoint_returns_name_and_description():
    from main import app
    with TestClient(app) as c:
        resp = c.get("/tools")
    assert resp.status_code == 200
    body = resp.json()
    by_name = {t["name"]: t for t in body}
    assert "list_collections" in by_name
    assert by_name["list_collections"]["description"]  # non-empty
