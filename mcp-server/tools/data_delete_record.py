"""Tool: data_delete_record —— 按记录 id 删除一条业务数据（应用层同路）。

走真实删除路由（DELETE /<collection>/<id>，以会话用户身份）：跨集合引用
保护（被其它集合 reference 字段引用的记录 409 拒删）、关联关系清理、
Webhook、操作日志全部生效。裸 SQL DELETE 会把引用它的关联数据悬空。
"""

import mcp.types as types

from context import ToolContext
from tools._data_api import resolve_collection
from db import get_db
from tools._data_api import DataApiError, ensure_writable, execute

NAME = "data_delete_record"

TOOL = types.Tool(
    name=NAME,
    description=(
        "删除平台业务数据集合中的一条记录（真实链路：跨集合引用保护——被"
        "其它集合引用的记录会拒绝删除并说明引用方；操作日志留痕；**不要再用 "
        "SQL 直删 dynamic_data**，会留下悬空引用）。"
        "参数：collection=集合标识或数据页显示名称（必填）；id=记录 id（必填）。"
        "仅非只读身份可用。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "集合标识或数据页显示名称"},
            "id": {"type": "string", "description": "记录 id"},
        },
        "required": ["collection", "id"],
        "additionalProperties": False,
    },
)


class DataDeleteError(Exception):
    pass


def handle(input: dict, ctx: ToolContext) -> dict:
    ensure_writable(ctx, NAME)
    inp = input or {}
    identifier = (inp.get("collection") or "").strip()
    item_id = (inp.get("id") or "").strip()
    if not identifier:
        raise DataDeleteError("collection 必填：集合标识或数据页显示名称")
    if not item_id:
        raise DataDeleteError("id 必填：要删除的记录 id")

    with get_db() as conn:
        with conn.cursor() as cur:
            collection = resolve_collection(cur, identifier)
            if not collection:
                raise DataDeleteError(
                    f"集合「{identifier}」不存在：用 list_collections 查看可用集合")

    try:
        execute(ctx, 'DELETE', f'/{collection}/{item_id}')
    except DataApiError as e:
        raise DataDeleteError(f"删除失败：{e.message}")

    return {"deleted": True, "id": item_id, "collection": collection,
            "note": "已走应用层真实删除：引用保护/关联清理/操作日志均已生效"}
