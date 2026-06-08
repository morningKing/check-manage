# 项目级数据迁移（批量导入 / 批量清空）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「数据迁移」页（由现有「数据导出」页改造）新增按项目维度的批量 Excel 导入（自动按 `reference`/`quoteSelect` 引用关系拓扑排序后导入，收尾自动重解析）和批量清空数据页记录。

**Architecture:** 导入在前端编排，复用 `pageConfig` store 已有的解析/重解析逻辑与共享的逐页写库函数；拓扑排序为独立纯函数；清空走后端单事务批量 DELETE 接口。统一页面三 Tab。

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia（前端，Vitest 测试）；Flask + psycopg2（后端，Pytest 测试）。

设计文档：`docs/superpowers/specs/2026-06-08-project-data-migration-design.md`

---

## File Structure

新增：
- `server/` — 在 `utils/menu_export.py` 增 `batch_clear()`；`routes/menu_export.py` 增 `POST /menuExport/batchClear`
- `src/utils/referenceTopoSort.ts` — 纯函数：引用依赖拓扑排序
- `src/utils/importPageRecords.ts` — 从 `DynamicPage.doImport` 抽取的单页写库核心（无 Vue 响应式依赖）
- `src/composables/useBatchImport.ts` — 批量导入编排
- `src/views/admin/DataMigrationPage.vue` — 三 Tab 容器
- `src/views/admin/data-migration/ExportTab.vue` — 迁入现有导出逻辑
- `src/views/admin/data-migration/ImportTab.vue` — 批量导入
- `src/views/admin/data-migration/ClearTab.vue` — 批量清空
- 对应测试文件

修改：
- `src/api/menu.ts` — 新增 `batchClearCollections`
- `src/views/dynamic/DynamicPage.vue` — `doImport` 改为复用 `importPageRecords`
- `src/router/index.ts` — `menu-export` 路由指向 `DataMigrationPage`，title 改「数据迁移」（path 不变）

---

## Task 1: 后端批量清空（util + 路由 + 测试）

**Files:**
- Modify: `server/utils/menu_export.py`（文件末尾新增函数）
- Modify: `server/routes/menu_export.py`
- Test: `server/tests/test_routes_menu_export.py`

- [ ] **Step 1: Write the failing test**

在 `server/tests/test_routes_menu_export.py` 末尾追加（文件已有 `client` / `admin_headers` / `dev_headers` fixtures 来自 `conftest.py`；guest 用自定义 token）：

```python
import json
from auth import create_token


class TestBatchClear:
    def _post(self, client, headers, body):
        return client.post(
            '/menuExport/batchClear',
            data=json.dumps(body),
            content_type='application/json',
            headers=headers,
        )

    def test_empty_collections_returns_400(self, client, admin_headers):
        resp = self._post(client, admin_headers, {'collections': [], 'branchId': 'main'})
        assert resp.status_code == 400

    def test_guest_forbidden(self, client, mock_cursor):
        guest_token = create_token({'id': 'u-g', 'username': 'g', 'role': 'guest'})
        resp = self._post(
            client,
            {'Authorization': f'Bearer {guest_token}'},
            {'collections': ['orders'], 'branchId': 'main'},
        )
        assert resp.status_code == 403

    def test_clears_data_and_relations(self, client, admin_headers, mock_cursor):
        # dynamic_data DELETE rowcount=3, data_relations DELETE rowcount=2
        mock_cursor.rowcount = 3
        resp = self._post(
            client, admin_headers,
            {'collections': ['orders', 'products'], 'branchId': 'main'},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['totalDeleted'] >= 0
        # 验证执行了 dynamic_data 与 data_relations 两条 DELETE
        executed = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
        assert 'DELETE FROM dynamic_data' in executed
        assert 'DELETE FROM data_relations' in executed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_menu_export.py::TestBatchClear -v`
Expected: FAIL（404 路由不存在 / 函数未定义）

- [ ] **Step 3: Add `batch_clear` util**

在 `server/utils/menu_export.py` 末尾追加：

```python
def batch_clear(conn, collections, branch_id='main'):
    """
    清空多个 collection 在指定分支的全部记录，并清理悬挂的 M:N 关系。

    返回：{'perCollection': {coll: deleted_count}, 'totalDeleted': int, 'relationsDeleted': int}
    单事务：调用方负责 commit/rollback（get_db 上下文管理器已处理）。
    """
    cur = conn.cursor()
    per = {}
    total = 0
    for coll in collections:
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (coll, branch_id),
        )
        per[coll] = cur.rowcount
        total += cur.rowcount

    # 清理悬挂关系：被清集合作为 source 或 target 都删
    cur.execute(
        'DELETE FROM data_relations WHERE branch_id = %s '
        'AND (collection = ANY(%s) OR related_collection = ANY(%s))',
        (branch_id, list(collections), list(collections)),
    )
    relations_deleted = cur.rowcount

    return {'perCollection': per, 'totalDeleted': total, 'relationsDeleted': relations_deleted}
```

- [ ] **Step 4: Add route**

在 `server/routes/menu_export.py` 顶部 import 处补充，并新增路由。import 行改为：

```python
from utils.menu_export import execute_menu_export
from auth import login_required, write_required
from utils.operation_log import log_operation
```

在文件末尾新增：

```python
@menu_export_bp.route('/menuExport/batchClear', methods=['POST'])
@write_required
def batch_clear_collections():
    """批量清空多个数据页（collection）在指定分支的全部记录。"""
    from utils.menu_export import batch_clear

    body = request.get_json(force=True)
    collections = body.get('collections', [])
    branch_id = body.get('branchId', 'main')

    if not collections:
        return jsonify({'error': '未选择要清空的数据页'}), 400

    with get_db() as conn:
        result = batch_clear(conn, collections, branch_id)
        log_operation(
            'delete', 'dynamic_data', None, None,
            f"批量清空 {len(collections)} 个数据页，共 {result['totalDeleted']} 条记录"
            f"（分支 {branch_id}）",
            branch_id=branch_id,
        )

    return jsonify(result)
```

> 说明：`write_required` 已阻止 guest（返回 403）。`data_relations` 列名 `collection / related_collection / branch_id` 见 `init_db.py:49`。

- [ ] **Step 5: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_menu_export.py::TestBatchClear -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add server/utils/menu_export.py server/routes/menu_export.py server/tests/test_routes_menu_export.py
git commit -m "feat(data-migration): backend batchClear endpoint for project data pages"
```

---

## Task 2: 引用拓扑排序纯函数 + 单测

**Files:**
- Create: `src/utils/referenceTopoSort.ts`
- Test: `src/utils/__tests__/referenceTopoSort.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from 'vitest'
import { buildReferenceOrder } from '../referenceTopoSort'
import type { PageConfig } from '@/types'

function page(id: string, refs: Array<{ type: 'reference' | 'quoteSelect'; target: string }> = []): PageConfig {
  return {
    id,
    name: id,
    fields: refs.map((r, i) => ({
      id: `${id}-f${i}`,
      label: `f${i}`,
      fieldName: `f${i}`,
      controlType: r.type,
      required: false,
      order: i,
      ...(r.type === 'reference'
        ? { referenceConfig: { targetCollection: r.target, displayField: 'name', inheritFields: [] } }
        : { quoteConfig: { targetCollection: r.target, displayField: 'name' } }),
    })),
  } as unknown as PageConfig
}

describe('buildReferenceOrder', () => {
  it('orders referenced collection before the referencing one', () => {
    // B 引用 A → A 必须先导入
    const { order, cycles } = buildReferenceOrder([
      page('page-b', [{ type: 'reference', target: 'a' }]),
      page('page-a'),
    ])
    expect(cycles).toEqual([])
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('b'))
  })

  it('handles diamond dependencies', () => {
    // d 引用 b,c；b,c 引用 a → a 在最前，d 在最后
    const { order } = buildReferenceOrder([
      page('page-d', [{ type: 'reference', target: 'b' }, { type: 'quoteSelect', target: 'c' }]),
      page('page-b', [{ type: 'reference', target: 'a' }]),
      page('page-c', [{ type: 'reference', target: 'a' }]),
      page('page-a'),
    ])
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('b'))
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('c'))
    expect(order.indexOf('b')).toBeLessThan(order.indexOf('d'))
    expect(order.indexOf('c')).toBeLessThan(order.indexOf('d'))
  })

  it('ignores self-reference', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'a' }]),
    ])
    expect(cycles).toEqual([])
    expect(order).toEqual(['a'])
  })

  it('ignores references to collections outside the batch', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'external' }]),
    ])
    expect(cycles).toEqual([])
    expect(order).toEqual(['a'])
  })

  it('detects a cycle and still returns all nodes', () => {
    // A<->B 互相引用
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'b' }]),
      page('page-b', [{ type: 'reference', target: 'a' }]),
    ])
    expect(cycles.length).toBeGreaterThan(0)
    expect(order.sort()).toEqual(['a', 'b'])
  })

  it('returns empty for empty input', () => {
    expect(buildReferenceOrder([])).toEqual({ order: [], cycles: [] })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/utils/__tests__/referenceTopoSort.test.ts`
Expected: FAIL（Cannot find module '../referenceTopoSort'）

- [ ] **Step 3: Implement the pure function**

```typescript
import type { PageConfig } from '@/types'

export interface TopoResult {
  /** collection 导入顺序：被引用的排在引用方之前 */
  order: string[]
  /** 检测到的环（残留无法定序的 collection 分组）；无环为空数组 */
  cycles: string[][]
}

function collOf(cfg: PageConfig): string {
  return cfg.id.replace(/^page-/, '')
}

/**
 * 基于 reference / quoteSelect 字段的 targetCollection 构建依赖图并拓扑排序。
 * 边方向：target → page（target 必须先导入）。
 * 仅考虑目标也在入参集合内的引用；自引用与外部引用忽略。
 */
export function buildReferenceOrder(configs: PageConfig[]): TopoResult {
  const collections = configs.map(collOf)
  const collSet = new Set(collections)

  const dependents = new Map<string, Set<string>>() // node → 依赖它的页集合
  const indeg = new Map<string, number>()
  for (const c of collections) {
    dependents.set(c, new Set())
    indeg.set(c, 0)
  }

  for (const cfg of configs) {
    const coll = collOf(cfg)
    for (const f of cfg.fields || []) {
      let target: string | undefined
      if (f.controlType === 'reference') target = f.referenceConfig?.targetCollection
      else if (f.controlType === 'quoteSelect') target = f.quoteConfig?.targetCollection
      if (!target || target === coll || !collSet.has(target)) continue
      if (!dependents.get(target)!.has(coll)) {
        dependents.get(target)!.add(coll)
        indeg.set(coll, (indeg.get(coll) || 0) + 1)
      }
    }
  }

  // Kahn 算法，保持原始顺序的稳定性
  const queue = collections.filter((c) => (indeg.get(c) || 0) === 0)
  const order: string[] = []
  while (queue.length) {
    const n = queue.shift()!
    order.push(n)
    for (const m of dependents.get(n)!) {
      indeg.set(m, indeg.get(m)! - 1)
      if (indeg.get(m) === 0) queue.push(m)
    }
  }

  const cycles: string[][] = []
  if (order.length < collections.length) {
    const remaining = collections.filter((c) => !order.includes(c))
    cycles.push(remaining)
    for (const c of remaining) order.push(c) // 断边尽力：残留追加到末尾
  }

  return { order, cycles }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/utils/__tests__/referenceTopoSort.test.ts`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/utils/referenceTopoSort.ts src/utils/__tests__/referenceTopoSort.test.ts
git commit -m "feat(data-migration): reference dependency topo-sort pure function"
```

---

## Task 3: 抽取单页导入核心 `importPageRecords` + 让 DynamicPage 复用

**Files:**
- Create: `src/utils/importPageRecords.ts`
- Modify: `src/views/dynamic/DynamicPage.vue:2488-2608`
- Test: `src/utils/__tests__/importPageRecords.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi } from 'vitest'
import { importPageRecords } from '../importPageRecords'

function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    batchGenerateSequenceValues: vi.fn().mockReturnValue({}),
    stripRelationFields: vi.fn((_p: string, r: any) => ({ ...r })),
    getRelationFields: vi.fn().mockReturnValue([]),
    ...overrides,
  }
}

// post 通过依赖注入传入，便于断言
describe('importPageRecords', () => {
  it('resolves references then calls batch-create and returns counts', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({ success: true, created: 2, updated: 0, failed: 0 })
    const records = [{ name: 'a' }, { name: 'b' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(store.resolveReferenceImportValues).toHaveBeenCalledWith('page-orders', records, expect.any(Map))
    expect(post).toHaveBeenCalledWith('/orders/batch-create', expect.objectContaining({
      records: expect.any(Array),
    }))
    expect(result).toEqual({ success: 2, failed: 0, created: 2, updated: 0 })
  })

  it('counts batch failure without throwing', async () => {
    const store = makeStore()
    const post = vi.fn().mockRejectedValue(new Error('boom'))
    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }],
    })
    expect(result.failed).toBe(1)
    expect(result.success).toBe(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: FAIL（Cannot find module '../importPageRecords'）

- [ ] **Step 3: Implement `importPageRecords` (抽取自 doImport)**

```typescript
import { makeImportRowId } from '@/utils/importId'

export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
}

export interface ImportPageParams {
  store: any // pageConfig store（解析/序列/关联方法）
  post: <T>(url: string, body: any) => Promise<T> // 注入 @/utils/request 的 post
  pageId: string
  collection: string
  records: Record<string, any>[]
  onProgress?: (current: number, total: number) => void
}

const BATCH_SIZE = 500

/**
 * 单页批量导入核心：盖保序 id → 解析引用/关联 → 生成序列 → 分批 batch-create。
 * 不含任何 Vue 响应式依赖，供 DynamicPage 与批量导入编排共用。
 */
export async function importPageRecords(params: ImportPageParams): Promise<ImportPageResult> {
  const { store, post, pageId, collection, records, onProgress } = params
  const collectionCache = new Map<string, any[]>()

  records.forEach((r, i) => {
    if (!r._importId) r._importId = makeImportRowId(collection, i, records.length)
  })

  await Promise.all([
    store.resolveRelationImportValues(pageId, records, collectionCache),
    store.resolveReferenceImportValues(pageId, records, collectionCache),
    store.resolveQuoteImportValues(pageId, records, collectionCache),
    store.resolveCollectionSelectImportValues(pageId, records, collectionCache),
  ])

  const sequenceValues = store.batchGenerateSequenceValues(pageId, records.length)
  const sequenceFields = Object.keys(sequenceValues)

  let success = 0, failed = 0, created = 0, updated = 0
  const batches = Math.ceil(records.length / BATCH_SIZE)

  for (let batchIdx = 0; batchIdx < batches; batchIdx++) {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, records.length)
    const batchRecords = records.slice(start, end)

    try {
      const batchData = batchRecords.map((record, idx) => {
        const importId = record._importId as string | undefined
        const regularData = store.stripRelationFields(pageId, record)
        delete regularData._importId
        for (const fieldName of sequenceFields) {
          if (!regularData[fieldName]) regularData[fieldName] = sequenceValues[fieldName][start + idx]
        }
        const relations: Record<string, string[]> = {}
        for (const field of store.getRelationFields(pageId)) {
          const ids = record[field.fieldName]
          if (Array.isArray(ids) && ids.length > 0) relations[field.fieldName] = ids
        }
        return {
          id: importId || `${collection}-${Math.random().toString(36).slice(2, 10)}`,
          data: regularData,
          relations,
        }
      })

      const result = await post<{ created: number; updated?: number; failed: number }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
    } catch {
      failed += batchRecords.length
    }
    onProgress?.(end, records.length)
  }

  return { success, failed, created, updated }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: PASS（2 passed）

- [ ] **Step 5: Refactor DynamicPage.doImport to reuse it**

替换 `src/views/dynamic/DynamicPage.vue` 中 `doImport` 函数体（行 2488-2608）为：

```typescript
async function doImport(records: Record<string, any>[]): Promise<void> {
  importResult.value = null
  importLoading.value = true
  importProgress.value = 0
  importCurrent.value = 0
  importTotal.value = records.length
  importDialogVisible.value = true

  const { success, failed, created, updated } = await importPageRecords({
    store: pageConfigStore,
    post,
    pageId: pageId.value,
    collection: collection.value,
    records,
    onProgress: (current, total) => {
      importCurrent.value = current
      importProgress.value = Math.round((current / total) * 100)
    },
  })

  importLoading.value = false
  importResult.value = { success, failed, created, updated }
  if (success > 0) await loadPageData()
}
```

并在 `<script setup>` import 区新增：

```typescript
import { importPageRecords } from '@/utils/importPageRecords'
```

确认 `post` 已从 `@/utils/request` 导入（搜索文件内 `from '@/utils/request'`；若 `post` 未在列表则加入）。

- [ ] **Step 6: Run frontend tests + typecheck**

Run: `npx vitest run src/views/dynamic/__tests__/ && npx vue-tsc --noEmit`
Expected: 现有测试 PASS，类型检查无新增错误

- [ ] **Step 7: Commit**

```bash
git add src/utils/importPageRecords.ts src/utils/__tests__/importPageRecords.test.ts src/views/dynamic/DynamicPage.vue
git commit -m "refactor(import): extract importPageRecords core shared by single + batch import"
```

---

## Task 4: 批量导入编排 composable + 测试

**Files:**
- Create: `src/composables/useBatchImport.ts`
- Test: `src/composables/__tests__/useBatchImport.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi } from 'vitest'

vi.mock('@/utils/importPageRecords', () => ({
  importPageRecords: vi.fn(async ({ collection }: any) => ({
    success: 1, failed: 0, created: 1, updated: 0, _collection: collection,
  })),
}))

import { importPageRecords } from '@/utils/importPageRecords'
import { runBatchImport } from '../useBatchImport'
import type { PageConfig } from '@/types'

function page(id: string, target?: string): PageConfig {
  return {
    id, name: id,
    fields: target
      ? [{ id: 'f', label: 'f', fieldName: 'f', controlType: 'reference', required: false, order: 0,
          referenceConfig: { targetCollection: target, displayField: 'name', inheritFields: [] } }]
      : [],
  } as unknown as PageConfig
}

describe('runBatchImport', () => {
  it('imports pages in dependency order then re-resolves each', async () => {
    const store = {
      reResolveReferences: vi.fn().mockResolvedValue({ updated: 0, pending: 0 }),
    }
    const post = vi.fn()
    // b 引用 a → 应先导 a 再导 b
    const configs = [page('page-b', 'a'), page('page-a')]
    const pages = [
      { pageId: 'page-b', collection: 'b', records: [{ n: 1 }] },
      { pageId: 'page-a', collection: 'a', records: [{ n: 1 }] },
    ]

    const results = await runBatchImport({ store, post, pages, allConfigs: configs })

    const importedOrder = (importPageRecords as any).mock.calls.map((c: any[]) => c[0].collection)
    expect(importedOrder).toEqual(['a', 'b'])
    expect(store.reResolveReferences).toHaveBeenCalledWith('page-a')
    expect(store.reResolveReferences).toHaveBeenCalledWith('page-b')
    expect(results.map((r) => r.collection)).toEqual(['a', 'b'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/composables/__tests__/useBatchImport.test.ts`
Expected: FAIL（Cannot find module '../useBatchImport'）

- [ ] **Step 3: Implement orchestration**

```typescript
import type { PageConfig } from '@/types'
import { buildReferenceOrder } from '@/utils/referenceTopoSort'
import { importPageRecords, type ImportPageResult } from '@/utils/importPageRecords'

export interface BatchImportPageInput {
  pageId: string
  collection: string
  records: Record<string, any>[]
}

export interface BatchImportPageResult extends ImportPageResult {
  collection: string
  reResolved: number
  pending: number
}

export interface RunBatchImportParams {
  store: any
  post: <T>(url: string, body: any) => Promise<T>
  pages: BatchImportPageInput[]
  allConfigs: PageConfig[]
  onPageProgress?: (collection: string, current: number, total: number) => void
}

/**
 * 按引用依赖顺序逐页导入，收尾对每个导入过的页重新解析引用。
 */
export async function runBatchImport(params: RunBatchImportParams): Promise<BatchImportPageResult[]> {
  const { store, post, pages, allConfigs, onPageProgress } = params
  const { order } = buildReferenceOrder(allConfigs)
  const orderIndex = new Map(order.map((c, i) => [c, i]))

  const sorted = [...pages].sort(
    (a, b) => (orderIndex.get(a.collection) ?? 9999) - (orderIndex.get(b.collection) ?? 9999),
  )

  const results: BatchImportPageResult[] = []
  for (const p of sorted) {
    const r = await importPageRecords({
      store, post, pageId: p.pageId, collection: p.collection, records: p.records,
      onProgress: (cur, total) => onPageProgress?.(p.collection, cur, total),
    })
    results.push({ ...r, collection: p.collection, reResolved: 0, pending: 0 })
  }

  // 收尾重解析（顺序同上，确保被引用页已落库）
  for (const res of results) {
    const page = sorted.find((p) => p.collection === res.collection)!
    try {
      const { updated, pending } = await store.reResolveReferences(page.pageId)
      res.reResolved = updated
      res.pending = pending
    } catch {
      // 重解析失败不影响已导入数据
    }
  }

  return results
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/composables/__tests__/useBatchImport.test.ts`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add src/composables/useBatchImport.ts src/composables/__tests__/useBatchImport.test.ts
git commit -m "feat(data-migration): batch import orchestration (topo order + re-resolve)"
```

---

## Task 5: 前端 API `batchClearCollections`

**Files:**
- Modify: `src/api/menu.ts`

- [ ] **Step 1: Add API function**

在 `src/api/menu.ts` 末尾追加：

```typescript
/**
 * 批量清空多个数据页在指定分支的全部记录
 *
 * @param collections - collection 名称列表
 * @param branchId - 分支ID（默认 main）
 */
export function batchClearCollections(collections: string[], branchId = 'main') {
  return post<{
    perCollection: Record<string, number>
    totalDeleted: number
    relationsDeleted: number
  }>('/menuExport/batchClear', { collections, branchId })
}
```

- [ ] **Step 2: Typecheck**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 3: Commit**

```bash
git add src/api/menu.ts
git commit -m "feat(data-migration): batchClearCollections api"
```

---

## Task 6: 「数据迁移」页容器 + 导出 Tab 迁移 + 路由

**Files:**
- Create: `src/views/admin/DataMigrationPage.vue`
- Create: `src/views/admin/data-migration/ExportTab.vue`
- Modify: `src/router/index.ts:216-224`

- [ ] **Step 1: Create ExportTab by moving existing export page body**

新建 `src/views/admin/data-migration/ExportTab.vue`：把现有 `src/views/admin/MenuExportPage.vue` 的 `<template>` 内根 `<div class="menu-export-page">` 整体、`<script setup>`、`<style>` 原样复制进来（仅文件位置改变，逻辑不变）。导入路径 `@/api/menu`、`@/types` 保持不变。

- [ ] **Step 2: Create container with three tabs**

新建 `src/views/admin/DataMigrationPage.vue`：

```vue
<template>
  <div class="data-migration-page">
    <el-tabs v-model="activeTab" class="migration-tabs">
      <el-tab-pane label="数据导出" name="export">
        <ExportTab v-if="activeTab === 'export'" />
      </el-tab-pane>
      <el-tab-pane label="批量导入" name="import">
        <ImportTab v-if="activeTab === 'import'" />
      </el-tab-pane>
      <el-tab-pane label="批量清空" name="clear">
        <ClearTab v-if="activeTab === 'clear'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ExportTab from './data-migration/ExportTab.vue'
import ImportTab from './data-migration/ImportTab.vue'
import ClearTab from './data-migration/ClearTab.vue'

const activeTab = ref('export')
</script>

<style scoped lang="scss">
.data-migration-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.migration-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
  :deep(.el-tabs__content) {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
}
</style>
```

> 注：`ImportTab` / `ClearTab` 在 Task 7/8 创建。本步先创建占位文件以便容器编译——分别写入 `<template><div>TODO</div></template><script setup lang="ts"></script>`，Task 7/8 再实现。

- [ ] **Step 3: Update router**

将 `src/router/index.ts` 中 `menu-export` 路由块（行 216-224）改为：

```typescript
      {
        path: 'admin/menu-export',
        name: 'MenuExport',
        component: () => import('@/views/admin/DataMigrationPage.vue'),
        meta: {
          title: '数据迁移',
          icon: 'Download',
        },
      },
```

- [ ] **Step 4: Verify build**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误（占位 ImportTab/ClearTab 可编译）

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/DataMigrationPage.vue src/views/admin/data-migration/ src/router/index.ts
git commit -m "feat(data-migration): tabbed DataMigrationPage container + export tab"
```

---

## Task 7: 批量导入 Tab

**Files:**
- Create (replace placeholder): `src/views/admin/data-migration/ImportTab.vue`

- [ ] **Step 1: Implement ImportTab**

```vue
<template>
  <div class="import-tab">
    <el-form inline>
      <el-form-item label="项目">
        <el-tree-select
          v-model="selectedMenuId"
          :data="menuTree"
          :props="treeProps"
          node-key="id"
          check-strictly
          placeholder="选择项目"
          style="width: 280px"
          @change="loadPages"
        />
      </el-form-item>
      <el-form-item label="分支">
        <el-input v-model="branchId" style="width: 140px" />
      </el-form-item>
    </el-form>

    <el-table :data="pageRows" border v-loading="pagesLoading">
      <el-table-column prop="pageName" label="数据页" />
      <el-table-column prop="collection" label="Collection" width="180" />
      <el-table-column prop="recordCount" label="现有记录" width="100" align="right" />
      <el-table-column label="导入文件" width="260">
        <template #default="{ row }">
          <input type="file" accept=".xlsx,.xls,.json" @change="(e) => onFile(row, e)" />
          <span v-if="row._file" class="file-name">{{ row._file.name }}</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="actions">
      <el-button type="primary" :loading="running" :disabled="!hasFiles" @click="start">
        开始导入
      </el-button>
    </div>

    <el-dialog v-model="resultVisible" title="导入结果" width="640">
      <el-table :data="results" border size="small">
        <el-table-column prop="collection" label="Collection" />
        <el-table-column prop="created" label="新增" width="80" />
        <el-table-column prop="updated" label="更新" width="80" />
        <el-table-column prop="failed" label="失败" width="80" />
        <el-table-column prop="reResolved" label="补齐引用" width="90" />
        <el-table-column prop="pending" label="未匹配" width="80" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { post } from '@/utils/request'
import { getAvailableExportMenus, previewMenuExport } from '@/api/menu'
import { usePageConfigStore } from '@/stores'
import { parseImportFile, parseJsonImportFile } from '@/utils/excel'
import { runBatchImport, type BatchImportPageResult } from '@/composables/useBatchImport'
import type { MenuItem } from '@/types'

interface PageRow {
  pageId: string
  collection: string
  pageName: string
  recordCount: number
  _file?: File
}

const pageConfigStore = usePageConfigStore()
const treeProps = { children: 'children', label: 'name' }
const menuTree = ref<MenuItem[]>([])
const selectedMenuId = ref<string>('')
const branchId = ref('main')
const pageRows = ref<PageRow[]>([])
const pagesLoading = ref(false)
const running = ref(false)
const resultVisible = ref(false)
const results = ref<BatchImportPageResult[]>([])

const hasFiles = computed(() => pageRows.value.some((r) => r._file))

async function loadMenus() {
  menuTree.value = await getAvailableExportMenus()
}

async function loadPages() {
  if (!selectedMenuId.value) return
  pagesLoading.value = true
  try {
    const preview = await previewMenuExport([selectedMenuId.value])
    const pages = preview.menus.flatMap((m) => m.pages)
    pageRows.value = pages.map((p) => ({
      pageId: `page-${p.collection}`,
      collection: p.collection,
      pageName: p.pageName,
      recordCount: p.recordCount,
    }))
  } finally {
    pagesLoading.value = false
  }
}

async function onFile(row: PageRow, e: Event) {
  const input = e.target as HTMLInputElement
  row._file = input.files?.[0]
}

async function start() {
  if (!pageConfigStore.pageConfigs.length) await pageConfigStore.fetchPageConfigs()

  running.value = true
  try {
    const withFiles = pageRows.value.filter((r) => r._file)
    const pages = []
    for (const row of withFiles) {
      const fields = pageConfigStore.getPageFields(row.pageId)
      const isJson = row._file!.name.toLowerCase().endsWith('.json')
      const records = isJson
        ? await parseJsonImportFile(row._file!, fields)
        : await parseImportFile(row._file!, fields)
      pages.push({ pageId: row.pageId, collection: row.collection, records })
    }

    const allConfigs = withFiles.map((r) => pageConfigStore.getPageConfigById(r.pageId)!).filter(Boolean)
    results.value = await runBatchImport({ store: pageConfigStore, post, pages, allConfigs })
    resultVisible.value = true
    ElMessage.success('批量导入完成')
  } catch (err: any) {
    ElMessage.error(err?.message || '批量导入失败')
  } finally {
    running.value = false
  }
}

loadMenus()
</script>

<style scoped lang="scss">
.import-tab { padding: 8px; }
.actions { margin-top: 16px; }
.file-name { margin-left: 8px; font-size: 12px; color: #909399; }
</style>
```

- [ ] **Step 2: Typecheck**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 3: Manual smoke (record in commit message)**

启动 `npm run dev:all`，进入「数据迁移 → 批量导入」，选一个含 reference 字段的项目，为两个互相引用的页各挂一个导入模板 Excel，点「开始导入」，确认：结果弹窗按 collection 列出新增/失败/补齐引用计数，被引用页先导入。

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/data-migration/ImportTab.vue
git commit -m "feat(data-migration): batch import tab UI"
```

---

## Task 8: 批量清空 Tab

**Files:**
- Create (replace placeholder): `src/views/admin/data-migration/ClearTab.vue`

- [ ] **Step 1: Implement ClearTab**

```vue
<template>
  <div class="clear-tab">
    <el-form inline>
      <el-form-item label="项目">
        <el-tree-select
          v-model="selectedMenuId"
          :data="menuTree"
          :props="treeProps"
          node-key="id"
          check-strictly
          placeholder="选择项目"
          style="width: 280px"
          @change="loadPages"
        />
      </el-form-item>
      <el-form-item label="分支">
        <el-input v-model="branchId" style="width: 140px" />
      </el-form-item>
    </el-form>

    <el-table :data="pageRows" border v-loading="pagesLoading" @selection-change="onSelect">
      <el-table-column type="selection" width="48" />
      <el-table-column prop="pageName" label="数据页" />
      <el-table-column prop="collection" label="Collection" width="180" />
      <el-table-column prop="recordCount" label="记录数" width="100" align="right" />
    </el-table>

    <div class="actions">
      <span class="summary" v-if="selected.length">
        将清空 {{ selected.length }} 个页、共 {{ totalRecords }} 条记录
      </span>
      <el-button type="danger" :disabled="!selected.length" @click="confirmClear">
        清空所选
      </el-button>
    </div>

    <el-dialog v-model="confirmVisible" title="确认清空" width="480">
      <p>此操作将永久删除所选 {{ selected.length }} 个数据页（分支 {{ branchId }}）的全部记录，且不可恢复。</p>
      <p>请输入 <b>CLEAR</b> 以确认：</p>
      <el-input v-model="confirmText" placeholder="CLEAR" />
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="danger" :disabled="confirmText !== 'CLEAR'" :loading="clearing" @click="doClear">
          确认清空
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getAvailableExportMenus, previewMenuExport, batchClearCollections } from '@/api/menu'
import type { MenuItem } from '@/types'

interface PageRow { collection: string; pageName: string; recordCount: number }

const treeProps = { children: 'children', label: 'name' }
const menuTree = ref<MenuItem[]>([])
const selectedMenuId = ref('')
const branchId = ref('main')
const pageRows = ref<PageRow[]>([])
const pagesLoading = ref(false)
const selected = ref<PageRow[]>([])
const confirmVisible = ref(false)
const confirmText = ref('')
const clearing = ref(false)

const totalRecords = computed(() => selected.value.reduce((s, r) => s + r.recordCount, 0))

async function loadMenus() {
  menuTree.value = await getAvailableExportMenus()
}

async function loadPages() {
  if (!selectedMenuId.value) return
  pagesLoading.value = true
  try {
    const preview = await previewMenuExport([selectedMenuId.value])
    pageRows.value = preview.menus.flatMap((m) => m.pages)
  } finally {
    pagesLoading.value = false
  }
}

function onSelect(rows: PageRow[]) {
  selected.value = rows
}

function confirmClear() {
  confirmText.value = ''
  confirmVisible.value = true
}

async function doClear() {
  clearing.value = true
  try {
    const collections = selected.value.map((r) => r.collection)
    const res = await batchClearCollections(collections, branchId.value)
    ElMessage.success(`已清空 ${res.totalDeleted} 条记录`)
    confirmVisible.value = false
    await loadPages()
  } catch (err: any) {
    ElMessage.error(err?.message || '清空失败')
  } finally {
    clearing.value = false
  }
}

loadMenus()
</script>

<style scoped lang="scss">
.clear-tab { padding: 8px; }
.actions { margin-top: 16px; display: flex; align-items: center; gap: 12px; }
.summary { color: #e6a23c; }
</style>
```

- [ ] **Step 2: Typecheck**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 3: Manual smoke**

进入「数据迁移 → 批量清空」，选项目→勾选数据页→点「清空所选」→输入 `CLEAR`→确认，核对记录数清零、关联随之清理。

- [ ] **Step 4: Delete obsolete MenuExportPage.vue**

现有 `src/views/admin/MenuExportPage.vue` 已被 `DataMigrationPage` + `ExportTab` 取代，删除它：

```bash
git rm src/views/admin/MenuExportPage.vue
```

确认无其它引用：`grep -rn "MenuExportPage" src/`（应只剩无结果或已改的 router）。

- [ ] **Step 5: Full test suite + commit**

Run: `npm run test && cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_menu_export.py -v`
Expected: 全部 PASS

```bash
git add -A
git commit -m "feat(data-migration): batch clear tab UI; remove old MenuExportPage"
```

---

## Self-Review 结果

- **Spec 覆盖**：三 Tab 容器(Task6) / 批量导入+拓扑排序+收尾重解析(Task2,3,4,7) / 批量清空+data_relations 清理+二次确认(Task1,5,8) / 测试(各 Task) 均有对应任务。✓
- **占位符**：ImportTab/ClearTab 在 Task6 以临时占位创建、Task7/8 替换实现——已显式说明，非遗留占位。✓
- **类型一致**：`importPageRecords`(Task3) → `runBatchImport`(Task4) → `ImportTab`(Task7) 间 `ImportPageResult` / `BatchImportPageResult` / `post` 注入签名一致；`batchClearCollections`(Task5) 返回类型与后端(Task1)字段 `perCollection/totalDeleted/relationsDeleted` 一致。✓
