"""Tool: list_collections — returns data pages visible to the current user."""

import mcp.types as types
from db import get_db
from context import ToolContext


NAME = "list_collections"

TOOL = types.Tool(
    name=NAME,
    description="List business data collections visible to the caller.",
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
)


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
