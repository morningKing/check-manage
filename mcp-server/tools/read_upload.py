"""Tool: read_upload — read a file the user uploaded into the current session's
uploads/ dir. Needed because OpenCode's own file tools resolve relative paths
against the server's launch cwd, not the session workspace, so the agent can't
otherwise locate uploaded files. Call with no filename to list what's available."""

import os
import mcp.types as types
from db import get_db
from context import ToolContext


NAME = "read_upload"

_MAX_BYTES = 200_000

TOOL = types.Tool(
    name=NAME,
    description=(
        "读取用户在本次会话上传的文件内容（位于会话的 uploads/ 目录）。"
        "不带 filename 调用可列出当前会话已上传的所有文件；"
        "带 filename 调用则返回该文件的文本内容。"
        "参数：filename=要读取的文件名(可选，如 config.txt)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "要读取的上传文件名，不含路径；省略则列出所有上传文件"},
        },
        "additionalProperties": False,
    },
)


class ReadUploadError(Exception):
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


def _list_uploads(uploads_dir: str):
    if not os.path.isdir(uploads_dir):
        return []
    return sorted(
        f for f in os.listdir(uploads_dir)
        if os.path.isfile(os.path.join(uploads_dir, f))
    )


def handle(input: dict, ctx: ToolContext) -> dict:
    ws = _workspace_for_session(ctx.session_id)
    if not ws:
        raise ReadUploadError("session workspace not found")
    uploads_dir = os.path.join(ws, "uploads")

    filename = (input or {}).get("filename") or ""
    files = _list_uploads(uploads_dir)

    # No filename → list available uploads so the agent can pick.
    if not filename:
        return {"files": files}

    name = os.path.basename(filename.replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise ReadUploadError("invalid filename")

    path = os.path.join(uploads_dir, name)
    # confine to uploads/
    if os.path.commonpath([os.path.realpath(path), os.path.realpath(uploads_dir)]) != os.path.realpath(uploads_dir):
        raise ReadUploadError("path escapes uploads/")
    if not os.path.isfile(path):
        return {"found": False, "error": f"未找到文件 {name}", "available": files}

    size = os.path.getsize(path)
    if size > _MAX_BYTES:
        return {"found": True, "name": name, "truncated": True, "size": size,
                "error": f"文件过大({size} 字节)，仅支持读取 {_MAX_BYTES} 字节以内的文本文件"}
    try:
        with open(path, "rb") as f:
            content = f.read().decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return {"found": True, "name": name, "binary": True, "size": size,
                "error": "该文件不是 UTF-8 文本，无法读取内容"}

    return {"found": True, "name": name, "size": size, "content": content}
