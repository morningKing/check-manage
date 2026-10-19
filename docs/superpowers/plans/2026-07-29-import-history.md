# 导入历史（持久化 + 跨会话重试/导出）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据页"导入数据"的每次上传结果（成功/失败统计 + 失败明细）持久化到后端，关闭进度弹窗、刷新页面、换设备都不丢失；新增"导入历史"入口可以回看任意一次历史记录，并直接对历史失败记录重试/导出；会话内弹窗的重试和历史页的重试实时互相同步。

**Architecture:** 新增两张表 `import_runs`/`import_run_failures` + 一个新蓝图 `server/routes/import_runs.py`（4 个端点：创建、列表、详情、重试同步）。前端新增 `src/api/importRuns.ts` 封装这 4 个端点，`src/utils/importPageRecords.ts` 的 `ImportPageResult` 类型新增可选 `runId` 字段 + 一个纯函数 `diffRetryResult`。`DynamicPage.vue`（已有的多文件导入流程）在每个文件上传完成后落库一次、在会话内重试后同步一次；新增 `ImportHistoryDialog.vue` 组件承载"导入历史"列表 + 详情 + 重试/导出。**不改动** `src/composables/useMultiFileImport.ts`（已上线、已审查）。

**Tech Stack:** Flask + psycopg2（后端），Vue 3 + TypeScript + Element Plus（前端），pytest + Vitest + Playwright。

## Global Constraints

- **不改动** `src/composables/useMultiFileImport.ts`——所有新逻辑放在
  `DynamicPage.vue`（它本来就能直接读 `multiImport.state`）和新文件里。
- **不改动** `importPageRecords()`/`retryImportFailures()` 的内部逻辑——只给
  `ImportPageResult` 类型加一个可选字段，函数行为完全不变（`useBatchImport.ts`
  这个不相关的调用方不受影响）。
- **`POST /importRuns` 和会话内重试触发的 `POST /importRuns/<id>/retry-result`
  必须是 best-effort**：`try/catch`，失败只 `console.warn`，绝不影响导入/
  重试本身在弹窗里的正常表现。历史页自己发起的操作（列表/详情加载、历史页
  里点的重试）走正常的 `ElMessage.error` 错误提示路径。
- **`status` 只有两个值**：`failed_count === 0 ? 'success' : 'partial'`，
  前后端口径一致（跟 `FileImportEntry.status` 判定逻辑同一套语义）。
- **`import_run_failures.record_id` = `payload.id`**，是重试后定位"这条解决
  了"的唯一锚点，前后端所有匹配逻辑都用这个字段，不用数组下标。
- **权限**：所有 `/importRuns*` 路由用 `require_page_action(collection, action)`
  （跟 `dynamic.py` 自己的路由一致），不是 webhook 那种全局 `admin.*` 权限。
- **`RESERVED`**（`server/routes/dynamic.py`）新增 `'importRuns'`。
- **蓝图注册顺序**：`import_runs_bp` 在 `app.py` 里必须注册在 `dynamic_bp`
  之前（`dynamic_bp` 的 catch-all 路由必须最后注册，这是现有规则）。
- CLAUDE.md 强制项：本次是 UI + 前后端交互变更，完成后必须 Playwright 现场
  验证；必须同步更新用户手册（`docs/user-guide/data/import-export.md`，已
  存在，本次追加"导入历史"一节）。

---

### Task 1: 后端 — 表结构 + `import_runs` 蓝图

**Files:**
- Modify: `server/init_db.py`
- Modify: `server/routes/dynamic.py`（`RESERVED` 集合）
- Create: `server/routes/import_runs.py`
- Modify: `server/app.py`
- Create: `server/tests/test_routes_import_runs.py`

**Interfaces:**
- Produces（Task 2/3 依赖，通过 HTTP 而非直接 import）：
  ```
  POST   /importRuns                       -> 201 {"id": str}
  GET    /importRuns?pageId=&collection=&limit=&offset=
                                            -> 200 {"runs": [...], "total": int}
  GET    /importRuns/<id>                  -> 200 {"run": {...}, "failures": [...]}
                                               404 {"error": str}
  POST   /importRuns/<id>/retry-result     -> 200 {"successCount","createdCount",
                                                    "updatedCount","failedCount","status"}
                                               404 {"error": str}
  ```
  Run 对象字段：`id, pageId, collection, branchId, fileName, successCount,
  createdCount, updatedCount, failedCount, status ('success'|'partial'),
  operator, createdAt`。
  Failure 对象字段：`recordId, originalRecord, payload, reason, createdAt`。

- [ ] **Step 1: 建表（DDL）**

`server/init_db.py` 里 `DDL = """ ... """` 字符串（第 12-535 行）的结尾，在
`"""`（第 535 行）之前追加一段（紧接在"工作流引擎表"那一段之后）：

```sql

-- ==================== 导入历史表 ====================

CREATE TABLE IF NOT EXISTS import_runs (
    id              VARCHAR(100) PRIMARY KEY,
    page_id         VARCHAR(100) NOT NULL,
    collection      VARCHAR(200) NOT NULL,
    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
    file_name       VARCHAR(500) NOT NULL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    created_count   INTEGER NOT NULL DEFAULT 0,
    updated_count   INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'success',
    operator        VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_runs_page_created ON import_runs(page_id, created_at DESC);

CREATE TABLE IF NOT EXISTS import_run_failures (
    id              VARCHAR(100) PRIMARY KEY,
    run_id          VARCHAR(100) NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    record_id       VARCHAR(200) NOT NULL,
    original_record JSONB NOT NULL,
    payload         JSONB NOT NULL,
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_run_failures_run_id ON import_run_failures(run_id);
```

（这个 DDL 块在 `init_db.py` 每次运行时都会执行，`CREATE TABLE IF NOT EXISTS`
天然幂等，不需要额外的迁移块——跟这个文件里绝大多数表的写法一致，只有极少
数"在已发布后才补的表"才需要 `information_schema` 判断的迁移块，本次是全新
表，不需要。）

在 `server/routes/dynamic.py` 的 `RESERVED` 集合（第 20 行）里加入
`'importRuns'`：

```python
RESERVED = {'menus', 'pageConfigs', 'favicon.ico', 'relations', 'auth', 'users', 'roles', 'operationLogs', 'backups', 'exportScripts', 'apiKeys', 'validationScripts', 'etlTasks', 'relation-graph', 'query', 'comments', 'timeline', 'dashboards', 'notifications', 'triggerRules', 'ai', 'versions', 'project-versions', 'webhook', 'dependencies', 'system-config', 'home-widgets', 'data-files', 'ai-scan-tasks', 'importRuns'}
```

- [ ] **Step 2: 新建路由文件**

Create `server/routes/import_runs.py`：

```python
"""
导入历史 API 路由

端点：
- POST   /importRuns                        创建一条导入历史记录（含失败明细）
- GET    /importRuns                        按数据页分页列出历史记录（不含失败明细）
- GET    /importRuns/<run_id>               单条记录详情（含失败明细）
- POST   /importRuns/<run_id>/retry-result  重试后同步：删除已解决的失败行，更新计数
"""
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required
from utils.rbac_guard import require_page_action
import psycopg2.extras
import uuid

import_runs_bp = Blueprint('import_runs', __name__)


def _run_to_dict(row):
    return {
        'id': row[0],
        'pageId': row[1],
        'collection': row[2],
        'branchId': row[3],
        'fileName': row[4],
        'successCount': row[5],
        'createdCount': row[6],
        'updatedCount': row[7],
        'failedCount': row[8],
        'status': row[9],
        'operator': row[10],
        'createdAt': row[11].isoformat() if row[11] else None,
    }


@import_runs_bp.route('/importRuns', methods=['POST'])
@login_required
def create_import_run():
    body = request.get_json(force=True)
    collection = body.get('collection', '')
    denied = require_page_action(collection, 'create')
    if denied:
        return denied

    try:
        username = g.current_user.get('username', '')
    except (AttributeError, KeyError):
        username = ''

    failed_count = int(body.get('failedCount', 0))
    status = 'success' if failed_count == 0 else 'partial'
    run_id = f'imprun-{uuid.uuid4().hex[:12]}'
    failures = body.get('failures') or []

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO import_runs (id, page_id, collection, branch_id, file_name, '
                'success_count, created_count, updated_count, failed_count, status, operator) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (run_id, body.get('pageId', ''), collection, body.get('branchId') or 'main',
                 body.get('fileName', ''), int(body.get('successCount', 0)),
                 int(body.get('createdCount', 0)), int(body.get('updatedCount', 0)),
                 failed_count, status, username)
            )
            if failures:
                values = [
                    (f'imprunf-{uuid.uuid4().hex[:12]}', run_id, f.get('recordId', ''),
                     psycopg2.extras.Json(f.get('originalRecord', {})),
                     psycopg2.extras.Json(f.get('payload', {})), f.get('reason', ''))
                    for f in failures
                ]
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO import_run_failures (id, run_id, record_id, original_record, payload, reason) VALUES %s',
                    values
                )
            conn.commit()
        return jsonify({'id': run_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns', methods=['GET'])
@login_required
def list_import_runs():
    page_id = request.args.get('pageId', '')
    collection = request.args.get('collection', '')
    denied = require_page_action(collection, 'read')
    if denied:
        return denied

    limit = min(int(request.args.get('limit', 20)), 100)
    offset = int(request.args.get('offset', 0))

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, page_id, collection, branch_id, file_name, success_count, '
                'created_count, updated_count, failed_count, status, operator, created_at '
                'FROM import_runs WHERE page_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s',
                (page_id, limit, offset)
            )
            rows = cur.fetchall()
            cur.execute('SELECT COUNT(*) FROM import_runs WHERE page_id = %s', (page_id,))
            total = cur.fetchone()[0]
        return jsonify({'runs': [_run_to_dict(r) for r in rows], 'total': total})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns/<run_id>', methods=['GET'])
@login_required
def get_import_run_detail(run_id):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, page_id, collection, branch_id, file_name, success_count, '
                'created_count, updated_count, failed_count, status, operator, created_at '
                'FROM import_runs WHERE id = %s',
                (run_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '记录不存在'}), 404

            denied = require_page_action(row[2], 'read')
            if denied:
                return denied

            cur.execute(
                'SELECT record_id, original_record, payload, reason, created_at '
                'FROM import_run_failures WHERE run_id = %s ORDER BY created_at',
                (run_id,)
            )
            failure_rows = cur.fetchall()
        return jsonify({
            'run': _run_to_dict(row),
            'failures': [
                {
                    'recordId': fr[0], 'originalRecord': fr[1], 'payload': fr[2],
                    'reason': fr[3], 'createdAt': fr[4].isoformat() if fr[4] else None,
                }
                for fr in failure_rows
            ],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@import_runs_bp.route('/importRuns/<run_id>/retry-result', methods=['POST'])
@login_required
def sync_retry_result(run_id):
    body = request.get_json(force=True)
    resolved_ids = body.get('resolvedRecordIds') or []
    success_delta = int(body.get('successDelta', 0))
    created_delta = int(body.get('createdDelta', 0))
    updated_delta = int(body.get('updatedDelta', 0))

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT collection FROM import_runs WHERE id = %s', (run_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '记录不存在'}), 404

            denied = require_page_action(row[0], 'create')
            if denied:
                return denied

            if resolved_ids:
                cur.execute(
                    'DELETE FROM import_run_failures WHERE run_id = %s AND record_id = ANY(%s)',
                    (run_id, resolved_ids)
                )
                cur.execute(
                    'UPDATE import_runs SET '
                    'success_count = success_count + %s, created_count = created_count + %s, '
                    'updated_count = updated_count + %s, '
                    'failed_count = GREATEST(failed_count - %s, 0), '
                    "status = CASE WHEN failed_count - %s <= 0 THEN 'success' ELSE 'partial' END "
                    'WHERE id = %s',
                    (success_delta, created_delta, updated_delta, len(resolved_ids), len(resolved_ids), run_id)
                )
                conn.commit()

            cur.execute(
                'SELECT success_count, created_count, updated_count, failed_count, status '
                'FROM import_runs WHERE id = %s',
                (run_id,)
            )
            updated = cur.fetchone()
        return jsonify({
            'successCount': updated[0], 'createdCount': updated[1],
            'updatedCount': updated[2], 'failedCount': updated[3], 'status': updated[4],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 3: 注册蓝图**

`server/app.py`：在其它蓝图 import 语句附近加入
`from routes.import_runs import import_runs_bp`；在
`app.register_blueprint(dynamic_bp)`（第 96 行）之前加入
`app.register_blueprint(import_runs_bp)`（放在哪个已有蓝图之后都可以，
只要在 `dynamic_bp` 之前即可，比如紧挨着 `webhook_bp` 之后）。

- [ ] **Step 4: 写测试**

Create `server/tests/test_routes_import_runs.py`：

```python
"""
导入历史 API 路由测试

真实 DB 集成测试（不 mock 游标）：验证 import_runs/import_run_failures 的
写入、分页列表、详情、重试同步计数逻辑。测试数据用 `_t_` 前缀自建自清理。
admin token 是 superuser，绕过 require_page_action 的具体权限查询，不需要
额外 seed page_configs/角色权限——跟 test_dynamic_batch_pk_check.py 的
route_client 集成测试模式一致。
"""
import sys
import os

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import DB_CONFIG  # noqa: E402
import db as _db_module  # noqa: E402
from auth import create_token  # noqa: E402

COLLECTION = '_t_import_runs_coll'
PAGE_ID = f'page-{COLLECTION}'


@pytest.fixture
def client():
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.') or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _admin_headers():
    token = create_token({'id': 'admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


def _cleanup():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM import_runs WHERE collection = %s", (COLLECTION,))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _around_each():
    _cleanup()
    yield
    _cleanup()


def _create_run(client, **overrides):
    body = {
        'pageId': PAGE_ID, 'collection': COLLECTION, 'branchId': 'main', 'fileName': 'a.xlsx',
        'successCount': 8, 'createdCount': 8, 'updatedCount': 0, 'failedCount': 2,
        'failures': [
            {'recordId': 'rec-1', 'originalRecord': {'n': 1}, 'payload': {'id': 'rec-1', 'data': {'n': 1}, 'relations': {}}, 'reason': '主键重复'},
            {'recordId': 'rec-2', 'originalRecord': {'n': 2}, 'payload': {'id': 'rec-2', 'data': {'n': 2}, 'relations': {}}, 'reason': '校验失败'},
        ],
    }
    body.update(overrides)
    resp = client.post('/importRuns', json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()['id']


class TestCreateImportRun:
    def test_creates_run_and_failures_with_partial_status(self, client):
        run_id = _create_run(client)

        resp = client.get(f'/importRuns/{run_id}', headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['run']['status'] == 'partial'
        assert body['run']['failedCount'] == 2
        assert len(body['failures']) == 2
        assert {f['recordId'] for f in body['failures']} == {'rec-1', 'rec-2'}

    def test_zero_failures_gives_success_status(self, client):
        run_id = _create_run(client, failedCount=0, failures=[])

        resp = client.get(f'/importRuns/{run_id}', headers=_admin_headers())
        body = resp.get_json()
        assert body['run']['status'] == 'success'
        assert body['failures'] == []


class TestListImportRuns:
    def test_filters_by_page_and_orders_by_created_at_desc(self, client):
        run1 = _create_run(client, fileName='first.xlsx')
        run2 = _create_run(client, fileName='second.xlsx')

        resp = client.get(f'/importRuns?pageId={PAGE_ID}&collection={COLLECTION}', headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['total'] == 2
        assert [r['id'] for r in body['runs']] == [run2, run1]

    def test_pagination_limit_offset(self, client):
        for i in range(3):
            _create_run(client, fileName=f'f{i}.xlsx')

        resp = client.get(f'/importRuns?pageId={PAGE_ID}&collection={COLLECTION}&limit=2&offset=1', headers=_admin_headers())
        body = resp.get_json()
        assert body['total'] == 3
        assert len(body['runs']) == 2


class TestGetImportRunDetail:
    def test_unknown_id_returns_404(self, client):
        resp = client.get('/importRuns/does-not-exist', headers=_admin_headers())
        assert resp.status_code == 404


class TestSyncRetryResult:
    def test_resolved_failures_removed_and_counts_updated(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': ['rec-1'], 'successDelta': 1, 'createdDelta': 1, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['failedCount'] == 1
        assert body['successCount'] == 9
        assert body['createdCount'] == 9
        assert body['status'] == 'partial'

        detail = client.get(f'/importRuns/{run_id}', headers=_admin_headers()).get_json()
        assert {f['recordId'] for f in detail['failures']} == {'rec-2'}

    def test_resolving_all_failures_flips_status_to_success(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': ['rec-1', 'rec-2'], 'successDelta': 2, 'createdDelta': 2, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        body = resp.get_json()
        assert body['failedCount'] == 0
        assert body['status'] == 'success'

    def test_empty_resolved_ids_is_a_harmless_noop(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': [], 'successDelta': 0, 'createdDelta': 0, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['failedCount'] == 2
        assert body['status'] == 'partial'

    def test_unknown_run_id_returns_404(self, client):
        resp = client.post(
            '/importRuns/does-not-exist/retry-result',
            json={'resolvedRecordIds': [], 'successDelta': 0, 'createdDelta': 0, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 404
```

- [ ] **Step 5: 跑数据库初始化 + 测试**

Run: `cd server && python init_db.py`（建表，幂等，重复跑无害）
Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_import_runs.py -v`
Expected: 全部通过（10 个测试）。

- [ ] **Step 6: Commit**

```bash
git add server/init_db.py server/routes/dynamic.py server/routes/import_runs.py server/app.py server/tests/test_routes_import_runs.py
git commit -m "feat(import-history): 新增导入历史表 + import_runs API"
```

---

### Task 2: 前端 — API 封装 + `diffRetryResult`

**Files:**
- Create: `src/api/importRuns.ts`
- Modify: `src/utils/importPageRecords.ts`
- Modify: `src/utils/__tests__/importPageRecords.test.ts`

**Interfaces:**
- Consumes: Task 1 的 4 个 `/importRuns*` 端点
- Produces（Task 3 依赖）：
  ```typescript
  // src/api/importRuns.ts
  export interface ImportRunSummary { id, fileName, branchId, successCount,
    createdCount, updatedCount, failedCount, status: 'success'|'partial',
    operator, createdAt: string }
  export interface ImportRunFailureRow { recordId, originalRecord,
    payload: {id,data,relations}, reason, createdAt: string }
  export function createImportRun(body): Promise<{ id: string }>
  export function listImportRuns(pageId, collection, limit?, offset?):
    Promise<{ runs: ImportRunSummary[]; total: number }>
  export function getImportRunDetail(id): Promise<{ run: ImportRunSummary; failures: ImportRunFailureRow[] }>
  export function syncImportRunRetryResult(id, body): Promise<{successCount,createdCount,updatedCount,failedCount,status}>

  // src/utils/importPageRecords.ts
  export interface ImportPageResult { ...已有字段..., runId?: string | null }
  export function diffRetryResult(before: ImportPageResult, after: ImportPageResult):
    { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number }
  ```

- [ ] **Step 1: 新建 API 封装**

Create `src/api/importRuns.ts`：

```typescript
import { get, post } from '@/utils/request'

export interface ImportRunSummary {
  id: string
  fileName: string
  branchId: string
  successCount: number
  createdCount: number
  updatedCount: number
  failedCount: number
  status: 'success' | 'partial'
  operator: string
  createdAt: string
}

export interface ImportRunFailureRow {
  recordId: string
  originalRecord: Record<string, any>
  payload: { id: string; data: any; relations: Record<string, string[]> }
  reason: string
  createdAt: string
}

export function createImportRun(body: {
  pageId: string
  collection: string
  branchId: string
  fileName: string
  successCount: number
  createdCount: number
  updatedCount: number
  failedCount: number
  failures: Array<{ recordId: string; originalRecord: any; payload: any; reason: string }>
}): Promise<{ id: string }> {
  return post('/importRuns', body)
}

export function listImportRuns(
  pageId: string,
  collection: string,
  limit = 20,
  offset = 0,
): Promise<{ runs: ImportRunSummary[]; total: number }> {
  return get('/importRuns', { pageId, collection, limit, offset })
}

export function getImportRunDetail(
  id: string,
): Promise<{ run: ImportRunSummary; failures: ImportRunFailureRow[] }> {
  return get(`/importRuns/${id}`)
}

export function syncImportRunRetryResult(
  id: string,
  body: { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number },
): Promise<{ successCount: number; createdCount: number; updatedCount: number; failedCount: number; status: string }> {
  return post(`/importRuns/${id}/retry-result`, body)
}
```

- [ ] **Step 2: Write the failing tests for `diffRetryResult`**

在 `src/utils/__tests__/importPageRecords.test.ts` 末尾追加：

```typescript
describe('diffRetryResult', () => {
  function failure(id: string): ImportFailure {
    return { originalRecord: { id }, payload: { id, data: {}, relations: {} }, reason: 'x' }
  }
  function result(overrides: Partial<ImportPageResult> = {}): ImportPageResult {
    return { success: 0, failed: 0, created: 0, updated: 0, failures: [], ...overrides }
  }

  it('computes resolved ids as those present before but absent from after', () => {
    const before = result({ success: 5, created: 5, failed: 3, failures: [failure('a'), failure('b'), failure('c')] })
    const after = result({ success: 7, created: 7, failed: 1, failures: [failure('c')] })

    const diff = diffRetryResult(before, after)

    expect(diff.resolvedRecordIds.sort()).toEqual(['a', 'b'])
    expect(diff.successDelta).toBe(2)
    expect(diff.createdDelta).toBe(2)
    expect(diff.updatedDelta).toBe(0)
  })

  it('returns an empty resolved list when nothing changed', () => {
    const before = result({ success: 5, failed: 1, failures: [failure('a')] })
    const after = result({ success: 5, failed: 1, failures: [failure('a')] })

    const diff = diffRetryResult(before, after)

    expect(diff.resolvedRecordIds).toEqual([])
    expect(diff.successDelta).toBe(0)
  })

  it('handles all failures resolved', () => {
    const before = result({ success: 0, created: 0, updated: 3, failed: 2, failures: [failure('a'), failure('b')] })
    const after = result({ success: 3, created: 0, updated: 5, failed: 0, failures: [] })

    const diff = diffRetryResult(before, after)

    expect(diff.resolvedRecordIds.sort()).toEqual(['a', 'b'])
    expect(diff.successDelta).toBe(3)
    expect(diff.updatedDelta).toBe(2)
  })
})
```

Import `diffRetryResult` alongside the existing `importPageRecords`/`retryImportFailures` import at the top of the test file.

- [ ] **Step 3: Run tests to verify they fail**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: FAIL — `diffRetryResult is not a function` / import error.

- [ ] **Step 4: Implement**

`src/utils/importPageRecords.ts`：`ImportPageResult` 接口（当前第 9-15 行）
新增一个可选字段：

```typescript
export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
  failures: ImportFailure[]
  runId?: string | null   // 本次上传对应的导入历史记录 id；由调用方（DynamicPage.vue）
                           // 在 importPageRecords() 返回后另行 POST /importRuns 得到，
                           // importPageRecords() 自己永远不会设置这个字段
}
```

文件末尾追加：

```typescript
/**
 * 计算一次重试前后的差集：哪些失败记录这次解决了（不在新的 failures 里
 * 但在旧的里），以及本次重试贡献的成功/新增/更新增量——供调用方同步到
 * /importRuns/<id>/retry-result。before/after 是同一累计口径的两个
 * ImportPageResult 快照（比如 useMultiFileImport.retryFileFailures 合并
 * 前后的 entry.result，或者调用方自己按同样的合并公式构造的一对）。
 */
export function diffRetryResult(
  before: ImportPageResult,
  after: ImportPageResult,
): { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number } {
  const stillFailingIds = new Set(after.failures.map((f) => f.payload.id))
  const resolvedRecordIds = before.failures
    .map((f) => f.payload.id)
    .filter((id) => !stillFailingIds.has(id))
  return {
    resolvedRecordIds,
    successDelta: after.success - before.success,
    createdDelta: after.created - before.created,
    updatedDelta: after.updated - before.updated,
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: 全部通过（既有测试 + 3 个新测试）。

- [ ] **Step 6: Commit**

```bash
git add src/api/importRuns.ts src/utils/importPageRecords.ts src/utils/__tests__/importPageRecords.test.ts
git commit -m "feat(import-history): 新增 importRuns API 封装与 diffRetryResult"
```

---

### Task 3: 前端 — `DynamicPage.vue` 接入 + `ImportHistoryDialog.vue` + 文档 + Playwright

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`
- Create: `src/components/common/ImportHistoryDialog.vue`
- Modify: `src/components/common/index.ts`
- Modify: `docs/user-guide/data/import-export.md`

**Interfaces:**
- Consumes: Task 2 的 `src/api/importRuns.ts` + `diffRetryResult`

#### Step 1: `uploadImportedRecords` 落库

找到 `uploadImportedRecords` 函数（`src/views/dynamic/DynamicPage.vue`，搜索
函数名定位，多文件导入功能引入的），改为：

```typescript
async function uploadImportedRecords(
  records: Record<string, any>[],
  onProgress: (current: number, total: number) => void,
) {
  resolvingActive.value = false
  resolvingProgress.value = { current: 0, total: 0 }
  const result = await importPageRecords({
    store: pageConfigStore,
    post,
    pageId: pageId.value,
    collection: collection.value,
    records,
    onProgress: (current, total) => {
      resolvingActive.value = false
      onProgress(current, total)
    },
    onResolveProgress: (current, total) => {
      resolvingActive.value = true
      resolvingProgress.value = { current, total }
    },
  })

  // 落库导入历史，best-effort：失败只警告，不影响导入本身的用户体验
  // （这是"锦上添花"的审计功能，不应该因为网络抖动让用户觉得导入失败了）。
  let runId: string | null = null
  try {
    const fileName = multiImport.state.fileResults[multiImport.state.currentFileIndex]?.file.name || ''
    const { id } = await createImportRun({
      pageId: pageId.value,
      collection: collection.value,
      branchId: currentBranch.value?.branchId || 'main',
      fileName,
      successCount: result.success,
      createdCount: result.created,
      updatedCount: result.updated,
      failedCount: result.failed,
      failures: result.failures.map((f) => ({
        recordId: f.payload.id,
        originalRecord: f.originalRecord,
        payload: f.payload,
        reason: f.reason,
      })),
    })
    runId = id
  } catch (e) {
    console.warn('导入历史记录写入失败（不影响本次导入结果）', e)
  }

  return { ...result, runId }
}
```

在文件顶部现有的 `import { importPageRecords, retryImportFailures, type ImportFailure } from '@/utils/importPageRecords'`
这一行改为（加入 `diffRetryResult`）：

```typescript
import { importPageRecords, retryImportFailures, diffRetryResult, type ImportFailure } from '@/utils/importPageRecords'
```

并新增一行 import：

```typescript
import { createImportRun, syncImportRunRetryResult } from '@/api/importRuns'
```

#### Step 2: `handleRetryFileFailures` 同步

找到 `handleRetryFileFailures` 函数，改为：

```typescript
/**
 * 重试某个文件仍失败的记录；有新增写入则刷新表格；成功解决的失败记录
 * 同步到导入历史（best-effort，失败不影响本次重试在弹窗里的表现）。
 */
async function handleRetryFileFailures(index: number): Promise<void> {
  const entry = multiImport.state.fileResults[index]
  const before = entry.result
  const runId = entry.result?.runId

  await multiImport.retryFileFailures(index)

  if (entry.result && entry.result.created + entry.result.updated > 0) await loadPageData()

  if (runId && before && entry.result) {
    const diff = diffRetryResult(before, entry.result)
    if (diff.resolvedRecordIds.length > 0) {
      try {
        await syncImportRunRetryResult(runId, diff)
      } catch (e) {
        console.warn('导入历史同步失败（本次重试结果不受影响）', e)
      }
    }
  }
}
```

#### Step 3: "导入历史" 菜单入口 + 弹窗状态

在"更多"下拉菜单模板里（搜索 `command="template"` 定位），紧接着
`<el-dropdown-item v-if="canCreate" command="template" :icon="Download">下载导入模板</el-dropdown-item>`
之后新增：

```html
              <el-dropdown-item v-if="canCreate" command="importHistory" :icon="Document">导入历史</el-dropdown-item>
```

`handleMoreCommand` 函数里，在 `else if (command === 'template') { ... }` 分支
之后新增：

```typescript
  } else if (command === 'importHistory') {
    importHistoryDialogVisible.value = true
```

在 `<script setup>` 里跟 `projectVersionManagerVisible` 相邻的位置新增：

```typescript
const importHistoryDialogVisible = ref(false)
```

在模板里 `<ProjectVersionManager ... />` 组件标签附近新增：

```html
    <ImportHistoryDialog
      v-model="importHistoryDialogVisible"
      :page-id="pageId"
      :collection="collection"
      :fields="pageFields"
    />
```

顶部组件 import 那一行（`import { DataTable, ConfirmDialog, ..., ProjectVersionManager, ... } from '@/components/common'`）
加入 `ImportHistoryDialog`。

#### Step 4: 新建 `ImportHistoryDialog.vue`

Create `src/components/common/ImportHistoryDialog.vue`：

```vue
<template>
  <el-dialog v-model="visible" title="导入历史" width="900px" :close-on-click-modal="false" destroy-on-close>
    <el-table v-loading="loading" :data="runs" stripe style="width: 100%">
      <el-table-column prop="fileName" label="文件名" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'warning'" size="small">
            {{ row.status === 'success' ? '成功' : '部分失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="成功 / 失败" width="140">
        <template #default="{ row }">
          {{ row.successCount }} / {{ row.failedCount }}
        </template>
      </el-table-column>
      <el-table-column prop="operator" label="操作人" width="120" />
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ formatDateTime(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link @click="showDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="currentPage"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next"
      style="margin-top: 12px; justify-content: flex-end"
      @current-change="fetchRuns"
    />

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="导入详情" width="700px" :close-on-click-modal="false">
    <div v-if="detailRun" class="import-history-detail-summary">
      <p>{{ detailRun.fileName }} — 成功 {{ detailRun.successCount }} 条，失败 {{ detailRun.failedCount }} 条</p>
    </div>
    <el-table v-loading="detailLoading" :data="detailFailures" size="small" max-height="360">
      <el-table-column label="原始数据" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ JSON.stringify(row.originalRecord) }}</template>
      </el-table-column>
      <el-table-column prop="reason" label="失败原因" min-width="160" show-overflow-tooltip />
    </el-table>
    <template #footer>
      <el-button v-if="detailFailures.length > 0" @click="handleExportDetailFailures">导出失败清单</el-button>
      <el-button
        v-if="detailFailures.length > 0"
        type="primary"
        :loading="retrying"
        @click="handleRetryDetailFailures"
      >
        重试失败记录
      </el-button>
      <el-button type="primary" @click="detailVisible = false">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FieldConfig } from '@/types'
import {
  listImportRuns, getImportRunDetail, syncImportRunRetryResult,
  type ImportRunSummary, type ImportRunFailureRow,
} from '@/api/importRuns'
import {
  retryImportFailures, diffRetryResult, type ImportFailure, type ImportPageResult,
} from '@/utils/importPageRecords'
import { exportImportFailures } from '@/utils/excel'
import { post } from '@/utils/request'

const props = defineProps<{
  modelValue: boolean
  pageId: string
  collection: string
  fields: FieldConfig[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const loading = ref(false)
const runs = ref<ImportRunSummary[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20

async function fetchRuns(): Promise<void> {
  loading.value = true
  try {
    const resp = await listImportRuns(props.pageId, props.collection, pageSize, (currentPage.value - 1) * pageSize)
    runs.value = resp.runs
    total.value = resp.total
  } catch {
    ElMessage.error('加载导入历史失败')
  } finally {
    loading.value = false
  }
}

watch(visible, (val) => {
  if (val) {
    currentPage.value = 1
    fetchRuns()
  }
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailRun = ref<ImportRunSummary | null>(null)
const detailFailures = ref<ImportRunFailureRow[]>([])
const retrying = ref(false)

async function showDetail(row: ImportRunSummary): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    const resp = await getImportRunDetail(row.id)
    detailRun.value = resp.run
    detailFailures.value = resp.failures
  } catch {
    ElMessage.error('加载导入详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

function toImportFailures(rows: ImportRunFailureRow[]): ImportFailure[] {
  return rows.map((r) => ({ originalRecord: r.originalRecord, payload: r.payload, reason: r.reason }))
}

function handleExportDetailFailures(): void {
  if (!detailRun.value) return
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const baseName = detailRun.value.fileName.replace(/\.[^.]+$/, '')
  exportImportFailures(toImportFailures(detailFailures.value), props.fields, `导入失败记录_${baseName}_${ts}`)
}

async function handleRetryDetailFailures(): Promise<void> {
  if (!detailRun.value) return
  const run = detailRun.value
  const before: ImportPageResult = {
    success: run.successCount, created: run.createdCount, updated: run.updatedCount,
    failed: run.failedCount, failures: toImportFailures(detailFailures.value),
  }

  retrying.value = true
  try {
    const retryResult = await retryImportFailures(post, props.collection, before.failures, () => {})
    const after: ImportPageResult = {
      success: before.success + retryResult.success,
      created: before.created + retryResult.created,
      updated: before.updated + retryResult.updated,
      failed: retryResult.failed,
      failures: retryResult.failures,
    }
    const diff = diffRetryResult(before, after)

    if (diff.resolvedRecordIds.length > 0) {
      const synced = await syncImportRunRetryResult(run.id, diff)
      detailRun.value = { ...run, ...synced } as ImportRunSummary
      detailFailures.value = detailFailures.value.filter(
        (f) => !diff.resolvedRecordIds.includes(f.recordId),
      )
      const idx = runs.value.findIndex((r) => r.id === run.id)
      if (idx !== -1) runs.value[idx] = { ...runs.value[idx], ...synced } as ImportRunSummary
      ElMessage.success(`重试完成：解决 ${diff.resolvedRecordIds.length} 条，仍失败 ${after.failed} 条`)
    } else {
      ElMessage.warning('本次重试没有记录成功')
    }
  } catch {
    ElMessage.error('重试失败，请稍后再试')
  } finally {
    retrying.value = false
  }
}

function formatDateTime(iso: string): string {
  if (!iso) return ''
  return iso.replace('T', ' ').replace(/\.\d+Z?$/, '').slice(0, 19)
}
</script>

<style scoped>
.import-history-detail-summary {
  margin-bottom: 12px;
  color: #606266;
}
</style>
```

`src/components/common/index.ts`：加入一行

```typescript
export { default as ImportHistoryDialog } from './ImportHistoryDialog.vue'
```

（放在 `ProjectVersionManager` 那一行附近即可，按字母序或就近插入都行，跟随
文件里已有的排列习惯。）

- [ ] **Step 5: 类型检查 + 现有测试**

Run: `npx vue-tsc --noEmit` → 期望 0 错误
Run: `npx vitest run src/views/dynamic/__tests__/ src/composables/__tests__/ src/utils/__tests__/importPageRecords.test.ts` → 期望全部通过

#### Step 6: 用户手册

`docs/user-guide/data/import-export.md`，在"导入数据"一节末尾（"下载导入
模板"那段之后，"导出数据"一节之前）追加：

```markdown
### 导入历史

「更多」→「导入历史」可以查看这个数据页所有历史导入记录（跨会话保存，
刷新页面、关闭浏览器都不会丢失），不再依赖导入完成时弹出的那个进度弹窗——
弹窗关掉之后，仍然可以随时通过「导入历史」回看任意一次导入的结果。

列表按时间倒序展示每次导入：文件名、状态、成功/失败数、操作人、时间。
点击「详情」可以看到失败记录的具体内容和原因，并可以「导出失败清单」或
直接「重试失败记录」——效果跟导入完成时那个进度弹窗里的重试/导出完全一样，
只是可以在任何时候操作，不受弹窗是否还开着的限制。

如果在弹窗还开着的时候就点了「重试失败记录」，「导入历史」里对应记录的
失败数会自动同步更新，两边看到的数字始终一致。
```

- [ ] **Step 7: Playwright 现场验证（CLAUDE.md 强制项）**

1. 确保前后端加载最新代码（后端新增了路由，需要重启；前端 Vite HMR 即可）。
2. 用 Playwright 触发一次真实导入（准备一个会产生部分失败的文件，比如主键
   冲突），完成后关闭弹窗。
3. 打开"更多"→"导入历史"，确认能看到刚才这次导入（文件名、成功/失败数
   正确）。
4. 点击"详情"，确认失败明细正确展示；点击"导出失败清单"，确认触发下载；
   点击"重试失败记录"，确认失败数下降、"成功"提示正确。
5. **刷新整个浏览器页面**（这是跨会话持久化的核心验证点），重新打开"导入
   历史"，确认记录还在、失败数是重试后的最新值（不是重试前的旧值）。
6. 再做一次新的导入（同样会产生失败），这次导入完成后**不关闭进度弹窗**，
   直接在弹窗里点"重试失败记录"；重试完成后再打开"导入历史"，确认对应记录
   的失败数已经是弹窗里重试后的最新值（验证会话内重试 → 历史记录的实时
   同步）。
7. 截图关键状态（历史列表、详情弹窗、重试后数字变化）保存证据。
8. 清理验证产生的测试数据（`dynamic_data` 里的测试记录 + `import_runs`/
   `import_run_failures` 里的测试记录）。

- [ ] **Step 8: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue src/components/common/ImportHistoryDialog.vue \
        src/components/common/index.ts docs/user-guide/data/import-export.md
git commit -m "feat(import-history): 导入历史入口 + 弹窗/历史页重试实时同步"
```
