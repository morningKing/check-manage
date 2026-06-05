# 引用字段延迟解析 + 手动重新解析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让引用页可在被引用页之前导入：导入时保留未解析的 quoteSelect 值，并提供「重新解析引用」按钮在被引用页导入后把 quoteSelect/reference 补全为有效 id。

**Architecture:** 纯前端。导入解析器 `resolveQuoteImportValues` 改为匹配不上时保留原始主键/显示值（不丢弃）；新增 store action `reResolveReferences(pageId)` 复用导入解析器跑在已存数据上，仅回写变化记录（batch-create）；`DynamicPage` 加触发按钮。

**Tech Stack:** Vue 3 + TypeScript + Pinia，Vitest。

---

## File Structure

- `src/stores/pageConfig.ts`（改）— `resolveQuoteImportValues` 保留原始值；新增并导出 `reResolveReferences`。
- `src/views/dynamic/DynamicPage.vue`（改）— 「更多」下拉新增「重新解析引用」+ 处理函数 + 条件显示。
- `src/stores/__tests__/pageConfig.test.ts`（改）— 新增用例。

---

## Task 1: 导入时保留未解析的 quoteSelect 值

**Files:**
- Modify: `src/stores/pageConfig.ts` (`resolveQuoteImportValues`, ~`:1288-1300`)
- Test: `src/stores/__tests__/pageConfig.test.ts`

- [ ] **Step 1: Write the failing test** — append inside the existing top-level `describe(...)` block in `src/stores/__tests__/pageConfig.test.ts`:

```ts
  it('resolveQuoteImportValues 目标缺失时保留原始值（不丢弃）', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({
              id: 'f1', fieldName: 'quotedCases', controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })
    // 目标集合此刻还没有数据（被引用页尚未导入）
    mockedGet.mockResolvedValueOnce({ data: [], total: 0 })

    const records = [{ quotedCases: ['IC-001', 'IC-002'] }]
    await store.resolveQuoteImportValues('page-test', records)
    // 改动前会被清空为 []；改动后保留原始主键值，供之后重新解析
    expect(records[0].quotedCases).toEqual(['IC-001', 'IC-002'])
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts -t "保留原始值"`
Expected: FAIL — `records[0].quotedCases` is `[]` (current code drops unmatched values).

- [ ] **Step 3: Implement** — in `src/stores/pageConfig.ts`, in `resolveQuoteImportValues`, change the per-value loop. Replace:
```ts
        for (const v of vals) {
          if (idSet.has(v)) {
            if (!seen.has(v)) { seen.add(v); resolved.push(v) }
          } else {
            const ids = pkToIds.get(v) || displayToIds.get(v)
            if (ids) {
              for (const id of ids) {
                if (!seen.has(id)) { seen.add(id); resolved.push(id) }
              }
            }
          }
        }
```
with:
```ts
        for (const v of vals) {
          if (idSet.has(v)) {
            if (!seen.has(v)) { seen.add(v); resolved.push(v) }
          } else {
            const ids = pkToIds.get(v) || displayToIds.get(v)
            if (ids) {
              for (const id of ids) {
                if (!seen.has(id)) { seen.add(id); resolved.push(id) }
              }
            } else if (!seen.has(v)) {
              // 目标记录尚不存在：保留原始主键/显示值，供之后「重新解析引用」补全（不丢弃）
              seen.add(v); resolved.push(v)
            }
          }
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts -t "保留原始值"`
Expected: PASS. Then run the existing quote tests to confirm no regression:
`npx vitest run src/stores/__tests__/pageConfig.test.ts -t "resolveQuoteImportValues"`
Expected: PASS (existing `正确解析导入值` and `保持导入值的原始顺序` still green — they resolve against present targets).

- [ ] **Step 5: Commit**

```bash
git add src/stores/pageConfig.ts src/stores/__tests__/pageConfig.test.ts
git commit -m "fix(import): keep unresolved quoteSelect values instead of dropping them"
```

---

## Task 2: `reResolveReferences` store action

**Files:**
- Modify: `src/stores/pageConfig.ts` (add function + export it in the store's return object)
- Test: `src/stores/__tests__/pageConfig.test.ts`

- [ ] **Step 1: Write the failing test** — append inside the existing top-level `describe(...)` block:

```ts
  it('reResolveReferences 把已存的原始值解析为内部 id 并回写', async () => {
    const mockedGet = vi.mocked(get)
    const mockedPost = vi.mocked(post)
    mockedGet.mockReset()
    mockedPost.mockReset()
    mockedPost.mockResolvedValue({ success: true, created: 0, updated: 1, failed: 0 } as any)

    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-orders',
          fields: [
            makeField({ id: 'nm', fieldName: 'orderNo', controlType: 'text' }),
            makeField({
              id: 'f1', fieldName: 'quotedCases', controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [
            makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }),
          ],
        }),
      ],
    })

    // get('/orders') → 已存记录，quotedCases 里是原始主键值 'IC-001'（之前导入时目标还不存在）
    // get('/cases')  → 目标现已导入，存在 case-1(IC-001)
    mockedGet.mockImplementation((url: string) => {
      if (url === '/orders') {
        return Promise.resolve({ data: [{ id: 'order-1', orderNo: 'O1', quotedCases: ['IC-001'] }], total: 1 })
      }
      if (url === '/cases') {
        return Promise.resolve({ data: [{ id: 'case-1', caseId: 'IC-001', caseName: '用例A' }], total: 1 })
      }
      return Promise.resolve({ data: [], total: 0 })
    })

    const res = await store.reResolveReferences('page-orders')
    expect(res.updated).toBe(1)
    expect(res.pending).toBe(0)
    // 回写 payload 里 quotedCases 已变成内部 id
    expect(mockedPost).toHaveBeenCalledTimes(1)
    const [url, body] = mockedPost.mock.calls[0]
    expect(url).toBe('/orders/batch-create')
    expect(body.records[0].id).toBe('order-1')
    expect(body.records[0].data.quotedCases).toEqual(['case-1'])
  })

  it('reResolveReferences 目标仍缺失时保持原始值且计入 pending、不回写', async () => {
    const mockedGet = vi.mocked(get)
    const mockedPost = vi.mocked(post)
    mockedGet.mockReset()
    mockedPost.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-orders',
          fields: [
            makeField({
              id: 'f1', fieldName: 'quotedCases', controlType: 'quoteSelect',
              quoteConfig: { targetCollection: 'cases', displayField: 'caseName' },
            }),
          ],
        }),
        makePageConfig({
          id: 'page-cases',
          fields: [ makeField({ id: 'pk', fieldName: 'caseId', controlType: 'text', isPrimaryKey: true }) ],
        }),
      ],
    })
    mockedGet.mockImplementation((url: string) => {
      if (url === '/orders') return Promise.resolve({ data: [{ id: 'order-1', quotedCases: ['IC-999'] }], total: 1 })
      if (url === '/cases') return Promise.resolve({ data: [], total: 0 })  // 目标仍空
      return Promise.resolve({ data: [], total: 0 })
    })
    const res = await store.reResolveReferences('page-orders')
    expect(res.updated).toBe(0)
    expect(res.pending).toBe(1)
    expect(mockedPost).not.toHaveBeenCalled()  // 无变化，不回写
  })
```

(Confirm `post` is already in the test file's imports: `import { get, post } from '@/utils/request'` — it is.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts -t "reResolveReferences"`
Expected: FAIL — `store.reResolveReferences is not a function`.

- [ ] **Step 3: Implement** — in `src/stores/pageConfig.ts`, add this function (place it right after `resolveReferenceImportValues`, ~`:1200`, alongside the other resolve helpers). It reuses `getQuoteFields`, `getReferenceFields`, `fetchCollectionData`, `resolveQuoteImportValues`, `resolveReferenceImportValues`, `get`, `post` — all defined in this store:

```ts
  /**
   * 重新解析当前页面已存数据中的 quoteSelect / reference 引用字段：
   * 把导入时未匹配而保留的原始主键/显示值，按目标集合的当前数据补全为内部 id。
   * 仅回写发生变化的记录（quoteSelect/reference 都在 JSONB data 内，relations 传空不动关联表）。
   * 返回 { updated: 回写记录数, pending: 仍含未匹配引用的记录数 }。
   */
  async function reResolveReferences(
    pageId: string
  ): Promise<{ updated: number; pending: number }> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return { updated: 0, pending: 0 }
    const quoteFields = getQuoteFields(pageId)
    const refFields = getReferenceFields(pageId)
    const refFieldNames = [...quoteFields, ...refFields].map((f) => f.fieldName)
    if (refFieldNames.length === 0) return { updated: 0, pending: 0 }

    const endpoint = pageId.replace('page-', '')
    let records: any[]
    try {
      const resp = await get<{ data: any[]; total: number }>(`/${endpoint}`, { pageSize: 10000 })
      records = resp.data || []
    } catch {
      return { updated: 0, pending: 0 }
    }
    if (records.length === 0) return { updated: 0, pending: 0 }

    // 解析前快照引用字段值
    const before = records.map((r) => refFieldNames.map((fn) => JSON.stringify(r[fn] ?? null)))

    // 复用导入解析器，针对当前目标数据重跑（共享缓存）
    const cache = new Map<string, any[]>()
    await resolveQuoteImportValues(pageId, records, cache)
    await resolveReferenceImportValues(pageId, records, cache)

    // 用同一缓存构建目标 id 集合（不产生额外请求），用于统计 pending
    const idSetByCollection = new Map<string, Set<string>>()
    const targetOf = (f: FieldConfig) =>
      f.quoteConfig?.targetCollection || f.referenceConfig?.targetCollection || ''
    for (const f of [...quoteFields, ...refFields]) {
      const tc = targetOf(f)
      if (!tc || idSetByCollection.has(tc)) continue
      const recs = await fetchCollectionData(tc, cache)
      idSetByCollection.set(tc, new Set(recs.map((r: any) => r.id)))
    }
    const idSetFor = (f: FieldConfig) => idSetByCollection.get(targetOf(f)) || new Set<string>()

    const changed: any[] = []
    let pending = 0
    records.forEach((r, i) => {
      const after = refFieldNames.map((fn) => JSON.stringify(r[fn] ?? null))
      if (after.some((v, j) => v !== before[i][j])) changed.push(r)
      const hasPending =
        quoteFields.some((f) => Array.isArray(r[f.fieldName]) &&
          r[f.fieldName].some((v: string) => !idSetFor(f).has(v))) ||
        refFields.some((f) => !!r[f.fieldName] && !idSetFor(f).has(String(r[f.fieldName])))
      if (hasPending) pending++
    })

    if (changed.length > 0) {
      const allFieldNames = config.fields.map((f) => f.fieldName)
      const batchRecords = changed.map((r) => {
        const data: Record<string, any> = {}
        for (const fn of allFieldNames) if (r[fn] !== undefined) data[fn] = r[fn]
        return { id: r.id, data, relations: {} }
      })
      await post(`/${endpoint}/batch-create`, {
        records: batchRecords,
        options: { skipValidation: false, continueOnError: true },
      })
    }

    return { updated: changed.length, pending }
  }
```

Then **export it**: in the store's `return { ... }` object (where `resolveReferenceImportValues`, `fetchQuoteDisplayMaps`, etc. are listed), add `reResolveReferences,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts -t "reResolveReferences"`
Expected: PASS (2 tests). Then full file: `npx vitest run src/stores/__tests__/pageConfig.test.ts` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/stores/pageConfig.ts src/stores/__tests__/pageConfig.test.ts
git commit -m "feat(import): reResolveReferences re-runs resolvers on stored data"
```

---

## Task 3: 「重新解析引用」按钮

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

- [ ] **Step 1: Add a computed for whether the page has reference-type fields**

In `src/views/dynamic/DynamicPage.vue` `<script setup>`, near other computed/field helpers, add:
```ts
const hasReferenceFields = computed<boolean>(() =>
  (pageConfig.value?.fields ?? []).some((f) => f.controlType === 'quoteSelect' || f.controlType === 'reference'),
)
```
(If `computed` / `pageConfig` aren't already in scope, they are — used throughout this file.)

- [ ] **Step 2: Add the handler**

Add near `handleExport` (~`:2326`):
```ts
async function handleReResolveReferences(): Promise<void> {
  try {
    const { updated, pending } = await pageConfigStore.reResolveReferences(pageId.value)
    if (updated > 0) await loadPageData()  // 刷新表格显示
    if (updated === 0 && pending === 0) {
      ElMessage.success('引用均已解析，无需更新')
    } else if (pending > 0) {
      ElMessage.warning(`已解析并更新 ${updated} 条；仍有 ${pending} 条未匹配（请确认被引用数据已导入）`)
    } else {
      ElMessage.success(`已重新解析，更新 ${updated} 条`)
    }
  } catch {
    ElMessage.error('重新解析失败')
  }
}
```
(`loadPageData`, `ElMessage`, `pageConfigStore`, `pageId` are all already defined/imported in this file.)

- [ ] **Step 3: Add the menu item in the 「更多」 dropdown**

Find the `<el-dropdown-item ... command="import" ...>导入数据</el-dropdown-item>` (~`:106`) inside the 「更多」 dropdown menu. Add a new item next to it (guarded by `hasReferenceFields`):
```vue
              <el-dropdown-item v-if="!isGuest && hasReferenceFields" command="reResolveRefs" :icon="RefreshRight">重新解析引用</el-dropdown-item>
```
Find the dropdown's `@command` handler (the function that switches on `command`, e.g. `handleImportCommand` / a `handleCommand`). Inspect which `@command` the 「更多」 `el-dropdown` is bound to, and add a branch routing `'reResolveRefs'` to `handleReResolveReferences()`. For example, if items are handled by a function with `if (command === 'import') ...`, add:
```ts
  if (command === 'reResolveRefs') { handleReResolveReferences(); return }
```
Ensure `RefreshRight` is imported from `@element-plus/icons-vue` (add to the existing icon import if missing).

> If the 「更多」 dropdown uses per-item `command` strings routed through one handler, place the branch there. If instead each item has its own `@click`, bind `@click="handleReResolveReferences"` on the new item and drop the `command`. Match the file's actual pattern.

- [ ] **Step 4: Type-check + run frontend store tests**

Run: `npx vue-tsc --noEmit 2>&1 | grep -iE "DynamicPage|pageConfig" | head`
Expected: no NEW errors referencing `hasReferenceFields`, `handleReResolveReferences`, `reResolveReferences`.

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(import): add 重新解析引用 action to data page"
```

---

## Self-Review 结果

- **Spec 覆盖：** 导入保留原始 quoteSelect 值（Task 1）；reference 导入侧本就保留（spec 已说明，无需改）；`reResolveReferences` 复用解析器、只回写变化、relations 传空、返回计数（Task 2）；UI 按钮 + 条件显示 + toast（Task 3）；测试（Task 1/2）。仅 quoteSelect+reference、不动 relation/后端 —— 一致。
- **Placeholder 扫描：** 无 TBD/TODO；代码步骤均含完整 before/after 或完整函数。Task 3 Step 3 的 `@command` 说明给了两种真实形态的处理方式，非占位。
- **类型/命名一致：** `reResolveReferences(pageId): {updated, pending}` 在定义、导出、测试、调用处一致；`getQuoteFields`/`getReferenceFields`/`fetchCollectionData`/`resolveQuoteImportValues`/`resolveReferenceImportValues`/`get`/`post` 均为 store 既有成员；`hasReferenceFields`/`handleReResolveReferences`/`RefreshRight` 在 DynamicPage 内一致。
