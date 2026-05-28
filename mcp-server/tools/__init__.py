"""Tool registry. Each tool module exposes NAME, TOOL (spec) and handle(input, ctx).

MCP allows a single list_tools / call_tool handler per server, so registration is
centralized here: list_tools returns every tool, call_tool dispatches by name.
"""

import mcp.types as types
from mcp.server import Server

from tools import list_collections, save_artifact, read_upload

_TOOLS = {
    list_collections.NAME: (list_collections.TOOL, list_collections.handle),
    save_artifact.NAME: (save_artifact.TOOL, save_artifact.handle),
    read_upload.NAME: (read_upload.TOOL, read_upload.handle),
}


def register_all(server: Server) -> None:
    @server.list_tools()
    async def _list_tools():
        return [spec for spec, _ in _TOOLS.values()]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        entry = _TOOLS.get(name)
        if entry is None:
            raise ValueError(f"unknown tool: {name}")
        from main import _resolve_context  # lazy import to avoid cycle
        ctx = _resolve_context()
        result = entry[1](arguments or {}, ctx)
        return [types.TextContent(type="text", text=str(result))]
