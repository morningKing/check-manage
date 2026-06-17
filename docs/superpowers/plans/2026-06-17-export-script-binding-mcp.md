# 导出脚本绑定 + MCP 导出工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让导出脚本绑定到具体数据页/菜单（专项专用），抽取共享执行器，并在 AI 助手中可调用导出脚本、结果作为可下载文件出现在会话里。

**Architecture:** `export_scripts` 加 `bound_collection`/`bound_menu_id` 两列；新增 Flask-free、游标注入的 `server/utils/export_runner.py`，被 Flask 路由与 MCP 工具共用（单一执行权威，含 references 注入）；MCP 新增 `list_export_scripts` / `run_export_script` 两个工具，结果写会话 `outputs/`。

**Tech Stack:** Python Flask + psycopg2 + PostgreSQL；Vue 3 + Element Plus + TS；MCP（FastAPI + mcp Streamable-HTTP，独立 venv，已含 pandas）。

**Spec:** `docs/superpowers/specs/2026-06-17-export-script-binding-mcp-design.md`

**测试约定（Windows）：** 后端测试统一 `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest <path> -v`；
MCP 测试 `cd mcp-server && python -m pytest tests/<file> -v`。后端引用解析类测试直连真实 DB（库 `casemanage`，与 `test_export_references.py` 同模式）。

---

## File Structure

- `server/init_db.py` — 迁移：`export_scripts` 加 `bound_collection` / `bound_menu_id`。
- `server/utils/export_runner.py`（新增）— 共享执行器：`check_binding` / `check_rbac` / `execute_bound_export`；异常 `ExportBindingError` / `ExportPermissionError`。
- `server/routes/export_scripts.py` — CRUD 读写绑定 + 新建必绑校验；`execute` 调执行器 + 绑定校验；新增 `GET /exportScripts/for-collection/<collection>`。
- `server/utils/menu_export.py` — 菜单导出沿用现状（已注入 references），仅补菜单级绑定校验（复用 `check_binding`）。
- `mcp-server/tools/_server_imports.py`（新增）— 把 `<repo>/server` 加进 `sys.path`，集中复用 server/utils。
- `mcp-server/tools/list_export_scripts.py`（新增）、`mcp-server/tools/run_export_script.py`（新增）、`mcp-server/tools/__init__.py`（注册）。
- 前端：`src/types/exportScript.ts`、`src/api/exportScript.ts`、`src/views/admin/ExportScriptManager.vue`、`src/views/dynamic/DynamicPage.vue`。
- 测试：`server/tests/test_export_runner.py`（新增）、`server/tests/test_routes_export_scripts.py`（补绑定用例）、`mcp-server/tests/test_list_export_scripts.py`、`mcp-server/tests/test_run_export_script.py`。
- 文档：`docs/user-guide/admin/scripts.md`、`docs/user-guide/ai/export-via-chat.md`（新增）、`docs/user-guide/README.md`。

---

## Task 1: DB 迁移 — 绑定列

**Files:**
- Modify: `server/init_db.py`（在现有「Migration: add scope column to export_scripts」之后追加）

- [ ] **Step 1: 加迁移代码**

在 `server/init_db.py` 中，紧跟 scope 迁移块（`ALTER TABLE export_scripts ADD COLUMN scope ...` 那段，约 686-694 行）之后插入：

```python
        # Migration: add binding columns to export_scripts (bound_collection / bound_menu_id)
        for col in ('bound_collection', 'bound_menu_id'):
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'export_scripts' AND column_name = %s
            """, (col,))
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE export_scripts ADD COLUMN {col} VARCHAR(100)")
                print(f"Added {col} column to export_scripts table.")
```

- [ ] **Step 2: 跑迁移确认**

Run: `cd server && python init_db.py`
Expected: 输出 `Added bound_collection column to export_scripts table.` 与 `Added bound_menu_id column to export_scripts table.`（重复跑则无输出，幂等）。

- [ ] **Step 3: 校验列存在**

Run: `cd server && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG).cursor(); c.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='export_scripts' AND column_name IN ('bound_collection','bound_menu_id')\"); print(sorted(x[0] for x in c.fetchall()))"`
Expected: `['bound_collection', 'bound_menu_id']`

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(export): add bound_collection/bound_menu_id migration to export_scripts"
```

---

## Task 2: 共享执行器 `export_runner.py`

**Files:**
- Create: `server/utils/export_runner.py`
- Create (test): `server/tests/test_export_runner.py`

**接口契约（后续任务都按此签名调用）：**
- `SCRIPT_SELECT = 'id, name, script, output_format, scope, bound_collection, bound_menu_id'`（统一 SELECT 列序）。
- `class ExportBindingError(Exception)` / `class ExportPermissionError(Exception)`。
- `check_binding(script_row, *, collection=None, menu_id=None)` → None；不符抛 `ExportBindingError`。
- `check_rbac(cur, *, collection=None, menu_id=None, role=None)` → None；不通过抛 `ExportPermissionError`。
- `execute_bound_export(cur, script_row, *, collection=None, menu_id=None, branch_id='main', role=None, record_id=None)`
  → page 维度返回 `(result_bytes, filename, content_type)`；menu 维度返回 `list[(bytes, filename, content_type)]`。

- [ ] **Step 1: 写失败测试**

写 `server/tests/test_export_runner.py`（直连真实 DB，复用 `test_export_references.py` 的 `_seed_page`/`_ins`/`_cleanup` 思路，自带精简版）：

```python
"""export_runner 共享执行器测试（直连真实 DB casemanage）。"""
import json
import psycopg2.extras
import pytest
from db import get_db
from utils.export_runner import (
    execute_bound_export, check_binding, check_rbac,
    ExportBindingError, ExportPermissionError, SCRIPT_SELECT,
)


def _seed_page(cur, coll, fields, roles=('admin', 'developer', 'guest')):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("DELETE FROM menus WHERE page_id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
    cur.execute("INSERT INTO menus (id,name,page_id,roles,menu_type) VALUES (%s,%s,%s,%s,'data')",
                (f'menu-{coll}', coll, f'page-{coll}', psycopg2.extras.Json(list(roles))))


def _seed_script(cur, sid, scope='page', bound_collection=None, bound_menu_id=None,
                 script="result = json.dumps([r['id'] for r in data])", output_format='json'):
    cur.execute("DELETE FROM export_scripts WHERE id=%s", (sid,))
    cur.execute(
        "INSERT INTO export_scripts (id,name,script,output_format,scope,bound_collection,bound_menu_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (sid, sid, script, output_format, scope, bound_collection, bound_menu_id))


def _fetch_script(cur, sid):
    cur.execute(f"SELECT {SCRIPT_SELECT} FROM export_scripts WHERE id=%s", (sid,))
    return cur.fetchone()


def _cleanup(colls=(), scripts=()):
    with get_db() as conn:
        cur = conn.cursor()
        for c in colls:
            cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (c,))
            cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{c}',))
            cur.execute("DELETE FROM menus WHERE page_id=%s", (f'page-{c}',))
        for s in scripts:
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (s,))
        conn.commit()


def test_bound_export_runs_when_target_matches():
    coll, sid = 'zzer_a', 'zzer_s1'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [{'fieldName': 'name', 'controlType': 'text'}])
            _ = cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                            ('r1', coll, psycopg2.extras.Json({'name': 'A'})))
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            out, fname, ctype = execute_bound_export(cur, row, collection=coll, role='admin')
        assert json.loads(out) == ['r1']
        assert ctype == 'application/json'
    finally:
        _cleanup([coll], [sid])


def test_binding_mismatch_raises():
    coll, other, sid = 'zzer_b', 'zzer_b2', 'zzer_s2'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            with pytest.raises(ExportBindingError):
                execute_bound_export(cur, row, collection=other, role='admin')
    finally:
        _cleanup([coll], [sid])


def test_unbound_script_is_tolerant():
    coll, sid = 'zzer_c', 'zzer_s3'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [{'fieldName': 'name', 'controlType': 'text'}])
            cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                        ('r1', coll, psycopg2.extras.Json({'name': 'A'})))
            _seed_script(cur, sid, scope='page', bound_collection=None)  # 未绑定
            conn.commit()
            row = _fetch_script(cur, sid)
            out, _, _ = execute_bound_export(cur, row, collection=coll, role='admin')
        assert json.loads(out) == ['r1']
    finally:
        _cleanup([coll], [sid])


def test_rbac_denies_role_not_in_menu_roles():
    coll, sid = 'zzer_d', 'zzer_s4'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [], roles=('admin',))  # 仅 admin
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            with pytest.raises(ExportPermissionError):
                execute_bound_export(cur, row, collection=coll, role='guest')
    finally:
        _cleanup([coll], [sid])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'utils.export_runner'`）。

- [ ] **Step 3: 实现 `export_runner.py`**

写 `server/utils/export_runner.py`：

```python
"""导出脚本共享执行器（Flask-free，游标注入）。

Flask 路由与 MCP 工具都调用本模块，确保「绑定校验 + RBAC + 取数 + 引用解析 + 沙箱」
只有一份实现。不 import Flask、不 import 任何 db 模块——调用方传入游标。
"""
from datetime import timezone
from utils.script_runner import run_export_script, run_menu_export_script
from utils.export_references import resolve_page_references, resolve_references
from utils.menu_export import get_menu_collections

SCRIPT_SELECT = 'id, name, script, output_format, scope, bound_collection, bound_menu_id'


class ExportBindingError(Exception):
    """脚本目标与其绑定不符。"""


class ExportPermissionError(Exception):
    """当前角色无权导出该目标。"""


def _script_fields(script_row):
    # script_row 形如 (id, name, script, output_format, scope, bound_collection, bound_menu_id)
    return {
        'id': script_row[0], 'name': script_row[1], 'script': script_row[2],
        'output_format': script_row[3], 'scope': script_row[4] or 'page',
        'bound_collection': script_row[5], 'bound_menu_id': script_row[6],
    }


def check_binding(script_row, *, collection=None, menu_id=None):
    s = _script_fields(script_row)
    if s['scope'] == 'menu':
        if s['bound_menu_id'] and menu_id != s['bound_menu_id']:
            raise ExportBindingError(
                f"脚本「{s['name']}」仅限其绑定菜单（{s['bound_menu_id']}），不能用于 {menu_id}")
    else:
        if s['bound_collection'] and collection != s['bound_collection']:
            raise ExportBindingError(
                f"脚本「{s['name']}」仅限其绑定数据页（{s['bound_collection']}），不能用于 {collection}")


def _menu_roles_for_collection(cur, collection):
    cur.execute(
        "SELECT roles FROM menus WHERE page_id = %s OR page_id = %s",
        (collection, f'page-{collection}'))
    row = cur.fetchone()
    return (row[0] or []) if row else []


def _menu_roles_for_menu(cur, menu_id):
    cur.execute("SELECT roles FROM menus WHERE id = %s", (menu_id,))
    row = cur.fetchone()
    return (row[0] or []) if row else []


def check_rbac(cur, *, collection=None, menu_id=None, role=None):
    if role == 'admin':
        return
    roles = _menu_roles_for_menu(cur, menu_id) if menu_id else _menu_roles_for_collection(cur, collection)
    if role not in (roles or []):
        target = menu_id or collection
        raise ExportPermissionError(f"无权限导出：{target}")


def _fetch_page_data(cur, collection, branch_id, record_id=None):
    cur.execute('SELECT name, fields FROM page_configs WHERE id = %s', (f'page-{collection}',))
    pc = cur.fetchone()
    page_name = pc[0] if pc else collection
    fields = pc[1] if pc else []
    if record_id:
        cur.execute('SELECT id, data, created_at FROM dynamic_data '
                    'WHERE collection = %s AND id = %s AND branch_id = %s',
                    (collection, record_id, branch_id))
    else:
        cur.execute('SELECT id, data, created_at FROM dynamic_data '
                    'WHERE collection = %s AND branch_id = %s ORDER BY created_at',
                    (collection, branch_id))
    data = []
    for r in cur.fetchall():
        rec = {'id': r[0]}
        if r[1]:
            rec.update(r[1])
        if r[2] and hasattr(r[2], 'astimezone'):
            rec['createdAt'] = r[2].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        data.append(rec)
    return page_name, fields, data


def execute_bound_export(cur, script_row, *, collection=None, menu_id=None,
                         branch_id='main', role=None, record_id=None):
    """校验绑定 + RBAC，取数解引用跑沙箱。
    page 维度返回 (bytes, filename, content_type)；menu 维度返回 list[(bytes, filename, content_type)]。
    """
    s = _script_fields(script_row)
    check_binding(script_row, collection=collection, menu_id=menu_id)

    if s['scope'] == 'menu':
        check_rbac(cur, menu_id=menu_id, role=role)
        collections = get_menu_collections(cur, menu_id)
        menu_data = []
        for coll in collections:
            page_name, fields, data = _fetch_page_data(cur, coll, branch_id)
            menu_data.append({'collection': coll, 'pageName': page_name, 'records': data,
                              'fields': fields, 'recordCount': len(data)})
        try:
            refs = resolve_references(cur, menu_data, export_branch=branch_id)
        except Exception:
            refs = {}
        return run_menu_export_script(s['script'], menu_data, menu_id, s['output_format'], references=refs)

    # page / row 维度
    check_rbac(cur, collection=collection, role=role)
    page_name, fields, data = _fetch_page_data(cur, collection, branch_id, record_id=record_id)
    try:
        refs = resolve_page_references(cur, collection, data, fields, export_branch=branch_id)
    except Exception:
        refs = {}
    return run_export_script(s['script'], data, fields, page_name, s['output_format'], references=refs)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py -v`
Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
git add server/utils/export_runner.py server/tests/test_export_runner.py
git commit -m "feat(export): shared cursor-injected export_runner with binding+rbac"
```

---

## Task 3: CRUD 读写绑定 + 新建必绑

**Files:**
- Modify: `server/routes/export_scripts.py:24-82`（`row_to_dict` / `create_script`）、`:85-144`（`update_script`）
- Modify (test): `server/tests/test_routes_export_scripts.py`

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_routes_export_scripts.py` 末尾追加（沿用该文件已有的 `setup` fixture 与 admin headers 约定；若 fixture 名不同，按文件实际命名调整）：

```python
class TestExportScriptBinding:
    def test_create_page_script_requires_binding(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result="1"', 'scope': 'page'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 400
        assert '绑定' in resp.get_json()['error']

    def test_create_page_script_with_binding_ok(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result="1"', 'scope': 'page',
                             'boundCollection': 'inspection-case'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 201
        assert resp.get_json()['boundCollection'] == 'inspection-case'

    def test_create_menu_script_with_collection_binding_rejected(self, setup):
        client, mock_cursor, admin_h, _ = setup
        resp = client.post('/exportScripts',
            data=json.dumps({'name': 'x', 'script': 'result=""\nfor t in menu_data:\n    pass',
                             'scope': 'menu', 'boundCollection': 'inspection-case'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code == 400
```

> 注：该文件用 mock cursor。`create_script` 的 INSERT 不读回，故无需新增 fetch mock；若 fixture 现有断言依赖具体 SQL，按需放宽。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_export_scripts.py::TestExportScriptBinding -v`
Expected: FAIL（创建未绑定时当前返回 201，而非 400）。

- [ ] **Step 3: 改 `row_to_dict` 带绑定字段**

把 `server/routes/export_scripts.py` 的 `row_to_dict`（24-35 行）替换为：

```python
def row_to_dict(row):
    return {
        'id': row[0],
        'name': row[1],
        'description': row[2],
        'language': row[3],
        'script': row[4],
        'outputFormat': row[5],
        'createdAt': format_ts(row[6]),
        'updatedAt': format_ts(row[7]),
        'scope': row[8] if len(row) > 8 else 'page',
        'boundCollection': row[9] if len(row) > 9 else None,
        'boundMenuId': row[10] if len(row) > 10 else None,
    }
```

并把 `list_scripts`（43-46 行）与 `update_script` 末尾回读（134-136 行）的两处 SELECT 列表都改为带绑定列：

```python
'SELECT id, name, description, language, script, output_format, created_at, updated_at, scope, bound_collection, bound_menu_id '
'FROM export_scripts ORDER BY created_at'
```

（`update_script` 的回读把 `ORDER BY created_at` 换成 `WHERE id = %s`，保持原样。）

- [ ] **Step 4: 加新建必绑校验 + 写绑定列**

在 `server/routes/export_scripts.py` 顶部加一个校验帮助函数（放在 `row_to_dict` 之后）：

```python
def _normalize_binding(scope, body):
    """返回 (bound_collection, bound_menu_id)；新建必绑、与 scope 一致，否则抛 ValueError。"""
    bc = (body.get('boundCollection') or '').strip() or None
    bm = (body.get('boundMenuId') or '').strip() or None
    if scope == 'menu':
        if bc:
            raise ValueError('菜单维度脚本只能绑定菜单，不能绑定数据页')
        if not bm:
            raise ValueError('菜单维度脚本必须绑定一个菜单')
        return None, bm
    else:
        if bm:
            raise ValueError('数据页维度脚本只能绑定数据页，不能绑定菜单')
        if not bc:
            raise ValueError('数据页维度脚本必须绑定一个数据页')
        return bc, None
```

把 `create_script`（53-82 行）改为校验并写入绑定列：

```python
@export_scripts_bp.route('/exportScripts', methods=['POST'])
@require_permission('admin.export_scripts')
def create_script():
    body = request.get_json(force=True)
    scope = body.get('scope', 'page')
    try:
        validate_export_script_scope(scope, body.get('script', ''))
        bound_collection, bound_menu_id = _normalize_binding(scope, body)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    script_id = body.get('id') or f'script-{uuid.uuid4().hex[:8]}'
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO export_scripts (id, name, description, language, script, output_format, '
            'created_at, updated_at, scope, bound_collection, bound_menu_id) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (script_id, body.get('name', ''), body.get('description', ''),
             body.get('language', 'python'), body.get('script', ''),
             body.get('outputFormat', 'json'), now, now, scope, bound_collection, bound_menu_id),
        )
    log_operation('create', 'export_script', script_id, body.get('name', ''),
                  f'新增导出脚本「{body.get("name", "")}」')
    return jsonify({
        'id': script_id, 'name': body.get('name', ''), 'description': body.get('description', ''),
        'language': body.get('language', 'python'), 'script': body.get('script', ''),
        'outputFormat': body.get('outputFormat', 'json'), 'scope': scope,
        'boundCollection': bound_collection, 'boundMenuId': bound_menu_id,
        'createdAt': format_ts(now), 'updatedAt': format_ts(now),
    }), 201
```

- [ ] **Step 5: update_script 支持改绑定（宽容，允许补绑）**

在 `update_script` 的 `sets`/`params` 拼装段（112-129 行），在 `scope` 分支后追加绑定写入：

```python
        if 'boundCollection' in body:
            sets.append('bound_collection=%s')
            params.append((body.get('boundCollection') or '').strip() or None)
        if 'boundMenuId' in body:
            sets.append('bound_menu_id=%s')
            params.append((body.get('boundMenuId') or '').strip() or None)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_export_scripts.py -v`
Expected: 新增 3 例 + 原有用例全通过。

- [ ] **Step 7: Commit**

```bash
git add server/routes/export_scripts.py server/tests/test_routes_export_scripts.py
git commit -m "feat(export): read/write script binding + new-must-bind validation"
```

---

## Task 4: execute 端点用执行器 + 绑定校验

**Files:**
- Modify: `server/routes/export_scripts.py`（`execute_script`，约 429-505 行）
- Modify (test): `server/tests/test_export_runner.py`（加一条经 HTTP 的端到端，或在 routes 测试加）

- [ ] **Step 1: 写失败测试（路由层，真实 DB 端到端）**

在 `server/tests/test_export_runner.py` 末尾追加（用 Flask test client 验证绑定不符 400）：

```python
def test_execute_endpoint_rejects_binding_mismatch():
    from app import app
    from auth import create_token
    coll, other, sid = 'zzer_e', 'zzer_e2', 'zzer_s5'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_page(cur, other, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
        app.config['TESTING'] = True
        tok = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        c = app.test_client()
        resp = c.post('/exportScripts/execute',
                      json={'scriptId': sid, 'collection': other, 'branchId': 'main'},
                      headers={'Authorization': f'Bearer {tok}'})
        assert resp.status_code == 400
        assert '绑定' in resp.get_json()['error']
    finally:
        _cleanup([coll, other], [sid])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py::test_execute_endpoint_rejects_binding_mismatch -v`
Expected: FAIL（当前 execute 不校验绑定，返回 200）。

- [ ] **Step 3: 改 `execute_script` 调用执行器**

把 `server/routes/export_scripts.py` 的 `execute_script`（429-505 行）整体替换为：

```python
@export_scripts_bp.route('/exportScripts/execute', methods=['POST'])
@login_required
def execute_script():
    """Execute an export script (page scope) and return the generated file."""
    from utils.export_runner import (
        execute_bound_export, ExportBindingError, ExportPermissionError, SCRIPT_SELECT,
    )
    from flask import g
    body = request.get_json(force=True)
    script_id = body.get('scriptId')
    collection = body.get('collection')
    record_id = body.get('recordId')
    branch_id = body.get('branchId', 'main')
    if not script_id or not collection:
        return jsonify({'error': '缺少参数 scriptId 或 collection'}), 400

    role = (g.current_user or {}).get('role')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT {SCRIPT_SELECT} FROM export_scripts WHERE id = %s', (script_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '脚本不存在'}), 404
        try:
            result_bytes, filename, content_type = execute_bound_export(
                cur, row, collection=collection, branch_id=branch_id, role=role, record_id=record_id)
        except ExportBindingError as e:
            return jsonify({'error': str(e)}), 400
        except ExportPermissionError as e:
            return jsonify({'error': str(e)}), 403
        except Exception as e:
            return jsonify({'error': f'脚本执行失败：{str(e)}'}), 400

    return Response(
        result_bytes, mimetype=content_type,
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}",
            'Content-Length': str(len(result_bytes)),
        },
    )
```

> 说明：`g.current_user` 由 `login_required` 注入（与项目其它路由一致）。

- [ ] **Step 4: batchExport 也做绑定校验**

`batch_export`（约 506-575 行）逐个任务跑脚本，当前 SELECT `script, output_format`；改为带绑定列并在跑前 `check_binding`：

```python
        from utils.export_runner import check_binding, ExportBindingError, SCRIPT_SELECT
        ...
                cur.execute(f'SELECT {SCRIPT_SELECT} FROM export_scripts WHERE id = %s', (script_id,))
                script_row = cur.fetchone()
                if not script_row:
                    errors.append(f'任务 {idx + 1}: 脚本 {script_id} 不存在')
                    continue
                try:
                    check_binding(script_row, collection=collection)
                except ExportBindingError as e:
                    errors.append(f'任务 {idx + 1}: {str(e)}')
                    continue
                script_code = script_row[2]
                output_format = script_row[3]
```

（其余取数 + `run_export_script(..., references=refs)` 维持现有实现不变。）

> test/debug 端点是 admin-only 的脚本预览/调试工具（`require_permission('admin.export_scripts')`），本期不强制绑定（admin 调试需对任意目标试跑）；如需收紧另起任务（YAGNI）。

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py tests/test_routes_export_scripts.py tests/test_export_references.py -v`
Expected: 全通过（含原 execute 引用解析回归）。

- [ ] **Step 6: Commit**

```bash
git add server/routes/export_scripts.py server/tests/test_export_runner.py
git commit -m "feat(export): execute+batchExport use export_runner binding enforcement"
```

---

## Task 5: `for-collection` 端点（数据页可用脚本）

**Files:**
- Modify: `server/routes/export_scripts.py`（在 `list_scripts` 之后加新端点）
- Modify (test): `server/tests/test_export_runner.py`

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_export_runner.py` 末尾追加：

```python
def test_for_collection_returns_bound_scripts():
    from app import app
    from auth import create_token
    coll, sid = 'zzer_f', 'zzer_s6'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
        app.config['TESTING'] = True
        tok = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        c = app.test_client()
        resp = c.get(f'/exportScripts/for-collection/{coll}',
                     headers={'Authorization': f'Bearer {tok}'})
        assert resp.status_code == 200
        ids = [s['id'] for s in resp.get_json()]
        assert sid in ids
    finally:
        _cleanup([coll], [sid])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py::test_for_collection_returns_bound_scripts -v`
Expected: FAIL（404，端点不存在）。

- [ ] **Step 3: 实现端点**

在 `server/routes/export_scripts.py` 的 `list_scripts` 之后插入：

```python
@export_scripts_bp.route('/exportScripts/for-collection/<collection>', methods=['GET'])
@login_required
def scripts_for_collection(collection):
    """该数据页可用的导出脚本 = 绑定到它的脚本 ∪ 该页 page_configs.export_scripts 里的未绑定脚本。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, description, language, script, output_format, created_at, updated_at, '
            'scope, bound_collection, bound_menu_id FROM export_scripts '
            'WHERE bound_collection = %s', (collection,))
        rows = list(cur.fetchall())
        bound_ids = {r[0] for r in rows}
        # 兼容旧 opt-in：page_configs.export_scripts 里登记、但未绑定的脚本
        cur.execute('SELECT export_scripts FROM page_configs WHERE id = %s', (f'page-{collection}',))
        pc = cur.fetchone()
        legacy_ids = [sid for sid in (pc[0] if pc and pc[0] else []) if sid not in bound_ids]
        if legacy_ids:
            cur.execute(
                'SELECT id, name, description, language, script, output_format, created_at, updated_at, '
                'scope, bound_collection, bound_menu_id FROM export_scripts '
                'WHERE id = ANY(%s) AND bound_collection IS NULL AND bound_menu_id IS NULL',
                (legacy_ids,))
            rows.extend(cur.fetchall())
    return jsonify([row_to_dict(r) for r in rows])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py::test_for_collection_returns_bound_scripts -v`
Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
git add server/routes/export_scripts.py server/tests/test_export_runner.py
git commit -m "feat(export): GET /exportScripts/for-collection endpoint"
```

---

## Task 6: 前端 — 绑定表单 + 数据页导出菜单

**Files:**
- Modify: `src/types/exportScript.ts`（ExportScript 类型加 `boundCollection?`/`boundMenuId?`；若类型在别处，按实际路径）
- Modify: `src/views/admin/ExportScriptManager.vue`（绑定选择器 + 必填 + 未绑定标签）
- Modify: `src/api/exportScript.ts`（如有；`for-collection` 取数）
- Modify: `src/views/dynamic/DynamicPage.vue`（导出菜单数据源改用 for-collection）

- [ ] **Step 1: 类型加绑定字段**

在 `src/types/exportScript.ts`（或定义 `ExportScript` 的文件，用 `grep -rn "interface ExportScript" src/`）的接口里加：

```typescript
  boundCollection?: string | null
  boundMenuId?: string | null
```

- [ ] **Step 2: ExportScriptManager 加绑定选择器**

在 `src/views/admin/ExportScriptManager.vue` 的脚本编辑表单里，「导出维度(scope)」选择项后新增「绑定目标」表单项（随 scope 切换；`collectionOptions`/`menuOptions` 仿 `WidgetEditDialog.vue` 的 `collectionOptions` 用 menuStore 派生）：

```vue
<el-form-item label="绑定目标" required>
  <el-select
    v-if="form.scope !== 'menu'"
    v-model="form.boundCollection"
    placeholder="选择绑定的数据页"
    filterable clearable style="width: 100%">
    <el-option v-for="opt in collectionOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
  </el-select>
  <el-select
    v-else
    v-model="form.boundMenuId"
    placeholder="选择绑定的菜单"
    filterable clearable style="width: 100%">
    <el-option v-for="opt in menuOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
  </el-select>
  <div class="form-hint">导出脚本专项专用：只能用于其绑定的{{ form.scope === 'menu' ? '菜单' : '数据页' }}</div>
</el-form-item>
```

脚本（`<script setup>`）里加 `collectionOptions`/`menuOptions` 计算属性（用 `useMenuStore()`，`menuList.filter(m=>m.menuType==='data' && m.pageId)` → collection 由 `pageId.replace(/^page-/,'')`；菜单项用全部菜单或 `menuType` 为 menu/project 的）。切换 scope 时清空另一侧绑定：`watch(()=>form.value.scope, ()=>{ form.value.boundCollection=''; form.value.boundMenuId='' })`。

- [ ] **Step 3: 列表给未绑定脚本标签**

在脚本列表行（表格或卡片）渲染处，未绑定时显示灰标签：

```vue
<el-tag v-if="!row.boundCollection && !row.boundMenuId" type="info" size="small">未绑定</el-tag>
```

- [ ] **Step 4: 保存校验（前端兜底）**

在保存方法开头加：

```typescript
const needCollection = form.value.scope !== 'menu'
if (needCollection && !form.value.boundCollection) { ElMessage.warning('请先绑定一个数据页'); return }
if (!needCollection && !form.value.boundMenuId) { ElMessage.warning('请先绑定一个菜单'); return }
```

（仅新建时强制；编辑存量脚本若想保留未绑定，可放开——按 spec「新建必绑、存量宽容」：仅当 `isCreate` 时校验。）

- [ ] **Step 5: DynamicPage 导出菜单用 for-collection**

在 `src/views/dynamic/DynamicPage.vue` 里，原本取该页导出脚本的逻辑（`grep -n "exportScripts\|导出" src/views/dynamic/DynamicPage.vue` 定位），改为调用新端点：

```typescript
import { get } from '@/utils/request'
// 加载该页可用导出脚本（绑定驱动 + 兼容旧 opt-in）
const exportScripts = ref<ExportScript[]>([])
async function loadExportScripts() {
  try { exportScripts.value = await get<ExportScript[]>(`/exportScripts/for-collection/${collection.value}`) }
  catch { exportScripts.value = [] }
}
```

在页面加载/`collection` 变化时调用 `loadExportScripts()`，导出下拉用 `exportScripts`。

- [ ] **Step 6: 类型检查 + 构建**

Run: `npx vue-tsc --noEmit -p tsconfig.json`
Expected: 无输出（通过）。

- [ ] **Step 7: 手测（Playwright 或手动）**

启动 dev（`npm run dev` + `npm run server`），在 设置中心→导出脚本 新建一个 page 维度脚本不绑定 → 保存被拦；绑定到某数据页后保存成功；到该数据页导出菜单能看到它，到别的数据页看不到。

- [ ] **Step 8: Commit**

```bash
git add src/types/exportScript.ts src/views/admin/ExportScriptManager.vue src/api/exportScript.ts src/views/dynamic/DynamicPage.vue
git commit -m "feat(export): script binding UI + binding-driven page export menu"
```

---

## Task 7: MCP 工具 — `list_export_scripts`

**Files:**
- Create: `mcp-server/tools/_server_imports.py`
- Create: `mcp-server/tools/list_export_scripts.py`
- Modify: `mcp-server/tools/__init__.py`
- Create (test): `mcp-server/tests/test_list_export_scripts.py`

- [ ] **Step 1: server 路径桥接模块**

写 `mcp-server/tools/_server_imports.py`：

```python
"""把 <repo>/server 加进 sys.path，供 MCP 工具复用 server/utils 的纯函数
（export_runner / script_runner / export_references，均 Flask-free）。"""
import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent.parent / 'server'
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))
```

- [ ] **Step 2: 写失败测试**

写 `mcp-server/tests/test_list_export_scripts.py`（仿 `test_export_collection_excel.py` 的 `_fake_db_seq`/`_ctx`）：

```python
from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_db(rows_by_call):
    calls = {"i": 0}
    cur = MagicMock()
    cur.execute.side_effect = lambda sql, params=None: None
    def fetchall():
        r = rows_by_call[calls["i"]]; calls["i"] += 1; return r
    cur.fetchall.side_effect = fetchall
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    return _get


def test_list_only_bound_scripts_for_role():
    # 1) 已绑定脚本; 2) 每个脚本目标菜单 roles
    rows = [
        [('s1', '巡检导出', 'desc', 'page', 'inspection-case', None),
         ('s2', '机密导出', 'd2', 'page', 'secret-col', None)],
        [(['admin', 'developer'],)],   # s1 target roles
        [(['admin'],)],                # s2 target roles (developer 看不到)
    ]
    with patch('tools.list_export_scripts.get_db', _fake_db(rows)):
        from tools.list_export_scripts import handle
        out = handle({}, _ctx('developer'))
    ids = [s['id'] for s in out['scripts']]
    assert ids == ['s1']
    assert out['scripts'][0]['target'] == 'page:inspection-case'
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd mcp-server && python -m pytest tests/test_list_export_scripts.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 4: 实现工具**

写 `mcp-server/tools/list_export_scripts.py`：

```python
"""Tool: list_export_scripts — 列出当前用户可访问、已绑定的导出脚本。"""
import mcp.types as types
from db import get_db
from context import ToolContext

NAME = "list_export_scripts"

TOOL = types.Tool(
    name=NAME,
    description=(
        "列出当前可用的导出脚本（仅已绑定到数据页/菜单、且你的角色有权访问的）。"
        "返回 id/name/description/target/outputFormat。当用户想用导出脚本导出数据时，"
        "先用本工具找到合适的脚本 id，再调 run_export_script。"
    ),
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
)


def _roles_for_collection(cur, collection):
    cur.execute("SELECT roles FROM menus WHERE page_id = %s OR page_id = %s",
                (collection, f'page-{collection}'))
    r = cur.fetchall()
    return (r[0][0] or []) if r else []


def _roles_for_menu(cur, menu_id):
    cur.execute("SELECT roles FROM menus WHERE id = %s", (menu_id,))
    r = cur.fetchall()
    return (r[0][0] or []) if r else []


def handle(input: dict, ctx: ToolContext) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, scope, bound_collection, bound_menu_id "
            "FROM export_scripts WHERE bound_collection IS NOT NULL OR bound_menu_id IS NOT NULL "
            "ORDER BY name")
        rows = cur.fetchall()
        out = []
        for sid, name, desc, scope, bc, bm in rows:
            if bm:
                roles = _roles_for_menu(cur, bm); target = f"menu:{bm}"
            else:
                roles = _roles_for_collection(cur, bc); target = f"page:{bc}"
            if ctx.role != "admin" and ctx.role not in (roles or []):
                continue
            out.append({"id": sid, "name": name, "description": desc or "",
                        "target": target, "scope": scope})
    return {"scripts": out, "count": len(out)}
```

- [ ] **Step 5: 注册工具**

在 `mcp-server/tools/__init__.py` 顶部 import 增加 `list_export_scripts`，并在 `_TOOLS` 字典加：

```python
    list_export_scripts.NAME: (list_export_scripts.TOOL, list_export_scripts.handle),
```

（同时把 `list_export_scripts` 加入 `from tools import (...)` 那行。）

- [ ] **Step 6: 跑测试确认通过**

Run: `cd mcp-server && python -m pytest tests/test_list_export_scripts.py tests/test_tools_endpoint.py -v`
Expected: 通过（含 /tools 列表新增该工具）。

- [ ] **Step 7: Commit**

```bash
git add mcp-server/tools/_server_imports.py mcp-server/tools/list_export_scripts.py mcp-server/tools/__init__.py mcp-server/tests/test_list_export_scripts.py
git commit -m "feat(mcp): list_export_scripts tool (RBAC + binding filtered)"
```

---

## Task 8: MCP 工具 — `run_export_script`

**Files:**
- Create: `mcp-server/tools/run_export_script.py`
- Modify: `mcp-server/tools/__init__.py`
- Create (test): `mcp-server/tests/test_run_export_script.py`

- [ ] **Step 1: 写失败测试**

写 `mcp-server/tests/test_run_export_script.py`：

```python
from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _ctx(role="admin"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def test_run_writes_output_and_summary(tmp_path):
    # script_row + workspace_path
    script_row = ('s1', 'JSON导出', "result = '[1,2,3]'", 'json', 'page', 'col-x', None)
    cur = MagicMock()
    seq = {"i": 0}
    def fetchone():
        vals = [script_row, (str(tmp_path),)]
        v = vals[seq["i"]]; seq["i"] += 1; return v
    cur.fetchone.side_effect = fetchone
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    # 桩掉真正的执行器，聚焦工具的「写 outputs + 摘要」职责
    def fake_exec(cur_, row, **kw):
        return (b'[1,2,3]', 'col-x.json', 'application/json')
    with patch('tools.run_export_script.get_db', _get), \
         patch('tools.run_export_script.execute_bound_export', fake_exec):
        from tools.run_export_script import handle
        out = handle({'script_id': 's1'}, _ctx('admin'))
    assert out['saved'] is True
    assert out['path'].startswith('outputs/')
    assert out['preview'] == '[1,2,3]'
    files = list((tmp_path / 'outputs').glob('*.json'))
    assert len(files) == 1


def test_run_rejects_unbound_script(tmp_path):
    script_row = ('s2', '未绑定', "result='x'", 'json', 'page', None, None)
    cur = MagicMock(); cur.fetchone.return_value = script_row
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    with patch('tools.run_export_script.get_db', _get):
        from tools.run_export_script import handle, RunExportError
        import pytest
        with pytest.raises(RunExportError):
            handle({'script_id': 's2'}, _ctx('admin'))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mcp-server && python -m pytest tests/test_run_export_script.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现工具**

写 `mcp-server/tools/run_export_script.py`：

```python
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
        "并返回文件名/行数/前若干字预览。先用 list_export_scripts 拿到 script_id。"
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
        safe = filename or f"{script_id}-{ts}{_EXT.get(row[3], '.dat')}"
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
```

- [ ] **Step 4: 注册工具**

在 `mcp-server/tools/__init__.py` 顶部 import 增加 `run_export_script`，并在 `_TOOLS` 加：

```python
    run_export_script.NAME: (run_export_script.TOOL, run_export_script.handle),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd mcp-server && python -m pytest tests/test_run_export_script.py tests/test_tools_endpoint.py -v`
Expected: 通过。

- [ ] **Step 6: Commit**

```bash
git add mcp-server/tools/run_export_script.py mcp-server/tools/__init__.py mcp-server/tests/test_run_export_script.py
git commit -m "feat(mcp): run_export_script tool writes result to session outputs/"
```

---

## Task 9: 文档同步

**Files:**
- Modify: `docs/user-guide/admin/scripts.md`
- Create: `docs/user-guide/ai/export-via-chat.md`
- Modify: `docs/user-guide/README.md`（索引加新页）

- [ ] **Step 1: scripts.md 补绑定说明**

在 `docs/user-guide/admin/scripts.md` 导出脚本一节加一段：

```markdown
### 导出脚本绑定（专项专用）

每个导出脚本必须**绑定**到一个目标，只能用于该目标，避免「什么数据都能跑」：

- **数据页维度（scope=page）** 的脚本绑定到一个**数据页**；
- **菜单维度（scope=menu）** 的脚本绑定到一个**菜单**。

新建脚本时「绑定目标」必填。数据页的导出菜单只列出绑定到它的脚本（旧的页面 opt-in 仍兼容）。
执行时若目标与绑定不符会被拒绝。存量未绑定脚本仍可运行（标「未绑定」），建议补绑。
```

- [ ] **Step 2: 新建 AI 导出文档**

写 `docs/user-guide/ai/export-via-chat.md`：

```markdown
# 在 AI 助手中调用导出脚本

AI 助手可直接调用**已绑定**的导出脚本，把结果作为可下载文件放进会话。

1. 在对话里说明你的导出意图（例如「把巡检用例导出成 JSON」）。
2. 助手用 `list_export_scripts` 找到合适的脚本，再用 `run_export_script` 执行。
3. 导出结果写入本次会话的产出目录，**对话中出现可下载文件**，并附文件名/行数/预览摘要。

约束：
- 仅**已绑定**脚本可经 AI 助手调用；未绑定脚本请先在 设置中心→导出脚本 绑定目标。
- 权限按目标（数据页/菜单）的可见角色控制：无权访问该目标的角色无法导出。
```

- [ ] **Step 3: 索引登记**

在 `docs/user-guide/README.md` 的 AI 章节下加一行链接到 `ai/export-via-chat.md`（仿现有条目格式）。

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/admin/scripts.md docs/user-guide/ai/export-via-chat.md docs/user-guide/README.md
git commit -m "docs(export): script binding + AI-chat export usage"
```

---

## Final: 全量回归

- [ ] **后端导出回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_export_runner.py tests/test_export_references.py tests/test_routes_export_scripts.py tests/test_menu_export.py tests/test_routes_menu_export.py tests/test_data_export.py -v`
Expected: 全通过。

- [ ] **MCP 回归**

Run: `cd mcp-server && python -m pytest tests/ -v`
Expected: 全通过。

- [ ] **前端类型检查**

Run: `npx vue-tsc --noEmit -p tsconfig.json`
Expected: 无输出。

- [ ] **收尾**：用 `superpowers:finishing-a-development-branch` 完成分支（合并/PR）。

---

## 备注（实现者注意）

- `execute_bound_export` 对 **menu 维度**返回的是 `list[(bytes, filename, content_type)]`；`run_export_script` MCP 工具与 menu-export 路由都按 list 处理；page 维度返回单个三元组。Task 7/8 与 Task 2 的返回约定务必一致。
- MCP 复用 `server/utils/export_runner` 时，确保 `mcp-server` 进程能 import `utils.export_runner`（靠 `_server_imports.py` 注入 `<repo>/server` 到 `sys.path`）。其链路只依赖 `utils.script_runner`（stdlib + pandas，MCP venv 已含）与 `utils.export_references`（纯函数，传入游标），不拉 Flask/db 模块。
- 菜单维度的 RBAC/绑定校验已在 `execute_bound_export` 内完成；现有 `server/utils/menu_export.py` 的生产菜单导出不在本计划改动范围（其已注入 references），如需对菜单导出也强制脚本绑定，另起任务（YAGNI，本期不做）。
```
