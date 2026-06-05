# 数据导入保序行 id Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让数据页「导入数据」后列表默认顺序与导入文件的行顺序一致。

**Architecture:** 行 id 改为内嵌零填充全局序号 `${collection}-${paddedSeq}-${rand}`，使同一批（`created_at` 相同）内按 `id` 排序即按文件顺序；在 `doImport` 的引用解析之前给每条记录预盖 `_importId`，保证自引用一致。纯前端，不动后端/schema。

**Tech Stack:** Vue 3 + TypeScript，Vitest。

---

## File Structure

- `src/utils/importId.ts`（新）— 纯函数 `makeImportRowId(collection, index, total)`，唯一职责：生成保序行 id。
- `src/utils/__tests__/importId.test.ts`（新）— 单测。
- `src/views/dynamic/DynamicPage.vue`（改）— `doImport` 预盖 `_importId` + 导入该函数。

---

## Task 1: `makeImportRowId` 纯函数

**Files:**
- Create: `src/utils/importId.ts`
- Test: `src/utils/__tests__/importId.test.ts`

- [ ] **Step 1: Write the failing test** — create `src/utils/__tests__/importId.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { makeImportRowId } from '../importId'

describe('makeImportRowId', () => {
  it('prefixes with the collection name', () => {
    expect(makeImportRowId('orders', 0, 10)).toMatch(/^orders-/)
  })

  it('zero-pads the sequence to the width of total', () => {
    // total=1000 -> width 4
    expect(makeImportRowId('c', 5, 1000)).toMatch(/^c-0005-/)
    // total=10 -> width 2
    expect(makeImportRowId('c', 3, 10)).toMatch(/^c-03-/)
  })

  it('generates ids that sort lexicographically in file order (incl. powers of ten)', () => {
    const total = 23 // crosses the 9->10 boundary
    const ids = Array.from({ length: total }, (_, i) => makeImportRowId('c', i, total))
    const sorted = [...ids].sort()
    expect(sorted).toEqual(ids)
  })

  it('includes a random suffix so two calls for the same index differ', () => {
    const a = makeImportRowId('c', 1, 10)
    const b = makeImportRowId('c', 1, 10)
    expect(a).not.toBe(b)
    expect(a.slice(0, 'c-01-'.length)).toBe(b.slice(0, 'c-01-'.length)) // same prefix+seg
  })

  it('handles total=0 without throwing or zero-width padding', () => {
    expect(makeImportRowId('c', 0, 0)).toMatch(/^c-0-/)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/utils/__tests__/importId.test.ts`
Expected: FAIL (cannot find module `../importId`)

- [ ] **Step 3: Write minimal implementation** — create `src/utils/importId.ts`:

```ts
/**
 * Build an order-preserving row id for data-page imports.
 *
 * Rows imported in one batch all share `created_at` (the INSERT uses the
 * `now()` default = transaction time), so the list's `ORDER BY created_at, id`
 * falls back to `id`. Embedding a zero-padded, fixed-width sequence number makes
 * the lexicographic id order match the file's row order. A short random suffix
 * keeps the id globally unique across re-imports (id+branch is the PK).
 *
 * @param collection collection name (id prefix)
 * @param index      zero-based row index in file order
 * @param total      total number of rows in this import (sets the pad width)
 */
export function makeImportRowId(collection: string, index: number, total: number): string {
  const width = String(Math.max(total, 1)).length
  const seq = String(index).padStart(width, '0')
  const rand = Math.random().toString(36).slice(2, 8)
  return `${collection}-${seq}-${rand}`
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/utils/__tests__/importId.test.ts`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/utils/importId.ts src/utils/__tests__/importId.test.ts
git commit -m "feat(import): makeImportRowId for order-preserving import row ids"
```

---

## Task 2: 在 doImport 预盖 `_importId`

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

- [ ] **Step 1: Add the import**

In `src/views/dynamic/DynamicPage.vue`, find the existing util import line (around `:839`):
```ts
import { exportToExcel, generateImportTemplate, parseImportFile, parseJsonImportFile } from '@/utils/excel'
```
and add a new import line directly after it:
```ts
import { makeImportRowId } from '@/utils/importId'
```

- [ ] **Step 2: Pre-stamp `_importId` before reference resolution**

In `doImport` (around `:2461`), find:
```ts
  // 创建共享缓存，避免多个 resolve 函数重复请求同一集合
  const collectionCache = new Map<string, any[]>()

  // 并行解析（使用共享缓存）
  await Promise.all([
```
and insert the pre-stamp loop between the cache creation and the `await Promise.all`:
```ts
  // 创建共享缓存，避免多个 resolve 函数重复请求同一集合
  const collectionCache = new Map<string, any[]>()

  // 预盖保序行 id：让同一批 (created_at 相同) 内按 id 排序即按文件顺序。
  // 必须在引用解析之前，这样自引用 (resolve*ImportValues) 用到的 _importId
  // 与最终行 id 一致，引用不会断。
  records.forEach((r, i) => {
    if (!r._importId) r._importId = makeImportRowId(collection.value, i, records.length)
  })

  // 并行解析（使用共享缓存）
  await Promise.all([
```

(No change to the `id: importId || ...` line at `:2526` — `_importId` is now always set, so the order-preserving id is used; the `|| <random>` stays as a defensive fallback.)

- [ ] **Step 3: Type-check (only care about NEW errors in these files)**

Run: `npx vue-tsc --noEmit 2>&1 | grep -iE "DynamicPage|importId" | head -20`
Expected: no NEW errors referencing `makeImportRowId`, `_importId`, or the lines you added. (The project has pre-existing unrelated vue-tsc output; only your additions must be clean.)

- [ ] **Step 4: Run the import-related test suites to confirm no regression**

Run: `npx vitest run src/utils/__tests__/importId.test.ts src/utils/__tests__/fieldImport.test.ts src/stores/__tests__/pageConfig.test.ts`
Expected: PASS (import-order util + existing import/reference tests stay green — confirms pre-stamping doesn't break self-reference resolution).

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(import): stamp order-preserving _importId before reference resolution"
```

---

## Self-Review 结果

- **Spec 覆盖：** ID 格式 + 定宽零填充（Task 1）；`makeImportRowId` 纯函数（Task 1）；`doImport` 引用解析前预盖 `_importId`（Task 2 Step 2）；不改后端/pageConfig/`:2526`（Task 2 说明）；测试（Task 1 五用例 + Task 2 Step 4 回归）均覆盖。`width = String(Math.max(total,1)).length`、total=0 兜底、跨 10 的幂保序断言都已落到具体测试。
- **Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整代码与确切命令。
- **类型/命名一致：** `makeImportRowId(collection, index, total)` 的签名在定义、测试、调用处一致；`_importId` 字段名与 `pageConfig.ts:1360`、`DynamicPage.vue:2504/2506/2526` 现有用法一致。
