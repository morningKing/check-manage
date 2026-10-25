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
    from tools import tool_specs
    with TestClient(app) as c:
        resp = c.get("/tools")
    assert resp.status_code == 200
    body = resp.json()
    by_name = {t["name"]: t for t in body}
    # endpoint returns every registered tool, each with a string description
    assert set(by_name) == {n for n, _ in tool_specs()}
    assert "list_collections" in by_name
    assert isinstance(by_name["list_collections"]["description"], str)


def test_tools_endpoint_returns_group_meta():
    """前端治理/分组展示依赖的 /tools 分组契约（Task 6 Step 5）。

    不进 with（lifespan 的 StreamableHTTPSessionManager 同进程只能 run 一次，
    与上一个用例的 with TestClient 冲突）；/tools 是普通路由不依赖 lifespan。"""
    from main import app
    client = TestClient(app)
    resp = client.get("/tools")
    by_name = {t["name"]: t for t in resp.json()}
    qc = by_name["query_collection"]
    assert qc["group"] == "data-query"
    assert qc["group_name"] == "数据查询"
    assert qc["access"] == "read"
    assert qc["group_order"] == 1
    rp = by_name["run_python"]
    assert rp["group"] == "exec" and rp["access"] == "write"
