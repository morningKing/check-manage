"""Tool: data_create_records —— 向业务数据集合写入记录（应用层同路）。

此前 AI 在会话里"构造数据写表"只能裸 SQL INSERT dynamic_data：绕过校验脚本、
主键查重、autoSequence 原子分配、触发器/Webhook、操作日志。本工具把记录交给
真实创建路由（POST /<collection>，以会话用户身份），与页面 UI 完全同语义：

- autoSequence 字段**不需要传**，服务端原子分配（传了也会被覆盖）；
- 主键冲突返回 409（不会静默覆盖已有记录）；
- 配置了校验脚本的集合逐条跑校验，失败给 validationErrors；
- statusBadge 状态字段的初始值会盖超时基准时间戳；触发器/Webhook 照常触发。

逐条调用创建路由（每条独立事务+审计），默认 fail-fast；continueOnError=true
时收集每条错误继续。单次上限 50 条——批量导入场景请用页面的批量导入/ETL。
"""

import uuid

import mcp.types as types

from context import ToolContext
from tools._data_api import resolve_collection
from db import get_db
from tools._data_api import DataApiError, ensure_writable, execute

NAME = "data_create_records"

MAX_RECORDS = 50

TOOL = types.Tool(
    name=NAME,
    description=(
        "向平台业务数据集合写入一条或多条记录（真实链路，与页面新增完全同语义："
        "自动编号、主键查重、校验脚本、操作日志、触发器全部生效）。"
        "**不要再用 SQL 直写 dynamic_data**——那会绕过以上全部。"
        "参数：collection=集合标识或数据页显示名称（必填，如 inspection-case 或"
        "「巡检记录」）；records=记录数组（必填，每条是 {fieldName: 值}，字段名与"
        "选项值用 list_collections 查询）；continueOnError=某条失败时是否继续"
        "（默认 false）。自动编号(autoSequence)字段不要传值，服务端分配。"
        "单次最多 50 条。返回创建的记录 id 列表。仅非只读身份可用。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string",
                           "description": "集合标识或数据页显示名称"},
            "records": {
                "type": "array", "items": {"type": "object"},
                "description": "记录数组，每条 {fieldName: 值}；id 可省略（自动生成）",
            },
            "continue_on_error": {"type": "boolean",
                                  "description": "默认 false：首条失败即停"},
        },
        "required": ["collection", "records"],
        "additionalProperties": False,
    },
)


class DataCreateError(Exception):
    pass


def handle(input: dict, ctx: ToolContext) -> dict:
    ensure_writable(ctx, NAME)
    inp = input or {}
    identifier = (inp.get("collection") or "").strip()
    records = inp.get("records")
    if not identifier:
        raise DataCreateError("collection 必填：集合标识或数据页显示名称")
    if not isinstance(records, list) or not records:
        raise DataCreateError("records 必填：至少一条记录（数组）")
    if len(records) > MAX_RECORDS:
        raise DataCreateError(f"单次最多 {MAX_RECORDS} 条（收到 {len(records)}）；"
                              "批量导入请用页面批量导入/ETL 能力")
    continue_on_error = bool(inp.get("continue_on_error"))

    with get_db() as conn:
        with conn.cursor() as cur:
            collection = resolve_collection(cur, identifier)
            if not collection:
                raise DataCreateError(
                    f"集合「{identifier}」不存在：用 list_collections 查看可用集合")

    created, results, errors = 0, [], []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            if continue_on_error:
                errors.append({"index": i, "error": "记录必须是对象"})
                continue
            raise DataCreateError(f"第 {i + 1} 条记录不是对象")
        data = {k: v for k, v in rec.items()}
        rid = str(data.pop('id', '') or '').strip() or str(uuid.uuid4())
        try:
            body = execute(ctx, 'POST', f'/{collection}', {**data, 'id': rid})
        except DataApiError as e:
            errors.append({"index": i, "error": e.message, "recordId": rid})
            if not continue_on_error:
                raise DataCreateError(
                    f"第 {i + 1} 条创建失败：{e.message}"
                    + ("" if len(records) == 1 else
                       f"（前 {created} 条已创建成功，其余未执行）"))
            continue
        created += 1
        results.append({"index": i, "id": body.get('id') or rid,
                        "data": {k: v for k, v in body.items()
                                 if k not in ('_version',)}})

    return {
        "created": created,
        "failed": len(errors),
        "records": results,
        **({"errors": errors} if errors else {}),
        "note": "已走应用层真实创建：校验/自动编号/操作日志/触发器均已生效",
    }
