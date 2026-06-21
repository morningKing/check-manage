"""Tools: per-user long-term memory (search/add/delete). Routed through Flask's
internal endpoints so Flask stays the sole mem0/Chroma owner. Scope = ctx.user_id."""
import mcp.types as types
from context import ToolContext
import memory_client

SEARCH = types.Tool(
    name="memory_search",
    description="检索当前用户的长期记忆（偏好/习惯/事实）。参数：query(必填)、limit(可选,默认5)。",
    inputSchema={"type": "object", "properties": {
        "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
)
ADD = types.Tool(
    name="memory_add",
    description="为当前用户新增一条长期记忆。参数：text(必填，要记住的事实/偏好)。",
    inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)
DELETE = types.Tool(
    name="memory_delete",
    description="删除一条长期记忆。参数：memoryId(必填，来自 memory_search 的 id)。",
    inputSchema={"type": "object", "properties": {"memoryId": {"type": "string"}}, "required": ["memoryId"]},
)


def handle_search(args: dict, ctx: ToolContext) -> str:
    rows = memory_client.search(ctx.user_id, args.get("query", ""), int(args.get("limit", 5)))
    if not rows:
        return "（无相关记忆）"
    return "\n".join(f"- [{r.get('id')}] {r.get('memory')}" for r in rows)


def handle_add(args: dict, ctx: ToolContext) -> str:
    memory_client.add(ctx.user_id, args.get("text", ""))
    return "已记住。"


def handle_delete(args: dict, ctx: ToolContext) -> str:
    memory_client.delete(args.get("memoryId", ""))
    return "已删除。"
