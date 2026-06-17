"""Tool: list_export_scripts — 列出当前用户可访问、已绑定的导出脚本。"""
import mcp.types as types
from db import get_db
from context import ToolContext

NAME = "list_export_scripts"

TOOL = types.Tool(
    name=NAME,
    description=(
        "列出当前可用的导出脚本（仅已绑定到数据页/菜单、且你的角色有权访问的）。"
        "返回 id/name/description/target/scope。当用户想用导出脚本导出数据时，"
        "先用本工具找到合适的脚本 id，再调 run_export_script。"
    ),
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
)


def _roles_for_collection(cur, collection):
    cur.execute("SELECT roles FROM menus WHERE page_id = %s OR page_id = %s",
                (collection, f'page-{collection}'))
    r = cur.fetchall()
    return (r[0][0] or []) if r else []


def _roles_for_menu(cur, menu_id):
    cur.execute("SELECT roles FROM menus WHERE id = %s", (menu_id,))
    r = cur.fetchall()
    return (r[0][0] or []) if r else []


def handle(input: dict, ctx: ToolContext) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, scope, bound_collection, bound_menu_id "
            "FROM export_scripts WHERE bound_collection IS NOT NULL OR bound_menu_id IS NOT NULL "
            "ORDER BY name")
        rows = cur.fetchall()
        out = []
        for sid, name, desc, scope, bc, bm in rows:
            if bm:
                roles = _roles_for_menu(cur, bm); target = f"menu:{bm}"
            else:
                roles = _roles_for_collection(cur, bc); target = f"page:{bc}"
            if ctx.role != "admin" and ctx.role not in (roles or []):
                continue
            out.append({"id": sid, "name": name, "description": desc or "",
                        "target": target, "scope": scope})
    return {"scripts": out, "count": len(out)}
