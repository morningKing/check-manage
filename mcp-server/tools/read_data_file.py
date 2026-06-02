"""Tool: read_data_file — read a file attached to a data-page record's file/image field.

Use case: the user uploaded an inspection guideline (or any binary/text) as a
file field on a dynamic_data row. The agent wants to inspect its contents.
Without this tool the agent can list collections and rows via query_collection,
but the file-field value is just a JSONB array of {uid, name, url, ...} — the
agent has no way to dereference uid → actual bytes.

Lookup: dynamic_data[id=record_id][field] → list of {uid: data_files.id} →
data_files row → storage_path on disk → file contents.

Auth: same MCP token-validated identity as the other tools. Any non-guest user
who reached this MCP token already has data-page read access, so we don't
re-check ACL per row.
"""

import os
import base64

import mcp.types as types

from db import get_db
from context import ToolContext


NAME = "read_data_file"
_MAX_BYTES = 200_000  # 200 KB inline cap, same as read_upload

TOOL = types.Tool(
    name=NAME,
    description=(
        "读取数据页记录上文件字段所附的文件内容。\n"
        "参数:collection=集合名(如 inspection-case),record_id=记录ID(如 IC-001),"
        "field=字段名(如 attachment),index=该字段下第几个文件(从 0 开始,默认 0)。\n"
        "返回:found=true 时给 name/mime/size/content;二进制文件 content 是 base64,"
        "并带 encoding='base64' 标记;文本则直接是 UTF-8 字符串。"
        "若文件大于 200KB 只回 truncated=true + 元数据。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "record_id": {"type": "string"},
            "field": {"type": "string"},
            "index": {"type": "integer", "default": 0, "minimum": 0},
        },
        "required": ["collection", "record_id", "field"],
        "additionalProperties": False,
    },
)


class ReadDataFileError(Exception):
    pass


def handle(input: dict, ctx: ToolContext) -> dict:
    collection = (input or {}).get("collection", "").strip()
    record_id = (input or {}).get("record_id", "").strip()
    field = (input or {}).get("field", "").strip()
    index = int((input or {}).get("index", 0) or 0)

    if not collection or not record_id or not field:
        raise ReadDataFileError("collection, record_id, field required")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM dynamic_data WHERE collection = %s AND id = %s",
            (collection, record_id),
        )
        row = cur.fetchone()
        if not row:
            return {"found": False, "error": f"记录不存在: {collection}/{record_id}"}
        data = row[0] or {}
        field_value = data.get(field)
        if not isinstance(field_value, list) or not field_value:
            return {"found": False,
                    "error": f"字段 {field} 不是文件列表或为空"}
        if index >= len(field_value):
            return {"found": False,
                    "error": f"index={index} 超出范围 (共 {len(field_value)} 个文件)"}

        file_info = field_value[index] or {}
        file_id = file_info.get("uid") or file_info.get("id")
        if not file_id:
            return {"found": False,
                    "error": "文件元数据缺少 uid/id 字段(可能是早期 mock 数据,需重新上传)"}

        cur.execute(
            "SELECT original_name, mime_type, size_bytes, storage_path "
            "FROM data_files WHERE id = %s",
            (file_id,),
        )
        f_row = cur.fetchone()

    if not f_row:
        return {"found": False,
                "error": f"data_files 表无此文件: {file_id}(可能已被删除)"}
    name, mime, size, path = f_row

    if not os.path.isfile(path):
        return {"found": False,
                "error": f"磁盘上文件已不存在: {path}"}

    if size > _MAX_BYTES:
        return {
            "found": True, "truncated": True,
            "name": name, "mime": mime, "size": size,
            "error": f"文件过大 ({size} 字节),最多读取 {_MAX_BYTES} 字节",
        }

    with open(path, "rb") as f:
        raw = f.read()

    try:
        content = raw.decode("utf-8")
        return {"found": True, "name": name, "mime": mime, "size": size,
                "encoding": "utf-8", "content": content}
    except UnicodeDecodeError:
        return {"found": True, "name": name, "mime": mime, "size": size,
                "encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii")}
