"""Tool: list_collections — returns data pages visible to the current user."""

import mcp.types as types
from mcp.server import Server
from db import get_db
from context import ToolContext


_TOOL_NAME = "list_collections"


def handle(_input: dict, ctx: ToolContext) -> list[dict]:
    """Return [{collection, label, fields[]}] for pages this role can see."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.page_id, m.name, m.roles, pc.fields
            FROM menus m
            JOIN page_configs pc ON pc.id = m.page_id
            WHERE m.page_id IS NOT NULL
            """
        )
        rows = cur.fetchall()

    result = []
    for page_id, name, roles, fields in rows:
        if ctx.role != "admin" and ctx.role not in (roles or []):
            continue
        collection = page_id[5:] if page_id.startswith("page-") else page_id
        result.append({
            "collection": collection,
            "label": name,
            "fields": fields or [],
        })
    return result


def register(server: Server) -> None:
    @server.list_tools()
    async def _list_tools():
        return [
            types.Tool(
                name=_TOOL_NAME,
                description="List business data collections visible to the caller.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        if name != _TOOL_NAME:
            raise ValueError(f"unknown tool: {name}")
        from main import _resolve_context  # imported lazily to avoid cycle
        ctx = _resolve_context()
        result = handle(arguments or {}, ctx)
        return [types.TextContent(type="text", text=str(result))]
