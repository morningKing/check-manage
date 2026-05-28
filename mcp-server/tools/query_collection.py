"""Tool: query_collection — read-only query of a business data collection with
optional cross-collection lookups. The agent (using list_collections schema)
translates the user's natural language into a MongoDB-style filter and calls
this to execute it. RBAC by the collection's menu roles."""

import os
import json
from datetime import datetime

import mcp.types as types
from db import get_db
from context import ToolContext
from query_engine import (
    load_page_configs, count_rows, run_query, write_xlsx,
    MAX_TABLE_ROWS, MAX_XLSX_ROWS,
)

NAME = "query_collection"

TOOL = types.Tool(
    name=NAME,
    description=(
        "按条件只读查询某个业务数据集合，返回匹配的数据（结果在前端渲染为表格）。"
        "用法：先用 list_collections 获取集合的字段(fieldName/label)与 select 选项；"
        "filter 用 MongoDB 风格，字段名用 fieldName(英文)，select 类型的值用 option 的 value；"
        "支持操作符：精确匹配、$regex 模糊、$ne、$gt/$gte/$lt/$lte、$in/$nin、$or/$and。"
        "可选 lookup 关联其它集合（[{from, localField, as}]）。"
        "参数：collection(必填)、filter、lookup、select、sort({字段:1|-1})、skip、limit。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "集合标识，如 inspection-case"},
            "filter": {"type": "object", "description": "MongoDB 风格筛选条件，缺省为全部"},
            "lookup": {"type": "array", "items": {"type": "object"}, "description": "跨集合关联"},
            "select": {"type": "array", "items": {"type": "string"}, "description": "投影字段"},
            "sort": {"type": "object", "description": "排序，如 {createdAt: -1}"},
            "skip": {"type": "integer"},
            "limit": {"type": "integer", "description": "取行上限，默认/上限 400"},
        },
        "required": ["collection"],
        "additionalProperties": False,
    },
)


class QueryCollectionError(Exception):
    pass


def _workspace_for_session(cur, session_id):
    cur.execute(
        "SELECT workspace_path FROM ai_chat_sessions WHERE id = %s AND status = 'active'",
        (session_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def handle(input: dict, ctx: ToolContext):
    inp = input or {}
    collection = (inp.get("collection") or "").strip()
    if not collection:
        raise QueryCollectionError("collection is required")
    filt = inp.get("filter") or {}
    lookups = inp.get("lookup") or []
    select = inp.get("select") or []
    sort = inp.get("sort") or {}
    skip = max(int(inp.get("skip", 0)), 0)
    limit = min(max(int(inp.get("limit", MAX_TABLE_ROWS)), 1), MAX_TABLE_ROWS)

    with get_db() as conn:
        cur = conn.cursor()
        configs = load_page_configs(cur)
        if collection not in configs:
            raise QueryCollectionError(f"集合不存在：{collection}")

        cur.execute("SELECT roles FROM menus WHERE page_id = %s", ('page-' + collection,))
        mrow = cur.fetchone()
        roles = mrow[0] if mrow else None
        if ctx.role != "admin" and (roles is None or ctx.role not in roles):
            raise QueryCollectionError(f"无权限查询：{collection}")

        fields = configs[collection]["fields"]
        total = count_rows(cur, collection, filt, fields)

        if total <= MAX_TABLE_ROWS:
            res = run_query(cur, collection, configs, filt, lookups, select, sort, skip, limit)
            return json.dumps({
                "mode": "table",
                "collection": collection,
                "total": res["total"],
                "columns": res["columns"],
                "rows": res["rows"],
            }, ensure_ascii=False, default=str)

        ws = _workspace_for_session(cur, ctx.session_id)
        if not ws:
            raise QueryCollectionError("session workspace not found")
        res = run_query(cur, collection, configs, filt, [], select, sort, 0, MAX_XLSX_ROWS)
        out_dir = os.path.join(ws, "outputs")
        os.makedirs(out_dir, exist_ok=True)
        fname = f"query-{collection}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
        write_xlsx(res["rows"], res["columns"], os.path.join(out_dir, fname),
                   sheet_title=configs[collection]["name"])
        return json.dumps({
            "mode": "file",
            "collection": collection,
            "total": total,
            "file": f"outputs/{fname}",
            "capped": total > MAX_XLSX_ROWS,
        }, ensure_ascii=False)
