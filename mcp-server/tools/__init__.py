"""Tool registry. Each tool module exposes NAME, TOOL (spec) and handle(input, ctx).

MCP allows a single list_tools / call_tool handler per server, so registration is
centralized here: list_tools returns every tool, call_tool dispatches by name.
"""

import mcp.types as types
from mcp.server import Server

from tools import (
    list_collections, save_artifact, read_upload, export_collection_excel, run_python,
    query_collection, read_data_file, list_export_scripts, run_export_script,
    memory,
)

_TOOLS = {
    list_collections.NAME: (list_collections.TOOL, list_collections.handle),
    save_artifact.NAME: (save_artifact.TOOL, save_artifact.handle),
    read_upload.NAME: (read_upload.TOOL, read_upload.handle),
    export_collection_excel.NAME: (export_collection_excel.TOOL, export_collection_excel.handle),
    run_python.NAME: (run_python.TOOL, run_python.handle),
    query_collection.NAME: (query_collection.TOOL, query_collection.handle),
    read_data_file.NAME: (read_data_file.TOOL, read_data_file.handle),
    list_export_scripts.NAME: (list_export_scripts.TOOL, list_export_scripts.handle),
    run_export_script.NAME: (run_export_script.TOOL, run_export_script.handle),
    memory.SEARCH.name: (memory.SEARCH, memory.handle_search),
    memory.ADD.name: (memory.ADD, memory.handle_add),
    memory.DELETE.name: (memory.DELETE, memory.handle_delete),
}


def tool_specs() -> list[tuple[str, str]]:
    """(name, description) for every registered tool — used by GET /tools."""
    return [(spec.name, spec.description or "") for spec, _ in _TOOLS.values()]


def _dispatch_tool(name: str, arguments: dict, ctx):
    """Module-level dispatch helper (testable without MCP server).

    Enforces the central tool allowlist before calling the handler.
    Raises ValueError for unknown tools, PermissionError when the caller's
    role is not permitted to invoke the tool.
    """
    from rbac import tool_allowed
    entry = _TOOLS.get(name)
    if entry is None:
        raise ValueError(f"unknown tool: {name}")
    if not tool_allowed(name, ctx.role):
        raise PermissionError(f"tool '{name}' not available for this session")
    result = entry[1](arguments, ctx)
    return [types.TextContent(type="text", text=str(result))]


def register_all(server: Server) -> None:
    @server.list_tools()
    async def _list_tools():
        try:
            from main import _resolve_context  # lazy import to avoid cycle
            from rbac import tool_allowed
            ctx = _resolve_context()
            return [spec for spec, _ in _TOOLS.values() if tool_allowed(spec.name, ctx.role)]
        except Exception:
            # If context resolution fails (e.g. non-kefu session, missing header),
            # fall back to returning all specs so legitimate sessions are unaffected.
            return [spec for spec, _ in _TOOLS.values()]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        from main import _resolve_context  # lazy import to avoid cycle
        ctx = _resolve_context()
        return _dispatch_tool(name, arguments or {}, ctx)
