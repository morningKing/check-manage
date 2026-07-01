"""Tool: save_artifact — write a file into the current session's outputs/ dir
so the user can download it. Lets the agent explicitly produce files instead of
only emitting code blocks."""

import os
import mcp.types as types
from db import get_db
from context import ToolContext
from rbac import is_readonly


NAME = "save_artifact"

TOOL = types.Tool(
    name=NAME,
    description=(
        "把一个文件保存到本次会话的产出目录(outputs/)，用户可在界面下载。"
        "当你为用户生成脚本、配置或文档时调用它。"
        "参数：filename=文件名(如 check_disk.py)，content=文件完整内容。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名，不含路径"},
            "content": {"type": "string", "description": "文件完整文本内容"},
        },
        "required": ["filename", "content"],
        "additionalProperties": False,
    },
)


class SaveArtifactError(Exception):
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


def handle(input: dict, ctx: ToolContext) -> dict:
    if is_readonly(ctx.role):
        raise SaveArtifactError("read-only role is not allowed to write files")
    filename = (input or {}).get("filename") or ""
    content = (input or {}).get("content")
    if not filename or content is None:
        raise SaveArtifactError("filename and content are required")
    # Path safety: only a bare filename, written under outputs/
    name = os.path.basename(filename.replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise SaveArtifactError("invalid filename")

    ws = _workspace_for_session(ctx.session_id)
    if not ws:
        raise SaveArtifactError("session workspace not found")

    out_dir = os.path.join(ws, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, name)
    # ensure dest stays inside out_dir
    if os.path.commonpath([os.path.realpath(dest), os.path.realpath(out_dir)]) != os.path.realpath(out_dir):
        raise SaveArtifactError("path escapes outputs/")

    text = content if isinstance(content, str) else str(content)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)

    return {"saved": True, "path": f"outputs/{name}", "bytes": len(text.encode("utf-8"))}
