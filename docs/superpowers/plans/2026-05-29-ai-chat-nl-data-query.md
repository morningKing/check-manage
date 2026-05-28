# AI 助手自然语言查询数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 助手通过新的 MCP 工具 `query_collection` 用自然语言查询平台数据（agent 自己把 NL 译成 MongoDB 风格 filter），结果默认渲染成表格（≤400 行预览+下载，>400 行仅 xlsx）。

**Architecture:** 把查询逻辑（`mongo_query` 翻译器 + filter→SQL 执行 + 跨集合 lookup + xlsx 导出）移植进独立的 MCP 服务，新增只读、RBAC 受控的 `query_collection` 工具；前端新增 `QueryResultBlock` 渲染工具结果（表格/文件）；后端 `_AGENT_DIRECTIVE` 引导 agent 使用该工具。

**Tech Stack:** Python（mcp-server，FastAPI/MCP，自带 venv，含 openpyxl）、psycopg2、pytest；Vue 3 + TS + Element Plus + SheetJS(`xlsx`)、Vitest；Flask（仅改提示）。

设计依据：`docs/superpowers/specs/2026-05-29-ai-chat-nl-data-query-design.md`

> 运行 mcp 测试：在 `mcp-server/` 下 `.venv/Scripts/python.exe -m pytest tests/<file> -v`（Windows）。

---

### Task 1: 移植 mongo_query 翻译器到 MCP

**Files:**
- Create: `mcp-server/mongo_query.py`
- Test: `mcp-server/tests/test_mongo_query.py`

- [ ] **Step 1: 写失败测试** `mcp-server/tests/test_mongo_query.py`

```python
"""Unit tests for the ported MongoDB-style -> SQL translator."""
from mongo_query import translate, remap_labels, MongoQueryError
import pytest


def test_equality():
    where, params = translate({"status": "open"})
    assert where == "data->>'status' = %s"
    assert params == ["open"]


def test_regex():
    where, params = translate({"name": {"$regex": "abc"}})
    assert "~*" in where and params == ["abc"]


def test_numeric_gte():
    where, params = translate({"age": {"$gte": 18}})
    assert "::numeric >= %s" in where and params == [18]


def test_in():
    where, params = translate({"s": {"$in": ["a", "b"]}})
    assert "IN (%s, %s)" in where and params == ["a", "b"]


def test_or():
    where, params = translate({"$or": [{"a": "1"}, {"b": "2"}]})
    assert " OR " in where and params == ["1", "2"]


def test_empty_is_true():
    assert translate({}) == ("TRUE", [])


def test_remap_labels():
    fields = [{"fieldName": "caseid", "label": "用例ID"}]
    assert remap_labels({"用例ID": "x"}, fields) == {"caseid": "x"}


def test_invalid_field_raises():
    with pytest.raises(MongoQueryError):
        translate({"bad field!": "x"})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_mongo_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mongo_query'`.

- [ ] **Step 3: 实现** — 复制翻译器（逐字）

Run: `cp server/utils/mongo_query.py mcp-server/mongo_query.py`
然后在 `mcp-server/mongo_query.py` 文件**最顶部**加一行注释（在现有 docstring 之上）：

```python
# NOTE: copied from server/utils/mongo_query.py — keep the two in sync.
```

（不修改任何逻辑；该模块仅依赖标准库 `re`。）

- [ ] **Step 4: 运行确认通过**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_mongo_query.py -v`
Expected: PASS（8 项）。

- [ ] **Step 5: 提交**

```bash
git add mcp-server/mongo_query.py mcp-server/tests/test_mongo_query.py
git commit -m "feat(mcp): port mongo_query translator into mcp-server"
```

---

### Task 2: 查询引擎（filter→SQL + lookup + xlsx）

**Files:**
- Create: `mcp-server/query_engine.py`
- Test: `mcp-server/tests/test_query_engine.py`

- [ ] **Step 1: 写失败测试** `mcp-server/tests/test_query_engine.py`

```python
"""Tests for query_engine: count/run via mocked cursor, and xlsx writer."""
import os
from unittest.mock import MagicMock
from openpyxl import load_workbook


def test_count_rows_uses_filter():
    from query_engine import count_rows
    cur = MagicMock()
    cur.fetchone.return_value = (7,)
    n = count_rows(cur, "orders", {"status": "open"}, [{"fieldName": "status", "label": "状态"}])
    assert n == 7
    sql, params = cur.execute.call_args[0]
    assert "COUNT(*)" in sql and "orders" in params and "open" in params


def test_run_query_no_lookup_builds_rows_and_columns():
    from query_engine import run_query
    cur = MagicMock()
    # 1st execute = COUNT -> fetchone; 2nd execute = rows -> fetchall
    cur.fetchone.side_effect = [(2,)]
    cur.fetchall.side_effect = [[
        ("id1", {"no": "A1", "status": "open"}, None, None),
        ("id2", {"no": "A2", "status": "done"}, None, None),
    ]]
    configs = {"orders": {"name": "订单", "fields": [
        {"fieldName": "no", "label": "单号", "controlType": "text"},
        {"fieldName": "status", "label": "状态", "controlType": "text"},
    ]}}
    res = run_query(cur, "orders", configs, {"no": {"$regex": "A"}}, [], [], {}, 0, 400)
    assert res["total"] == 2
    assert len(res["rows"]) == 2
    assert res["rows"][0]["no"] == "A1"
    keys = {c["key"] for c in res["columns"]}
    assert "no" in keys and "status" in keys


def test_write_xlsx_roundtrip(tmp_path):
    from query_engine import write_xlsx
    out = os.path.join(tmp_path, "o.xlsx")
    cols = [{"key": "no", "label": "单号"}, {"key": "tags", "label": "标签"}]
    rows = [{"no": "A1", "tags": ["x", "y"]}, {"no": "A2", "tags": None}]
    write_xlsx(rows, cols, out, sheet_title="订单")
    wb = load_workbook(out)
    sh = wb.active
    assert [c.value for c in sh[1]] == ["单号", "标签"]
    assert sh[2][0].value == "A1"
    assert sh[2][1].value == '["x", "y"]'  # nested serialized as JSON
```

- [ ] **Step 2: 运行确认失败**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_query_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'query_engine'`.

- [ ] **Step 3: 实现** `mcp-server/query_engine.py`（移植自 `server/routes/query.py`）

```python
"""Read-only query engine for the MCP server: MongoDB-style filter -> rows,
with cross-collection lookups, plus xlsx export.

Ported from server/routes/query.py (execute_query + helpers). Keep the lookup
logic in sync with that file.
"""
import json
from openpyxl import Workbook
from mongo_query import translate as mongo_translate, remap_labels

MAX_TABLE_ROWS = 400
MAX_XLSX_ROWS = 50000


def load_page_configs(cur):
    cur.execute('SELECT id, name, fields FROM page_configs')
    result = {}
    for pid, pname, pfields in cur.fetchall():
        col = pid.replace('page-', '', 1)
        result[col] = {'name': pname, 'fields': pfields or []}
    return result


def _build_label_map(fields):
    m = {}
    for f in fields:
        fn = f.get('fieldName', '')
        lb = f.get('label', '')
        if fn and lb:
            m[lb] = fn
    return m


def _detect_relation_type(fields, field_name):
    for f in fields:
        if f.get('fieldName') != field_name:
            continue
        ct = f.get('controlType', '')
        if ct == 'relation':
            return 'relation', (f.get('relationConfig') or {}).get('targetCollection', '')
        if ct == 'reference':
            return 'reference', (f.get('referenceConfig') or {}).get('targetCollection', '')
        if ct == 'quoteSelect':
            return 'quoteSelect', (f.get('quoteConfig') or {}).get('targetCollection', '')
    return None, None


def _order_clause(sort_spec, label_map):
    parts = []
    for k, direction in (sort_spec or {}).items():
        fname = label_map.get(k, k)
        if fname == 'createdAt':
            col = 'created_at'
        elif fname == 'updatedAt':
            col = 'updated_at'
        else:
            col = f"data->>'{fname.replace(chr(39), chr(39) + chr(39))}'"
        parts.append(f"{col} {'ASC' if direction >= 0 else 'DESC'}")
    return ', '.join(parts) if parts else 'created_at'


def count_rows(cur, collection, query, fields):
    q = remap_labels(query, fields) if query else {}
    where, params = mongo_translate(q)
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
        [collection] + params,
    )
    return cur.fetchone()[0]


def _apply_lookups(cur, records, collection, col_fields, configs, lookups, label_map):
    for lk in lookups:
        from_col = lk.get('from', '')
        local_field = label_map.get(lk.get('localField', ''), lk.get('localField', ''))
        as_name = lk.get('as', from_col)
        if not from_col or not local_field:
            continue
        rel_type, target_col = _detect_relation_type(col_fields, local_field)
        if not target_col:
            target_col = from_col

        if rel_type == 'relation':
            record_ids = [r['_id'] for r in records]
            rel_map, all_related = {}, set()
            if record_ids:
                cur.execute(
                    'SELECT record_id, related_id FROM data_relations '
                    'WHERE collection = %s AND field_name = %s AND record_id = ANY(%s)',
                    (collection, local_field, record_ids),
                )
                for src_id, rel_id in cur.fetchall():
                    rel_map.setdefault(src_id, []).append(rel_id)
                    all_related.add(rel_id)
            related_data = {}
            if all_related:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(all_related),))
                for fid, fdata in cur.fetchall():
                    related_data[fid] = fdata or {}
            for rec in records:
                rec[as_name] = [{'_id': rid, **related_data.get(rid, {})} for rid in rel_map.get(rec['_id'], [])]

        elif rel_type == 'reference':
            parent_ids = {rec.get(local_field) for rec in records if isinstance(rec.get(local_field), str)}
            parent_data = {}
            if parent_ids:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(parent_ids),))
                for fid, fdata in cur.fetchall():
                    parent_data[fid] = fdata or {}
            for rec in records:
                pid = rec.get(local_field)
                rec[as_name] = {'_id': pid, **parent_data[pid]} if pid in parent_data else None

        elif rel_type == 'quoteSelect':
            all_qids = set()
            for rec in records:
                if isinstance(rec.get(local_field), list):
                    all_qids.update(rec[local_field])
            quoted = {}
            if all_qids:
                cur.execute('SELECT id, data FROM dynamic_data WHERE id = ANY(%s)', (list(all_qids),))
                for fid, fdata in cur.fetchall():
                    quoted[fid] = fdata or {}
            for rec in records:
                qids = rec.get(local_field, [])
                rec[as_name] = [{'_id': q, **quoted.get(q, {})} for q in qids] if isinstance(qids, list) else []

        else:
            ids = set()
            for rec in records:
                v = rec.get(local_field)
                if isinstance(v, str):
                    ids.add(v)
                elif isinstance(v, list):
                    ids.update(x for x in v if isinstance(x, str))
            fetched = {}
            if ids:
                cur.execute(
                    'SELECT id, data FROM dynamic_data WHERE collection = %s AND id = ANY(%s)',
                    (target_col, list(ids)),
                )
                for fid, fdata in cur.fetchall():
                    fetched[fid] = fdata or {}
            for rec in records:
                v = rec.get(local_field)
                if isinstance(v, str):
                    rec[as_name] = {'_id': v, **fetched.get(v, {})} if v in fetched else None
                elif isinstance(v, list):
                    rec[as_name] = [{'_id': x, **fetched.get(x, {})} for x in v if x in fetched]
                else:
                    rec[as_name] = None


def _build_columns(records, col_fields, lookups, configs):
    label_map = {f.get('fieldName'): f.get('label', f.get('fieldName')) for f in col_fields}
    lookup_as = {lk.get('as', lk.get('from', '')): lk.get('from', '') for lk in lookups}
    columns, seen = [], set()
    if not records:
        return columns
    for key in records[0]:
        if key in seen:
            continue
        seen.add(key)
        col = {'key': key, 'label': label_map.get(key, key)}
        if key in lookup_as:
            tgt = lookup_as[key]
            col['label'] = f"{configs.get(tgt, {}).get('name', tgt)} ({key})"
            col['isLookup'] = True
        columns.append(col)
    return columns


def run_query(cur, collection, configs, query, lookups, select, sort, skip, limit):
    """Return {total, rows, columns} for `collection` matching `query`."""
    col_fields = configs[collection]['fields']
    label_map = _build_label_map(col_fields)
    q = remap_labels(query, col_fields) if query else {}
    where, params = mongo_translate(q)
    order = _order_clause(sort, label_map)

    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
        [collection] + params,
    )
    total = cur.fetchone()[0]

    cur.execute(
        'SELECT id, data, created_at, updated_at FROM dynamic_data '
        'WHERE collection = %s AND (' + where + ') ORDER BY ' + order + ' LIMIT %s OFFSET %s',
        [collection] + params + [limit, skip],
    )
    records = []
    for rid, data, c_at, u_at in cur.fetchall():
        rec = {'_id': rid}
        if data:
            rec.update(data)
        if c_at:
            rec['createdAt'] = c_at.isoformat() if hasattr(c_at, 'isoformat') else str(c_at)
        if u_at:
            rec['updatedAt'] = u_at.isoformat() if hasattr(u_at, 'isoformat') else str(u_at)
        records.append(rec)

    if lookups:
        _apply_lookups(cur, records, collection, col_fields, configs, lookups, label_map)

    select_fields = [label_map.get(s, s) for s in (select or [])]
    if select_fields:
        keep = set(select_fields) | {'_id', 'createdAt', 'updatedAt'} | \
            {lk.get('as', lk.get('from', '')) for lk in lookups}
        records = [{k: v for k, v in r.items() if k in keep} for r in records]

    return {'total': total, 'rows': records, 'columns': _build_columns(records, col_fields, lookups, configs)}


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return '是' if v else '否'
    return v


def write_xlsx(rows, columns, out_path, sheet_title='data'):
    wb = Workbook()
    sh = wb.active
    sh.title = (sheet_title or 'data')[:31]
    headers = [c['label'] for c in columns] or ['(空)']
    keys = [c['key'] for c in columns]
    sh.append(headers)
    for r in rows:
        sh.append([_cell(r.get(k)) for k in keys])
    wb.save(out_path)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_query_engine.py -v`
Expected: PASS（3 项）。

- [ ] **Step 5: 提交**

```bash
git add mcp-server/query_engine.py mcp-server/tests/test_query_engine.py
git commit -m "feat(mcp): query engine with lookups and xlsx export"
```

---

### Task 3: `query_collection` MCP 工具 + 注册

**Files:**
- Create: `mcp-server/tools/query_collection.py`
- Modify: `mcp-server/tools/__init__.py`
- Test: `mcp-server/tests/test_query_collection.py`

- [ ] **Step 1: 写失败测试** `mcp-server/tests/test_query_collection.py`

```python
"""Unit tests for tools.query_collection (mocked DB)."""
from unittest.mock import patch, MagicMock
import json
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _configs():
    return {"orders": {"name": "订单", "fields": [
        {"fieldName": "no", "label": "单号", "controlType": "text"},
        {"fieldName": "status", "label": "状态", "controlType": "text"},
    ]}}


def test_denies_when_collection_not_visible(fake_db, mock_cursor):
    # load_page_configs -> orders exists; menu roles = ['admin'] only
    mock_cursor.fetchall.side_effect = [[("page-orders", "订单", _configs()["orders"]["fields"])]]
    mock_cursor.fetchone.side_effect = [(["admin"],)]
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle, QueryCollectionError
        with pytest.raises(QueryCollectionError):
            handle({"collection": "orders"}, _ctx("developer"))


def test_table_mode_returns_rows_json(fake_db, mock_cursor):
    fields = _configs()["orders"]["fields"]
    # 1) load_page_configs fetchall  2) run_query rows fetchall
    mock_cursor.fetchall.side_effect = [
        [("page-orders", "订单", fields)],
        [("id1", {"no": "A1", "status": "open"}, None, None)],
    ]
    # 1) menu roles fetchone  2) count_rows fetchone  3) run_query COUNT fetchone
    mock_cursor.fetchone.side_effect = [(["developer"],), (1,), (1,)]
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle
        out = json.loads(handle({"collection": "orders", "filter": {"status": "open"}}, _ctx("developer")))
    assert out["mode"] == "table"
    assert out["total"] == 1
    assert out["rows"][0]["no"] == "A1"


def test_file_mode_when_over_threshold(fake_db, mock_cursor, tmp_path, monkeypatch):
    fields = _configs()["orders"]["fields"]
    mock_cursor.fetchall.side_effect = [
        [("page-orders", "订单", fields)],
        [("id%d" % i, {"no": "A%d" % i}, None, None) for i in range(3)],  # export rows
    ]
    # roles; count_rows() count (decides mode); run_query() internal COUNT
    mock_cursor.fetchone.side_effect = [(["developer"],), (401,), (3,)]
    import tools.query_collection as qc
    monkeypatch.setattr(qc, "_workspace_for_session", lambda cur, sid: str(tmp_path))
    (tmp_path / "outputs").mkdir()
    with patch("tools.query_collection.get_db", fake_db):
        out = json.loads(qc.handle({"collection": "orders"}, _ctx("developer")))
    assert out["mode"] == "file"
    assert out["total"] == 401
    assert out["file"].startswith("outputs/")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_query_collection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.query_collection'`.

- [ ] **Step 3: 实现** `mcp-server/tools/query_collection.py`

```python
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
        "filter 用 MongoDB 风格，字段名用 fieldName(英文)，select 类型的值用 option 的 value。"
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

        # RBAC: collection must be visible to this role (same rule as list_collections)
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

        # Large result: export base rows (no lookups) to xlsx, return a file ref.
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
```

- [ ] **Step 4: 注册工具** — 编辑 `mcp-server/tools/__init__.py`

把 import 行改为（追加 `query_collection`）：
```python
from tools import (
    list_collections, save_artifact, read_upload, export_collection_excel, run_python,
    query_collection,
)
```
在 `_TOOLS` 字典里追加一行：
```python
    query_collection.NAME: (query_collection.TOOL, query_collection.handle),
```

- [ ] **Step 5: 运行确认通过**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_query_collection.py -v`
Expected: PASS（3 项）。

- [ ] **Step 6: 提交**

```bash
git add mcp-server/tools/query_collection.py mcp-server/tools/__init__.py mcp-server/tests/test_query_collection.py
git commit -m "feat(mcp): query_collection tool (read-only, RBAC, table/file modes)"
```

---

### Task 4: 前端结果渲染组件 `QueryResultBlock`

**Files:**
- Create: `src/components/ai-chat/QueryResultBlock.vue`
- Test: `src/components/ai-chat/__tests__/QueryResultBlock.test.ts`

- [ ] **Step 1: 写失败测试** `src/components/ai-chat/__tests__/QueryResultBlock.test.ts`

```typescript
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import QueryResultBlock from '../QueryResultBlock.vue'

const writeFile = vi.fn()
vi.mock('xlsx', () => ({
  utils: { json_to_sheet: vi.fn(() => ({})), book_new: vi.fn(() => ({})), book_append_sheet: vi.fn() },
  writeFile: (...a: any[]) => writeFile(...a),
}))

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

const stubs = {
  'el-table': { template: '<table><slot /></table>' },
  'el-table-column': { template: '<col-stub />' },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
  'el-icon': { template: '<i><slot /></i>' },
}

describe('QueryResultBlock', () => {
  it('table mode renders a table and downloads via SheetJS', async () => {
    const result = { mode: 'table', collection: 'orders', total: 1,
      columns: [{ key: 'no', label: '单号' }], rows: [{ no: 'A1' }] }
    const w = mount(QueryResultBlock, { props: { result, downloadUrl: (p: string) => '/dl/' + p }, global: { stubs } })
    expect(w.text()).toContain('共 1 条')
    expect(w.find('table').exists()).toBe(true)
    await w.find('button').trigger('click')
    expect(writeFile).toHaveBeenCalled()
  })

  it('file mode renders a download link and no table', () => {
    const result = { mode: 'file', collection: 'orders', total: 999, file: 'outputs/x.xlsx' }
    const w = mount(QueryResultBlock, { props: { result, downloadUrl: (p: string) => '/dl/' + p }, global: { stubs } })
    expect(w.find('table').exists()).toBe(false)
    expect(w.find('a').attributes('href')).toBe('/dl/outputs/x.xlsx')
    expect(w.text()).toContain('999')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/QueryResultBlock.test.ts`
Expected: FAIL — 无法解析 `../QueryResultBlock.vue`。

- [ ] **Step 3: 实现** `src/components/ai-chat/QueryResultBlock.vue`

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { ElTable, ElTableColumn, ElButton, ElIcon } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'

interface Col { key: string; label: string; isLookup?: boolean }
interface QueryResult {
  mode: string
  collection: string
  total: number
  columns?: Col[]
  rows?: Record<string, any>[]
  file?: string
  capped?: boolean
}
const props = defineProps<{ result: QueryResult; downloadUrl: (path: string) => string }>()

const isTable = computed(() => props.result?.mode === 'table')
const columns = computed<Col[]>(() => props.result?.columns ?? [])
const rows = computed<Record<string, any>[]>(() => props.result?.rows ?? [])

function cell(row: Record<string, any>, col: Col): string {
  const v = row[col.key]
  if (v == null) return ''
  if (Array.isArray(v)) return v.length ? `${v.length} 项` : ''
  if (typeof v === 'object') return v.name ?? v.title ?? JSON.stringify(v)
  if (typeof v === 'boolean') return v ? '是' : '否'
  return String(v)
}

function downloadXlsx() {
  const data = rows.value.map((r) => {
    const o: Record<string, any> = {}
    for (const c of columns.value) o[c.label] = cell(r, c)
    return o
  })
  const ws = XLSX.utils.json_to_sheet(data)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'data')
  XLSX.writeFile(wb, `${props.result.collection || 'query'}.xlsx`)
}
</script>

<template>
  <div class="query-result">
    <template v-if="isTable">
      <div class="query-result__bar">
        <span class="query-result__count">共 {{ result.total }} 条</span>
        <ElButton size="small" :icon="Download" @click="downloadXlsx">下载 Excel</ElButton>
      </div>
      <ElTable :data="rows" size="small" border max-height="360" class="query-result__table">
        <ElTableColumn
          v-for="c in columns" :key="c.key" :prop="c.key" :label="c.label"
          show-overflow-tooltip min-width="120"
        >
          <template #default="{ row }">{{ cell(row, c) }}</template>
        </ElTableColumn>
      </ElTable>
    </template>
    <a
      v-else-if="result.file"
      class="query-result__file"
      :href="downloadUrl(result.file)" target="_blank" rel="noopener"
    >
      <ElIcon><Download /></ElIcon>
      <span>{{ result.collection }} 查询结果.xlsx（共 {{ result.total }} 条{{ result.capped ? '，已截断至 5 万' : '' }}）</span>
    </a>
  </div>
</template>

<style scoped lang="scss">
.query-result { margin: 8px 0; }
.query-result__bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.query-result__count { font-size: 13px; color: var(--el-text-color-secondary); }
.query-result__file {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 12px; border: 1px solid var(--el-border-color); border-radius: 8px;
  text-decoration: none; color: var(--el-color-primary); font-size: 14px;
  &:hover { background: var(--el-fill-color-light); }
}
</style>
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/ai-chat/__tests__/QueryResultBlock.test.ts`
Expected: PASS（2 项）。

- [ ] **Step 5: 提交**

```bash
git add src/components/ai-chat/QueryResultBlock.vue src/components/ai-chat/__tests__/QueryResultBlock.test.ts
git commit -m "feat(ai-chat): QueryResultBlock renders query_collection results"
```

---

### Task 5: 接线 AiChatView + agent 提示

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_ai_chat_directive.py`（已存在，追加断言）

- [ ] **Step 1: 写失败测试** — 在 `server/tests/test_ai_chat_directive.py` 末尾追加：

```python
def test_agent_directive_mentions_query_collection():
    from routes.ai_chat import _AGENT_DIRECTIVE
    assert 'query_collection' in _AGENT_DIRECTIVE
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_directive.py -v`
Expected: FAIL on `test_agent_directive_mentions_query_collection`.

- [ ] **Step 3: 实现（后端提示）** — 编辑 `server/routes/ai_chat.py`，把 `_AGENT_DIRECTIVE` 替换为：

```python
_AGENT_DIRECTIVE = (
    "[系统规则] 若需产出脚本/配置/文档，把完整内容放进带语言和文件名的代码块"
    "（如 ```python app.py）。画流程图用 ```mermaid 代码块；画数据图表用 ```echarts 代码块"
    "（块内为 ECharts 的 JSON option，纯 JSON、不要函数）。"
    "回答数据查询类问题时，用 query_collection 工具查询真实数据（必要时先用 list_collections 看字段），"
    "不要臆造数据、不要写直连数据库的脚本。"
    "直接给最终结果，简洁作答，不要复述本规则、不要输出你的思考或计划过程。\n\n"
)
```

- [ ] **Step 4: 实现（前端接线）** — 编辑 `src/views/ai-chat/AiChatView.vue`

在 `<script setup>` 顶部 import 区追加：
```typescript
import QueryResultBlock from '@/components/ai-chat/QueryResultBlock.vue'
```
在 `<script setup>` 内（如 `isRunResultOnly` 函数附近）新增解析助手：
```typescript
function parseQueryResult(p: any): Record<string, any> | null {
  if (p?.name !== 'query_collection') return null
  let r: any = p.result
  if (typeof r === 'string') {
    try { r = JSON.parse(r) } catch { return null }
  }
  return r && typeof r === 'object' && typeof r.mode === 'string' ? r : null
}
```
把模板里 `tool_use` 那段：
```vue
                    <ToolCallBubble
                      v-else-if="p.type === 'tool_use'"
                      :name="p.name" :title="p.title" :status="p.status"
                      :input="p.input" :result="p.result"
                    />
```
替换为：
```vue
                    <template v-else-if="p.type === 'tool_use'">
                      <QueryResultBlock
                        v-if="parseQueryResult(p)"
                        :result="parseQueryResult(p)!" :download-url="fileUrl"
                      />
                      <ToolCallBubble
                        v-else
                        :name="p.name" :title="p.title" :status="p.status"
                        :input="p.input" :result="p.result"
                      />
                    </template>
```

- [ ] **Step 5: 运行确认通过 + 构建**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_directive.py -v`
Expected: PASS（含新断言）。
Run: `npm run build`
Expected: vue-tsc 无类型错误，构建成功。

- [ ] **Step 6: 提交**

```bash
git add server/routes/ai_chat.py server/tests/test_ai_chat_directive.py src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): wire query_collection result rendering + agent directive"
```

---

### Task 6: 真机验证（构建 + Playwright + 可选 DB 集成）

**Files:** 无（验证）。前置：`:8080` 生产栈运行（`cd server && python proxy.py`，含后端+MCP），OpenCode 在 4096。

- [ ] **Step 1: 重启 MCP 以加载新工具**

新工具在 MCP 进程中注册，需重启 MCP（proxy.py 会一并启动）。若 `:8080` 栈在跑：停掉 3001/3003/8080 进程树，`npm run build`，再 `cd server && python proxy.py`。确认 `http://127.0.0.1:3003/health` 返回 200。

- [ ] **Step 2:（可选）MCP 集成测试**（真库）

Run: `cd mcp-server && set RUN_DB_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/test_query_collection.py -v`
（若按 `test_integration_db.py` 模式补了集成用例则运行；否则跳过，靠 Step 3 真机验证。）

- [ ] **Step 3: AI 助手真机验证**

用 Playwright：登录注入 token → `/ai-chat` 新建会话 → 发「查询 XX 集合里 …… 的数据」（用真实存在的集合）。确认：
- agent 调用了 `query_collection`；
- 结果渲染为 `el-table`（`document.querySelectorAll('.query-result__table').length >= 1`）、有「下载 Excel」按钮；
- 点击下载能生成 xlsx。
截图留证。再构造 >400 行场景（或临时把阈值调小验证）确认只出 xlsx 下载卡片、无表格。

---

## 备注 / 风险

- `mongo_query.py` 与 `query_engine.py` 是 server 端逻辑的副本/移植，文件顶部注释互指；Flask 查询逻辑变动时需同步。
- 工具必须返回 **JSON 字符串**（已在 handle 用 `json.dumps`），否则前端 `parseQueryResult` 解析失败会回退到 `ToolCallBubble`（不报错，但不出表格）。
- file 模式导出基础字段（不含 lookup），并有 5 万行硬上限。
- 弱模型 MiMo 的 filter 翻译质量靠工具描述 + list_collections schema 提升；复杂查询可能需用户澄清。
- 安全：只读、参数化 SQL、RBAC 集合可见性、表格 ≤400 行、xlsx ≤5 万行。
