# 智能客服热问/自助面板 — Stage ①（后端 + 管理编辑器）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 交付热门问题的后端（`kefu_faq_items` 表 + 公开只读列表/点击统计 API + 管理 CRUD/排序 API）与管理端热问编辑器，端到端可 curl/pytest + 管理端 Playwright 验证。

**Architecture:** 沿用 Phase 1 kefu 架构：新表 `kefu_faq_items`（每条热问一行，支持点击自增与逐条排序）；公开只读经 `kefu_public_bp`、管理 CRUD 经 `kefu_admin_bp`；前端新增 `src/api/kefu.ts` + 管理页 `KefuManager.vue`。访客面板 UI 属 Stage ②。

**Tech Stack:** Python Flask + psycopg2 + PostgreSQL（JSONB 不用，热问用真列）；Vue 3 + Element Plus + md-editor-v3；pytest（Windows 需 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）。

## Global Constraints

- 数据模型用**独立表 `kefu_faq_items`**（非 JSONB）。每条热问属于某个 `kefu_instances`，`ON DELETE CASCADE`。
- 公开端点在 `kefu_public_bp`（无 JWT，`X-Visitor-Id`）；管理端点在 `kefu_admin_bp`（`@require_permission('admin.kefu')`）。两蓝图已在 `dynamic_bp` 之前注册（Phase 1 已完成，勿改注册顺序）。
- 点击埋点用**独立限速桶**（key 前缀 `faqclick:`，复用 `RateLimiter` 实例 `_limiter` 但独立于消息桶），不挤占对话额度。
- 公开 FAQ 列表只返回 `enabled=true` 的项，按 `sort_order` 升序，**含答案**（一次拉全）。答案是 Markdown 源码。
- 迁移随 `server/migrate_kefu.py` 的 `_SQL` 幂等追加，并在 `server/init_db.py` 平行加同等 DDL（新库直接带上）。
- 后端测试从 `server/` 运行，需 env `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。前端 API 走 `@/utils/request` 的 `{get,post,put,del}`，baseURL `/api` 会被 vite 重写去掉 `/api`（故路径写 `/admin/kefu/...`）。
- 提交信息用中文 `feat:`/`fix:`/`test:`/`docs:` 前缀。
- 本 Stage 不做访客面板 UI / `/kefu/:slug` 页（Stage ②）。

---

### Task 1: 数据库迁移 `kefu_faq_items`

**Files:**
- Modify: `server/migrate_kefu.py`（`_SQL` 追加建表）
- Modify: `server/init_db.py`（平行加同等 DDL，紧邻 `kefu_instances` 的 DDL 之后）
- Test: `server/tests/test_kefu_faq_migration.py`（真库 `db_conn` fixture）

**Interfaces:**
- Produces: 表 `kefu_faq_items(id, instance_id, question, answer, category, sort_order, click_count, enabled, created_at, updated_at)` + 索引 `idx_kefu_faq_instance(instance_id, sort_order)`。`migrate_kefu(conn)` 仍幂等。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_faq_migration.py
from migrate_kefu import migrate_kefu


def _col(cur, table, col):
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s", (table, col))
    return cur.fetchone() is not None


def test_migrate_creates_faq_table(db_conn):
    migrate_kefu(db_conn)
    migrate_kefu(db_conn)  # idempotent
    cur = db_conn.cursor()
    cur.execute("SELECT to_regclass('public.kefu_faq_items')")
    assert cur.fetchone()[0] is not None
    for c in ('instance_id', 'question', 'answer', 'category',
              'sort_order', 'click_count', 'enabled'):
        assert _col(cur, 'kefu_faq_items', c)
    db_conn.rollback()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_migration.py -v`
Expected: FAIL（表不存在）

- [ ] **Step 3: 在 `migrate_kefu.py` 的 `_SQL` 末尾（`INSERT INTO roles ...` 之后）追加**

```sql

CREATE TABLE IF NOT EXISTS kefu_faq_items (
  id           VARCHAR(100) PRIMARY KEY,
  instance_id  VARCHAR(100) NOT NULL REFERENCES kefu_instances(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  category     VARCHAR(100),
  sort_order   INTEGER NOT NULL DEFAULT 0,
  click_count  INTEGER NOT NULL DEFAULT 0,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kefu_faq_instance ON kefu_faq_items(instance_id, sort_order);
```

- [ ] **Step 4: 在 `server/init_db.py` 中定位 `kefu_instances` 的 CREATE TABLE 块，其后紧邻插入同样的 `kefu_faq_items` CREATE TABLE + 索引 DDL**（新库建库时直接带上；grep `kefu_instances` 找到位置）。

- [ ] **Step 5: 运行确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_migration.py -v`
Expected: PASS

- [ ] **Step 6: 在开发库执行一次迁移**

Run: `cd server && python migrate_kefu.py` → 打印 `kefu migration done`

- [ ] **Step 7: 提交**

```bash
git add server/migrate_kefu.py server/init_db.py server/tests/test_kefu_faq_migration.py
git commit -m "feat(kefu): kefu_faq_items 热问表迁移"
```

---

### Task 2: 热问数据层 `kefu_repo` FAQ 函数

**Files:**
- Modify: `server/utils/kefu_repo.py`（追加 FAQ 段）
- Test: `server/tests/test_kefu_faq_repo.py`（mocked `get_db`）

**Interfaces:**
- Consumes: `get_db`, `secrets`（模块已 import）。
- Produces（追加到 `kefu_repo.py`）：
  - `_row_to_faq(r) -> dict` → `{id, instance_id, question, answer, category, sort_order, click_count, enabled}`
  - `create_faq(instance_id, payload) -> dict`
  - `list_faq_admin(instance_id) -> list[dict]`（全部，按 sort_order,created_at）
  - `list_faq_public(instance_id) -> list[dict]`（仅 enabled；每项 `{id, question, answer, category}`）
  - `get_faq(faq_id) -> dict | None`
  - `update_faq(faq_id, payload) -> dict | None`
  - `delete_faq(faq_id) -> bool`
  - `reorder_faq(instance_id, id_list) -> None`（按下标写 sort_order，限该 instance）
  - `increment_faq_click(instance_id, faq_id) -> bool`（`UPDATE ... SET click_count=click_count+1 WHERE id=%s AND instance_id=%s AND enabled=true`；返回是否命中）

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_faq_repo.py
from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def test_create_faq_inserts_scoped_to_instance(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = (
        'faq_1', 'kf_1', 'Q?', 'A', 'billing', 0, 0, True)
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        out = repo.create_faq('kf_1', {'question': 'Q?', 'answer': 'A', 'category': 'billing'})
    assert out['id'] == 'faq_1' and out['instance_id'] == 'kf_1'
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'kefu_faq_items' in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert 'kf_1' in params and 'Q?' in params


def test_increment_click_sql_scoped_and_enabled(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        hit = repo.increment_faq_click('kf_1', 'faq_1')
    assert hit is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'click_count = click_count + 1' in sql
    assert 'enabled' in sql and 'instance_id' in sql


def test_reorder_writes_sort_order_by_index(mock_conn, mock_cursor):
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.reorder_faq('kf_1', ['faq_b', 'faq_a'])
    calls = [c.args for c in mock_cursor.execute.call_args_list if c.args[1]]
    # faq_b → sort_order 0, faq_a → sort_order 1, both scoped to kf_1
    flat = [c[1] for c in calls]
    assert (0, 'faq_b', 'kf_1') in flat and (1, 'faq_a', 'kf_1') in flat
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_repo.py -v`
Expected: FAIL（函数未定义）

- [ ] **Step 3: 追加实现到 `kefu_repo.py` 末尾**

```python
# ---- FAQ (热门问题) ----
_FAQ_COLS = ("id, instance_id, question, answer, category, "
             "sort_order, click_count, enabled")


def _row_to_faq(r) -> dict:
    return {
        'id': r[0], 'instance_id': r[1], 'question': r[2], 'answer': r[3],
        'category': r[4], 'sort_order': r[5], 'click_count': r[6], 'enabled': r[7],
    }


def create_faq(instance_id: str, payload: dict) -> dict:
    fid = 'faq_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO kefu_faq_items "
            "(id, instance_id, question, answer, category, sort_order, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            f"RETURNING {_FAQ_COLS}",
            (fid, instance_id, payload['question'], payload['answer'],
             payload.get('category') or None, int(payload.get('sort_order') or 0),
             payload.get('enabled', True)),
        )
        return _row_to_faq(cur.fetchone())


def list_faq_admin(instance_id: str) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_FAQ_COLS} FROM kefu_faq_items WHERE instance_id=%s "
            "ORDER BY sort_order ASC, created_at ASC", (instance_id,))
        return [_row_to_faq(r) for r in cur.fetchall()]


def list_faq_public(instance_id: str) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, question, answer, category FROM kefu_faq_items "
            "WHERE instance_id=%s AND enabled=true "
            "ORDER BY sort_order ASC, created_at ASC", (instance_id,))
        return [{'id': r[0], 'question': r[1], 'answer': r[2], 'category': r[3]}
                for r in cur.fetchall()]


def get_faq(faq_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_FAQ_COLS} FROM kefu_faq_items WHERE id=%s", (faq_id,))
        r = cur.fetchone()
        return _row_to_faq(r) if r else None


def update_faq(faq_id: str, payload: dict):
    fields, params = [], []
    for col in ('question', 'answer', 'category', 'sort_order', 'enabled'):
        if col in payload:
            fields.append(f"{col}=%s")
            val = payload[col]
            if col == 'category' and val == '':
                val = None
            params.append(val)
    if not fields:
        return get_faq(faq_id)
    fields.append("updated_at=now()")
    params.append(faq_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE kefu_faq_items SET {', '.join(fields)} WHERE id=%s "
                    f"RETURNING {_FAQ_COLS}", tuple(params))
        r = cur.fetchone()
        return _row_to_faq(r) if r else None


def delete_faq(faq_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM kefu_faq_items WHERE id=%s", (faq_id,))
        return cur.rowcount > 0


def reorder_faq(instance_id: str, id_list: list) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        for idx, fid in enumerate(id_list):
            cur.execute(
                "UPDATE kefu_faq_items SET sort_order=%s, updated_at=now() "
                "WHERE id=%s AND instance_id=%s",
                (idx, fid, instance_id))


def increment_faq_click(instance_id: str, faq_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE kefu_faq_items SET click_count = click_count + 1 "
            "WHERE id=%s AND instance_id=%s AND enabled=true",
            (faq_id, instance_id))
        return cur.rowcount > 0
```

- [ ] **Step 4: 运行确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_repo.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/utils/kefu_repo.py server/tests/test_kefu_faq_repo.py
git commit -m "feat(kefu): 热问数据层 CRUD/排序/点击自增"
```

---

### Task 3: 管理 FAQ 端点

**Files:**
- Modify: `server/routes/kefu_admin.py`
- Test: `server/tests/test_kefu_faq_admin_routes.py`

**Interfaces:**
- Consumes: `kefu_repo.list_faq_admin/create_faq/get_faq/update_faq/delete_faq/reorder_faq`, `kefu_repo.get_instance`.
- Produces 端点（全部 `@require_permission('admin.kefu')`，前缀 `/admin/kefu`）：
  - `GET  /instances/<iid>/faq`
  - `POST /instances/<iid>/faq`（`question`+`answer` 必填→否则 400；实例不存在→404）
  - `PATCH  /instances/<iid>/faq/<fid>`（fid 归属校验→404）
  - `DELETE /instances/<iid>/faq/<fid>`
  - `PATCH  /instances/<iid>/faq/reorder`（body `{order:[...]}`）

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_faq_admin_routes.py
from unittest.mock import patch

FAQ = {'id': 'faq_1', 'instance_id': 'kf_1', 'question': 'Q?', 'answer': 'A',
       'category': None, 'sort_order': 0, 'click_count': 0, 'enabled': True}


def test_list_faq_requires_permission(client, dev_headers):
    r = client.get('/admin/kefu/instances/kf_1/faq', headers=dev_headers)
    assert r.status_code == 403


def test_create_faq_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value={'id': 'kf_1'}), \
         patch('routes.kefu_admin.kefu_repo.create_faq', return_value=FAQ) as m:
        r = client.post('/admin/kefu/instances/kf_1/faq',
                        json={'question': 'Q?', 'answer': 'A'}, headers=admin_headers)
    assert r.status_code == 201 and r.get_json()['id'] == 'faq_1'
    m.assert_called_once()


def test_create_faq_validates(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value={'id': 'kf_1'}):
        r = client.post('/admin/kefu/instances/kf_1/faq',
                        json={'question': 'Q?'}, headers=admin_headers)  # missing answer
    assert r.status_code == 400


def test_create_faq_instance_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value=None):
        r = client.post('/admin/kefu/instances/nope/faq',
                        json={'question': 'Q?', 'answer': 'A'}, headers=admin_headers)
    assert r.status_code == 404


def test_patch_faq_ownership_404(client, admin_headers):
    other = {**FAQ, 'instance_id': 'other'}
    with patch('routes.kefu_admin.kefu_repo.get_faq', return_value=other):
        r = client.patch('/admin/kefu/instances/kf_1/faq/faq_1',
                         json={'question': 'X'}, headers=admin_headers)
    assert r.status_code == 404


def test_reorder_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.reorder_faq') as m:
        r = client.patch('/admin/kefu/instances/kf_1/faq/reorder',
                         json={'order': ['faq_b', 'faq_a']}, headers=admin_headers)
    assert r.status_code == 200
    m.assert_called_once_with('kf_1', ['faq_b', 'faq_a'])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_admin_routes.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 追加到 `kefu_admin.py`**

```python
def _faq_owned(iid, fid):
    faq = kefu_repo.get_faq(fid)
    return faq if (faq and faq['instance_id'] == iid) else None


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['GET'])
@require_permission('admin.kefu')
def list_faq(iid):
    return jsonify({'items': kefu_repo.list_faq_admin(iid)})


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['POST'])
@require_permission('admin.kefu')
def create_faq(iid):
    if not kefu_repo.get_instance(iid):
        return jsonify({'error': 'instance not found'}), 404
    body = request.get_json(silent=True) or {}
    if not (body.get('question') or '').strip() or not (body.get('answer') or '').strip():
        return jsonify({'error': 'question 与 answer 必填'}), 400
    faq = kefu_repo.create_faq(iid, body)
    log_operation('create', 'kefu_faq_item', faq['id'], faq['question'][:50], '新建热问')
    return jsonify(faq), 201


@kefu_admin_bp.route('/instances/<iid>/faq/reorder', methods=['PATCH'])
@require_permission('admin.kefu')
def reorder_faq(iid):
    order = (request.get_json(silent=True) or {}).get('order')
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list'}), 400
    kefu_repo.reorder_faq(iid, order)
    return jsonify({'ok': True})


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['PATCH'])
@require_permission('admin.kefu')
def update_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    faq = kefu_repo.update_faq(fid, request.get_json(silent=True) or {})
    log_operation('update', 'kefu_faq_item', fid, faq['question'][:50], '更新热问')
    return jsonify(faq)


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['DELETE'])
@require_permission('admin.kefu')
def delete_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.delete_faq(fid)
    log_operation('delete', 'kefu_faq_item', fid, fid, '删除热问')
    return jsonify({'ok': True})
```

> 注意路由声明顺序：`/faq/reorder` 必须在 `/faq/<fid>` 之前声明，否则 `reorder` 会被 `<fid>` 捕获。上面的顺序已正确。

- [ ] **Step 4: 运行确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_admin_routes.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/routes/kefu_admin.py server/tests/test_kefu_faq_admin_routes.py
git commit -m "feat(kefu): 管理端热问 CRUD/排序 API"
```

---

### Task 4: 公开 FAQ 端点（列表 + 点击）

**Files:**
- Modify: `server/routes/kefu_public.py`
- Test: `server/tests/test_kefu_faq_public_routes.py`

**Interfaces:**
- Consumes: `kefu_repo.get_instance_by_slug/list_faq_public/increment_faq_click`, 模块内 `_limiter`, `_visitor_id`, `_client_ip`。
- Produces：
  - `GET /kefu/i/<slug>/faq` → `{items:[{id,question,answer,category}]}`；实例不存在→404；停用→`{items:[]}`。
  - `POST /kefu/i/<slug>/faq/<fid>/click` → 204；独立点击限速桶（`faqclick:` 前缀）；实例不存在→404；id 不属于实例/停用→静默 204。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_faq_public_routes.py
from unittest.mock import patch

INST = {'id': 'kf_1', 'slug': 'presale', 'enabled': True}


def test_public_faq_list(client):
    items = [{'id': 'faq_1', 'question': 'Q?', 'answer': 'A', 'category': 'billing'}]
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.list_faq_public', return_value=items):
        r = client.get('/kefu/i/presale/faq')
    assert r.status_code == 200 and r.get_json()['items'][0]['id'] == 'faq_1'


def test_public_faq_list_disabled_empty(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug',
               return_value={**INST, 'enabled': False}):
        r = client.get('/kefu/i/presale/faq')
    assert r.status_code == 200 and r.get_json()['items'] == []


def test_public_faq_list_404(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=None):
        r = client.get('/kefu/i/none/faq')
    assert r.status_code == 404


def test_click_increments(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.increment_faq_click', return_value=True) as m:
        r = client.post('/kefu/i/presale/faq/faq_1/click')
    assert r.status_code == 204
    m.assert_called_once_with('kf_1', 'faq_1')


def test_click_unknown_silent_204(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.increment_faq_click', return_value=False):
        r = client.post('/kefu/i/presale/faq/nope/click')
    assert r.status_code == 204
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_public_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 追加到 `kefu_public.py`**

```python
def _faqclick_ok(inst_id, vid):
    key = f"faqclick:{inst_id}:{vid}:{_client_ip()}"
    # 独立于消息桶：宽松上限，仅防明显灌水。
    return _limiter.allow(key, 120, 5000)


@kefu_public_bp.route('/i/<slug>/faq', methods=['GET'])
def faq_list(slug):
    inst = kefu_repo.get_instance_by_slug(slug)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    if not inst.get('enabled', True):
        return jsonify({'items': []})
    return jsonify({'items': kefu_repo.list_faq_public(inst['id'])})


@kefu_public_bp.route('/i/<slug>/faq/<fid>/click', methods=['POST'])
def faq_click(slug, fid):
    inst = kefu_repo.get_instance_by_slug(slug)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    if _faqclick_ok(inst['id'], _visitor_id()):
        kefu_repo.increment_faq_click(inst['id'], fid)  # 静默：命中与否都 204
    return ('', 204)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_faq_public_routes.py -v`
Expected: PASS

- [ ] **Step 5: 全量后端回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -q`
Expected: 除既有预存失败 `test_ai_scan_engine::test_run_one_invokes_scan_hook_on_success` 外全绿。

- [ ] **Step 6: 提交**

```bash
git add server/routes/kefu_public.py server/tests/test_kefu_faq_public_routes.py
git commit -m "feat(kefu): 公开热问列表 + 点击统计端点"
```

---

### Task 5: 前端管理编辑器 `KefuManager.vue`

**Files:**
- Create: `src/api/kefu.ts`
- Create: `src/views/admin/KefuManager.vue`
- Modify: `src/router/index.ts`（新增 `/admin/kefu` 路由，登录 + `admin.kefu` 可访问）
- Test: 组件逻辑 Vitest（`src/views/admin/__tests__/KefuManager.faq.test.ts`）+ Playwright 手测

**Interfaces:**
- Consumes: 管理 API（Task 3）。
- Produces: `src/api/kefu.ts` 导出 `listInstances/listFaq/createFaq/updateFaq/deleteFaq/reorderFaq`。

- [ ] **Step 1: 写 `src/api/kefu.ts`**

```ts
import { get, post, patch, del } from '@/utils/request'

export interface KefuInstance { id: string; slug: string; name: string; enabled: boolean }
export interface KefuFaq {
  id: string; instance_id: string; question: string; answer: string
  category: string | null; sort_order: number; click_count: number; enabled: boolean
}

export function listInstances() { return get<{ instances: KefuInstance[] }>('/admin/kefu/instances') }
export function listFaq(iid: string) { return get<{ items: KefuFaq[] }>(`/admin/kefu/instances/${iid}/faq`) }
export function createFaq(iid: string, data: Partial<KefuFaq>) { return post<KefuFaq>(`/admin/kefu/instances/${iid}/faq`, data) }
export function updateFaq(iid: string, fid: string, data: Partial<KefuFaq>) { return patch<KefuFaq>(`/admin/kefu/instances/${iid}/faq/${fid}`, data) }
export function deleteFaq(iid: string, fid: string) { return del(`/admin/kefu/instances/${iid}/faq/${fid}`) }
export function reorderFaq(iid: string, order: string[]) { return patch(`/admin/kefu/instances/${iid}/faq/reorder`, { order }) }
```

> `@/utils/request` 已导出 `patch`（`src/utils/request.ts:180`），与后端 PATCH 端点对齐，直接用即可。

- [ ] **Step 2: 写 `KefuManager.vue`**（实例选择 + 热问表格 + 编辑弹窗 + 拖拽排序）

```vue
<template>
  <div class="kefu-manager">
    <el-page-header content="智能客服 · 热门问题" />
    <el-select v-model="activeIid" placeholder="选择客服实例" @change="loadFaq" style="width:280px;margin:12px 0">
      <el-option v-for="i in instances" :key="i.id" :label="i.name" :value="i.id" />
    </el-select>
    <el-button type="primary" :disabled="!activeIid" @click="openCreate">新增热问</el-button>
    <el-table :data="faqs" row-key="id" style="margin-top:12px">
      <el-table-column label="排序" width="70">
        <template #default="{ $index }">
          <el-button link :disabled="$index===0" @click="move($index,-1)">↑</el-button>
          <el-button link :disabled="$index===faqs.length-1" @click="move($index,1)">↓</el-button>
        </template>
      </el-table-column>
      <el-table-column prop="question" label="问题" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="120" />
      <el-table-column prop="click_count" label="点击量" width="90" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="v => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editing?.id ? '编辑热问' : '新增热问'" width="720px">
      <el-form label-width="72px">
        <el-form-item label="问题"><el-input v-model="form.question" /></el-form-item>
        <el-form-item label="分类"><el-input v-model="form.category" placeholder="可选标签" /></el-form-item>
        <el-form-item label="答案">
          <MdEditor v-model="form.answer" style="height:320px" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog=false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import * as api from '@/api/kefu'
import type { KefuFaq } from '@/api/kefu'

const instances = ref<any[]>([])
const activeIid = ref('')
const faqs = ref<KefuFaq[]>([])
const dialog = ref(false)
const editing = ref<KefuFaq | null>(null)
const form = reactive({ question: '', answer: '', category: '', enabled: true })

async function loadInstances() { instances.value = (await api.listInstances()).instances }
async function loadFaq() { if (activeIid.value) faqs.value = (await api.listFaq(activeIid.value)).items }

function openCreate() { editing.value = null; Object.assign(form, { question:'', answer:'', category:'', enabled:true }); dialog.value = true }
function openEdit(row: KefuFaq) { editing.value = row; Object.assign(form, { question: row.question, answer: row.answer, category: row.category || '', enabled: row.enabled }); dialog.value = true }

async function save() {
  if (!form.question.trim() || !form.answer.trim()) { ElMessage.warning('问题与答案必填'); return }
  if (editing.value) await api.updateFaq(activeIid.value, editing.value.id, { ...form })
  else await api.createFaq(activeIid.value, { ...form })
  dialog.value = false; await loadFaq(); ElMessage.success('已保存')
}
async function remove(row: KefuFaq) { await api.deleteFaq(activeIid.value, row.id); await loadFaq() }
async function toggle(row: KefuFaq, v: boolean) { await api.updateFaq(activeIid.value, row.id, { enabled: v }); await loadFaq() }
async function move(idx: number, dir: number) {
  const arr = [...faqs.value]; const j = idx + dir
  ;[arr[idx], arr[j]] = [arr[j], arr[idx]]
  faqs.value = arr
  await api.reorderFaq(activeIid.value, arr.map(f => f.id))
}

onMounted(loadInstances)
</script>
```

- [ ] **Step 3: 注册路由** — 在 `src/router/index.ts` 认证布局的 children 中加（参照现有 admin 路由写法；`meta` 可带 `permission: 'admin.kefu'` 若项目有对应守卫，否则仅登录即可，页面内已按 API 403 兜底）：

```ts
{ path: 'admin/kefu', name: 'KefuManager', component: () => import('@/views/admin/KefuManager.vue'), meta: { title: '智能客服' } },
```

- [ ] **Step 4: 写 Vitest 逻辑测试** `src/views/admin/__tests__/KefuManager.faq.test.ts` — 覆盖 `move()` 生成的 reorder 顺序与 `save()` 的必填校验（mock `@/api/kefu`）。示例：

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
vi.mock('@/api/kefu')
import * as api from '@/api/kefu'
// 挂载组件、设置 faqs、调用 move(0,1)，断言 api.reorderFaq 收到反转后的 id 顺序。
// （按项目现有组件测试写法：mountWith stubs；见 CLAUDE.md 测试模式）
```

（按仓库前端测试范式补全断言：`move` 后 `api.reorderFaq` 以交换后的 id 数组被调用；`save` 空 answer 时不调用 `createFaq`。）

- [ ] **Step 5: 运行前端测试 + 类型检查**

Run: `npx vitest run src/views/admin/__tests__/KefuManager.faq.test.ts` → PASS
Run: `npm run build`（vue-tsc 类型检查通过）

- [ ] **Step 6: Playwright 手测（必做）** — 起 `npm run dev:all`（或后端已在跑 + `npm run dev`），登录 admin/admin123，进入 `/admin/kefu`：选实例 → 新增热问（Markdown 答案）→ 保存 → 列表出现 → 编辑 → 上下移动排序（刷新后顺序保持）→ 删除。截图存 `.playwright-mcp/kefu-faq-admin.png`，并 DB 交叉核对 `kefu_faq_items` 行与 `sort_order`。

- [ ] **Step 7: 提交**

```bash
git add src/api/kefu.ts src/views/admin/KefuManager.vue src/router/index.ts src/views/admin/__tests__/KefuManager.faq.test.ts
git commit -m "feat(kefu): 管理端热问编辑器 KefuManager.vue"
```

---

### Task 6: 文档

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

- [ ] **Step 1:** 在文档中新增「热门问题 / 自助服务面板（Stage ①：配置）」小节：如何在 `/admin/kefu` 选实例、增删改热问、写 Markdown 答案、设分类标签、调排序、看点击量；说明公开端点 `GET /kefu/i/<slug>/faq` 与点击 `POST /kefu/i/<slug>/faq/<id>/click`；注明访客自助面板 UI 属 Stage ②。

- [ ] **Step 2: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 热问配置（Stage ①）使用文档"
```

---

## Self-Review

**Spec coverage（对照 design §3–§6, §9, §10）：**
- §3 表 → Task 1 ✓；§4 公开 API（列表/点击，独立限速桶）→ Task 4 ✓；§5 管理 API（CRUD+reorder）→ Task 3 ✓；§6 管理编辑器 → Task 5 ✓；§9 测试（后端 pytest + 前端 + Playwright + 文档）→ Task 1-6 ✓；§10 阶段① 范围 → 本计划 ✓。
- 访客页/自助抽屉/转 AI（§7、§10 阶段②）**不在本计划**，属 Stage ②（下一个计划）。

**Placeholder scan:** 无 TBD/TODO。`@/utils/request` 的 `patch` 已确认存在（`request.ts:180`）。路由注册写法留给实现者对齐既有 admin 路由（非占位）。Task 5 Step 4 的 Vitest 用例给了断言目标（reorder 顺序、必填不提交），实现者按仓库前端测试范式补全 mount 细节。

**Type consistency:** `KefuFaq` 字段（id/instance_id/question/answer/category/sort_order/click_count/enabled）在 repo `_row_to_faq`、admin 返回、`src/api/kefu.ts` 一致；`increment_faq_click(instance_id, faq_id)` 与 public `faq_click` 调用一致；`reorder_faq(instance_id, id_list)` 与 admin `reorder`/前端 `reorderFaq` 一致；管理端点均 PATCH（前端需 `patch`）。

---

## 后续

- **Stage ②（访客页 + 自助抽屉 + 转 AI）**：`/kefu/:slug` 全页、`src/api/kefuPublic.ts`（visitor_id、无 JWT）、`KefuSelfServicePanel.vue`（平铺+标签筛选+内联展开 MdPreview+点击埋点+转 AI）、访客 Playwright 全流程。落地后另出计划。
