"""Tool: export_collection_excel — export a business data collection's rows to a
real .xlsx file in the current session's outputs/ dir (a result file, not a
script). Mirrors the Flask-side export-intent fallback so the agent can also
trigger it. RBAC by the collection's menu roles."""

import os
import json
from datetime import datetime

import mcp.types as types
from openpyxl import Workbook
from db import get_db
from context import ToolContext


NAME = "export_collection_excel"

TOOL = types.Tool(
    name=NAME,
    description=(
        "把某个业务数据集合(collection)的真实数据导出为 Excel 文件,写入本次会话的"
        "产出目录(outputs/),用户可直接下载。当用户要求导出/下载某类数据为 excel 时调用。"
        "参数:collection=集合标识(如 inspection-case;可先用 list_collections 查看)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "数据集合标识，如 inspection-case"},
        },
        "required": ["collection"],
        "additionalProperties": False,
    },
)


class ExportError(Exception):
    pass


def _workspace_for_session(session_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT workspace_path FROM ai_chat_sessions WHERE id = %s AND status = 'active'",
            (session_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _menu_and_fields(collection: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.page_id, m.name, m.roles, pc.fields FROM menus m "
            "JOIN page_configs pc ON pc.id = m.page_id "
            "WHERE m.page_id = %s OR m.page_id = %s",
            (collection, 'page-' + collection),
        )
        return cur.fetchone()


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return '是' if v else '否'
    return v


def handle(input: dict, ctx: ToolContext) -> dict:
    collection = (input or {}).get("collection") or ""
    if not collection:
        raise ExportError("collection is required")

    menu = _menu_and_fields(collection)
    if not menu:
        raise ExportError(f"未找到数据集合：{collection}")
    page_id, label, roles, fields = menu
    if ctx.role != "admin" and ctx.role not in (roles or []):
        raise ExportError(f"无权限导出：{label}")

    keys, headers = [], []
    for f in (fields or []):
        fn = f.get("fieldName")
        if fn:
            keys.append(fn)
            headers.append(f.get("label") or fn)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM dynamic_data WHERE collection = %s "
            "AND (branch_id = 'main' OR branch_id IS NULL) ORDER BY created_at",
            (collection,),
        )
        rows = [r[0] or {} for r in cur.fetchall()]

    if not keys:
        seen = []
        for d in rows:
            for k in d:
                if k not in seen:
                    seen.append(k)
        keys, headers = seen, seen

    ws_path = _workspace_for_session(ctx.session_id)
    if not ws_path:
        raise ExportError("session workspace not found")

    wb = Workbook()
    sh = wb.active
    sh.title = (label or collection)[:31]
    sh.append(headers or ["(空)"])
    for d in rows:
        sh.append([_cell(d.get(k)) for k in keys])

    out_dir = os.path.join(ws_path, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{collection}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb.save(os.path.join(out_dir, fname))
    return {"saved": True, "path": f"outputs/{fname}", "rows": len(rows),
            "columns": len(keys), "label": label}
