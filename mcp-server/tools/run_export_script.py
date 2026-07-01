"""Tool: run_export_script — 运行一个【已绑定】导出脚本，结果写入会话 outputs/ 并返回摘要。
目标由脚本绑定推导（专项专用），调用方只给 script_id。"""
import os
from datetime import datetime

import mcp.types as types
from db import get_db
from context import ToolContext
import tools._server_imports  # noqa: F401 — 把 server/ 加进 sys.path
from utils.export_runner import (
    execute_bound_export, ExportBindingError, ExportPermissionError, SCRIPT_SELECT,
)

NAME = "run_export_script"

_EXT = {'json': '.json', 'csv': '.csv', 'xml': '.xml', 'txt': '.txt', 'html': '.html'}

TOOL = types.Tool(
    name=NAME,
    description=(
        "运行一个【已绑定】的导出脚本，把导出结果写入本次会话的产出目录(outputs/)，用户可直接下载，"
        "并返回文件名/大小/前若干字预览。先用 list_export_scripts 拿到 script_id。"
        "参数：script_id=脚本标识。"
    ),
    inputSchema={
        "type": "object",
        "properties": {"script_id": {"type": "string", "description": "导出脚本 id"}},
        "required": ["script_id"],
        "additionalProperties": False,
    },
)


class RunExportError(Exception):
    pass


def _workspace(cur, session_id):
    cur.execute("SELECT workspace_path FROM ai_chat_sessions WHERE id = %s AND status = 'active'",
                (session_id,))
    row = cur.fetchone()
    return row[0] if row else None


def handle(input: dict, ctx: ToolContext) -> dict:
    from rbac import is_public_kefu
    if is_public_kefu(ctx.role):
        raise RunExportError("not available for public customer-service sessions")

    script_id = (input or {}).get("script_id") or ""
    if not script_id:
        raise RunExportError("script_id is required")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {SCRIPT_SELECT} FROM export_scripts WHERE id = %s", (script_id,))
        row = cur.fetchone()
        if not row:
            raise RunExportError(f"脚本不存在：{script_id}")
        scope, bound_collection, bound_menu_id = row[4] or 'page', row[5], row[6]
        if not bound_collection and not bound_menu_id:
            raise RunExportError("该脚本未绑定数据页/菜单，请先在管理端绑定后再调用")

        ws = _workspace(cur, ctx.session_id)
        if not ws:
            raise RunExportError("session workspace not found")

        try:
            if scope == 'menu':
                files = execute_bound_export(cur, row, menu_id=bound_menu_id, role=ctx.role)
            else:
                files = [execute_bound_export(cur, row, collection=bound_collection, role=ctx.role)]
        except ExportBindingError as e:
            raise RunExportError(str(e))
        except ExportPermissionError as e:
            raise RunExportError(str(e))

    out_dir = os.path.join(ws, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    for result_bytes, filename, content_type in files:
        raw_name = filename or f"{script_id}-{ts}{_EXT.get(row[3], '.dat')}"
        safe = os.path.basename(raw_name.replace('\\', '/')) or f"{script_id}-{ts}{_EXT.get(row[3], '.dat')}"
        path = os.path.join(out_dir, safe)
        with open(path, 'wb') as f:
            f.write(result_bytes)
        preview = ''
        if (content_type or '').startswith(('text/', 'application/json')):
            preview = result_bytes[:1000].decode('utf-8', errors='replace')
        saved.append({"path": f"outputs/{safe}", "filename": safe,
                      "size": len(result_bytes), "preview": preview})

    first = saved[0]
    return {"saved": True, "path": first["path"], "filename": first["filename"],
            "outputFormat": row[3], "files": saved, "preview": first["preview"]}
