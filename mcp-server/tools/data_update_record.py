"""Tool: data_update_record —— 按记录 id 更新一条业务数据（应用层同路）。

走真实更新路由（PUT /<collection>/<id>，以会话用户身份）：部分字段合并、
校验脚本、工作流状态流转校验、乐观锁 _version、操作日志、触发器全部生效。
裸 SQL UPDATE 会绕过以上全部——状态机可能被写穿，本工具是正规入口。
"""

import mcp.types as types

from context import ToolContext
from tools._data_api import resolve_collection
from db import get_db
from tools._data_api import DataApiError, ensure_writable, execute

NAME = "data_update_record"

TOOL = types.Tool(
    name=NAME,
    description=(
        "更新平台业务数据集合中的一条记录（真实链路：校验脚本/工作流状态机/"
        "乐观锁/操作日志全部生效，**不要再用 SQL 直改 dynamic_data**）。"
        "参数：collection=集合标识或数据页显示名称（必填）；id=记录 id（必填）；"
        "data=要更新的字段 {fieldName: 新值}（必填，只传变更字段，服务端合并）；"
        "expected_version=乐观锁版本号（可选；传了则与服务端当前版本不一致时"
        "409，防止覆盖他人修改）。字段名与选项值用 list_collections 查询。"
        "仅非只读身份可用。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "集合标识或数据页显示名称"},
            "id": {"type": "string", "description": "记录 id"},
            "data": {"type": "object", "description": "要更新的字段（部分更新，服务端合并）"},
            "expected_version": {"type": "integer",
                                 "description": "可选乐观锁：当前 _version 不一致时报 409"},
        },
        "required": ["collection", "id", "data"],
        "additionalProperties": False,
    },
)


class DataUpdateError(Exception):
    pass


def handle(input: dict, ctx: ToolContext) -> dict:
    ensure_writable(ctx, NAME)
    inp = input or {}
    identifier = (inp.get("collection") or "").strip()
    item_id = (inp.get("id") or "").strip()
    data = inp.get("data")
    if not identifier:
        raise DataUpdateError("collection 必填：集合标识或数据页显示名称")
    if not item_id:
        raise DataUpdateError("id 必填：要更新的记录 id")
    if not isinstance(data, dict) or not data:
        raise DataUpdateError("data 必填：至少一个要更新的字段")

    with get_db() as conn:
        with conn.cursor() as cur:
            collection = resolve_collection(cur, identifier)
            if not collection:
                raise DataUpdateError(
                    f"集合「{identifier}」不存在：用 list_collections 查看可用集合")

    body = {k: v for k, v in data.items()}
    body['id'] = item_id
    if inp.get("expected_version") is not None:
        try:
            body['_version'] = int(inp["expected_version"])
        except (TypeError, ValueError):
            raise DataUpdateError("expected_version 必须是整数")

    try:
        out = execute(ctx, 'PUT', f'/{collection}/{item_id}', body)
    except DataApiError as e:
        raise DataUpdateError(f"更新失败：{e.message}")

    return {
        "updated": True,
        "id": out.get('id') or item_id,
        "version": out.get('_version'),
        "data": {k: v for k, v in out.items() if k not in ('_version',)},
        "note": "已走应用层真实更新：校验/状态机/操作日志均已生效",
    }
