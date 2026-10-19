# 导入失败记录导出 + 重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DynamicPage 的「导入数据」在部分记录失败时，让用户能导出一份带失败原因的清单文件，并且不重新走完整流程就能一键重试当前仍失败的记录。

**Architecture:** 后端 `batch-create` 早已返回每条失败记录的 index/原因（`continueOnError=true` 时），前端 `importPageRecords.ts` 目前把这些信息丢弃，只保留计数。本次改造让它把每条失败记录（连同已经就绪、可直接重新提交的请求体）收集进 `failures[]`；新增 `retryImportFailures` 直接重放这些请求体（不重新解析引用/生成序列号）；`DynamicPage.vue` 在导入结果面板里挂上「导出失败清单」（生成 xlsx）和「重试失败记录」两个按钮。

**Tech Stack:** Vue 3 + TypeScript（前端逻辑与 UI），Vitest（单测），SheetJS/xlsx（已是项目依赖，导出文件用）。

## Global Constraints

- 仅改动 `src/utils/importPageRecords.ts`、`src/utils/excel.ts`、`src/views/dynamic/DynamicPage.vue` 三个文件（及它们对应的测试文件）。`src/composables/useBatchImport.ts` 和 `src/views/admin/DataMigrationPage.vue`（跨项目批量导入）本次不动——已与用户确认范围。
- 失败记录清单**不做任何后端持久化**，只存在于导入弹窗的前端响应式状态里，关闭弹窗即丢弃；导出的 xlsx 文件是唯一的留档方式。
- 重试必须直接复用 `runBatch` 里已经构建好的请求体（`{id, data, relations}`），**不得**重新调用 `store.resolveRelationImportValues`/`resolveReferenceImportValues`/`resolveQuoteImportValues`/`resolveCollectionSelectImportValues`/`batchGenerateSequenceValues` 等任何 store 方法——重新生成序列号会与已成功导入的记录冲突。
- `BATCH_SIZE = 1000`、`CONCURRENCY = 3`（`src/utils/importPageRecords.ts` 已有常量，重试沿用，不新增可配置项）。
- 每个改动文件的 SFC/模块保持现有代码风格（中文注释、现有命名习惯），不引入新的第三方依赖。

---

### Task 1: `importPageRecords.ts` — 收集每条失败记录（含原因与可重放请求体）

**Files:**
- Modify: `src/utils/importPageRecords.ts`
- Test: `src/utils/__tests__/importPageRecords.test.ts`

**Interfaces:**
- Produces:
  - `interface ImportFailure { originalRecord: Record<string, any>; payload: { id: string; data: any; relations: Record<string, string[]> }; reason: string }`
  - `interface ImportPageResult { success: number; failed: number; created: number; updated: number; failures: ImportFailure[] }`（`failures` 为新增字段）
  - 内部辅助 `async function runBatchesConcurrently(itemCount: number, runOne: (batchIdx: number) => Promise<void>): Promise<void>`（模块内私有，不导出——Task 2 的 `retryImportFailures` 会在同一个文件内直接调用它）

- [ ] **Step 1: 改写现有测试的 `toEqual` 断言，补上新增的 `failures` 字段**

`src/utils/__tests__/importPageRecords.test.ts` 里两处 `expect(result).toEqual({...})` 会因为 `result` 多出 `failures` 字段而失败（`toEqual` 是精确匹配）。先把断言改到位（此时实现还没加 `failures`，这两个断言会先短暂地"变红"，属于预期中的 TDD 红灯）：

```ts
// 第一个测试：'resolves references then calls batch-create and returns counts'
expect(result).toEqual({ success: 2, failed: 0, created: 2, updated: 0, failures: [] })
```

```ts
// 第六个测试：'splits large imports into multiple concurrent batches and aggregates all of them'
expect(result).toEqual({ success: 2500, failed: 0, created: 2500, updated: 0, failures: [] })
```

同时给第五个测试（`'counts batch failure without throwing'`）和第七个测试（`'one failing batch does not affect the other concurrently-run batches'`）追加对 `failures` 的断言：

```ts
// 'counts batch failure without throwing' 测试末尾追加：
// 注意：originalRecord 是同一个 record 引用，import 流程一开始就会给每条记录
// 盖上 _importId（见 importPageRecords 顶部的 records.forEach），所以这里不是
// 干净的 { name: 'a' }，断言要带上这个字段，否则 toEqual 会因为多出一个字段而失败。
expect(result.failures).toHaveLength(1)
expect(result.failures[0].originalRecord).toEqual({ name: 'a', _importId: expect.any(String) })
expect(result.failures[0].reason).toMatch(/请求失败.*boom.*可重试/)
```

```ts
// 'one failing batch does not affect the other concurrently-run batches' 测试末尾追加：
expect(result.failures).toHaveLength(1000)
expect(result.failures[0].originalRecord.name).toBe('r0')
expect(result.failures[0].reason).toMatch(/请求失败.*batch 0 boom.*可重试/)
```

- [ ] **Step 2: 新增两个针对"后端逐条失败"的测试**

在 `describe('importPageRecords', ...)` 块内追加：

```ts
  it('captures per-record failures reported by the backend, keyed back to the original record', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({
      created: 1,
      updated: 0,
      failed: 1,
      errors: [{ index: 1, error: '主键重复', record: { id: 'orders-b' } }],
    })
    const records = [{ name: 'a' }, { name: 'b' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].originalRecord).toEqual({ name: 'b', _importId: expect.any(String) })
    expect(result.failures[0].reason).toBe('主键重复')
    // payload 是已经构建好、可以直接重新 POST 的请求体
    expect(result.failures[0].payload).toEqual(post.mock.calls[0][1].records[1])
  })

  it('joins validationErrors into a single reason string when the backend reports a validation-script failure', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({
      created: 0,
      updated: 0,
      failed: 1,
      errors: [{
        index: 0,
        error: '校验失败',
        validationErrors: ['字段A必填', '字段B格式错误'],
        record: { id: 'orders-a' },
      }],
    })
    const records = [{ name: 'a' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(result.failures[0].reason).toBe('字段A必填; 字段B格式错误')
  })
```

- [ ] **Step 3: 运行测试，确认新增/改写的用例失败**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: FAIL — `toEqual` 断言因缺少 `failures` 字段报错；新增的两个测试因 `result.failures` 是 `undefined` 报错（`TypeError: Cannot read properties of undefined`）。

- [ ] **Step 4: 实现 — 抽出并发分批的公共辅助函数**

在 `src/utils/importPageRecords.ts` 里，`BATCH_SIZE`/`CONCURRENCY` 常量定义之后（原 `runBatch` 函数定义之前）新增：

```ts
/**
 * 按 BATCH_SIZE 切分、以 CONCURRENCY 个并发 worker 跑完 itemCount 个条目。
 * 每个 worker 从共享游标领取下一个批次下标，直到取完——与执行顺序无关，
 * 供 importPageRecords 和 retryImportFailures 共用，避免重复实现同一套调度逻辑。
 */
async function runBatchesConcurrently(
  itemCount: number,
  runOne: (batchIdx: number) => Promise<void>
): Promise<void> {
  const batchCount = Math.ceil(itemCount / BATCH_SIZE)
  let nextBatchIdx = 0
  async function worker(): Promise<void> {
    while (nextBatchIdx < batchCount) {
      const batchIdx = nextBatchIdx++
      await runOne(batchIdx)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, batchCount) }, () => worker()))
}
```

- [ ] **Step 5: 实现 — 新增 `ImportFailure` 类型，扩展 `ImportPageResult`**

```ts
export interface ImportFailure {
  originalRecord: Record<string, any>
  payload: { id: string; data: any; relations: Record<string, string[]> }
  reason: string
}

export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
  failures: ImportFailure[]
}
```

- [ ] **Step 6: 实现 — 改造 `runBatch`，收集 `failures`**

把 `batchData` 的构建移到 `try` 块顶部（用 `let` 声明在 `try` 外层，使 `catch` 分支也能访问到已构建的部分），并在两个分支里推入 `failures`：

```ts
  let success = 0, failed = 0, created = 0, updated = 0
  let completed = 0
  const failures: ImportFailure[] = []

  async function runBatch(batchIdx: number): Promise<void> {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, records.length)
    const batchRecords = records.slice(start, end)
    let batchData: Array<{ id: string; data: any; relations: Record<string, string[]> }> = []

    try {
      batchData = batchRecords.map((record, idx) => {
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
          // id 正常来自上面的 _importId 盖章；此为防御性兜底
          id: importId || `${collection}-${Math.random().toString(36).slice(2, 10)}`,
          data: regularData,
          relations,
        }
      })

      const result = await post<{
        created: number
        updated?: number
        failed: number
        errors?: Array<{ index: number; error: string; validationErrors?: string[]; record: any }>
      }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
      if (result.errors && result.errors.length > 0) {
        console.warn(`批次 ${batchIdx + 1} 失败记录:`, result.errors)
        for (const err of result.errors) {
          failures.push({
            originalRecord: batchRecords[err.index],
            payload: batchData[err.index],
            reason: err.validationErrors && err.validationErrors.length > 0
              ? err.validationErrors.join('; ')
              : err.error,
          })
        }
      }
    } catch (error) {
      console.error(`批次 ${batchIdx + 1} 导入失败:`, error)
      failed += batchRecords.length
      const message = error instanceof Error ? error.message : String(error)
      batchRecords.forEach((record, idx) => {
        failures.push({
          originalRecord: record,
          // batchData 构建阶段本身抛错（极少见）时兜底一个可用的最小请求体，
          // 保证即使这种边界情况也不丢失可重试性
          payload: batchData[idx] ?? {
            id: (record._importId as string) || `${collection}-${Math.random().toString(36).slice(2, 10)}`,
            data: record,
            relations: {},
          },
          reason: `请求失败：${message}，可重试`,
        })
      })
    }
    completed += batchRecords.length
    onProgress?.(completed, records.length)
  }

  await runBatchesConcurrently(records.length, runBatch)

  return { success, failed, created, updated, failures }
```

这一步替换掉原文件里从 `let success = 0, failed = 0, created = 0, updated = 0` 到函数末尾 `return { success, failed, created, updated }` 的全部内容（包括原来手写的 `nextBatchIdx`/`worker`/`Promise.all` 那一段，现在改用 `runBatchesConcurrently`）。

- [ ] **Step 7: 运行测试，确认全部通过**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: PASS（全部用例，包括 Step 1/2 新增和改写的部分）

- [ ] **Step 8: 提交**

```bash
git add src/utils/importPageRecords.ts src/utils/__tests__/importPageRecords.test.ts
git commit -m "feat(import): 记录每条导入失败原因，供后续导出/重试使用"
```

---

### Task 2: `importPageRecords.ts` — 新增 `retryImportFailures`

**Files:**
- Modify: `src/utils/importPageRecords.ts`
- Test: `src/utils/__tests__/importPageRecords.test.ts`

**Interfaces:**
- Consumes: `ImportFailure`、`ImportPageResult`、`runBatchesConcurrently`（均来自 Task 1，同一文件内）
- Produces: `async function retryImportFailures(post: <T>(url: string, body: any) => Promise<T>, collection: string, failures: ImportFailure[], onProgress?: (current: number, total: number) => void): Promise<ImportPageResult>`

- [ ] **Step 1: 写失败测试**

在 `src/utils/__tests__/importPageRecords.test.ts` 顶部 import 里加上 `retryImportFailures`：

```ts
import { importPageRecords, retryImportFailures } from '../importPageRecords'
```

在文件末尾（`describe('importPageRecords', ...)` 闭合括号之后）新增：

```ts
describe('retryImportFailures', () => {
  function makeFailure(overrides: Partial<import('../importPageRecords').ImportFailure> = {}) {
    return {
      originalRecord: { name: 'a' },
      payload: { id: 'orders-a', data: { name: 'a' }, relations: {} },
      reason: '主键重复',
      ...overrides,
    }
  }

  it('re-posts the given payloads directly, without any resolution step', async () => {
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const failures = [makeFailure()]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(post).toHaveBeenCalledWith('/orders/batch-create', {
      records: [failures[0].payload],
      options: { skipValidation: false, generateSequence: true, continueOnError: true },
    })
    expect(result).toEqual({ success: 1, failed: 0, created: 1, updated: 0, failures: [] })
  })

  it('splits more than BATCH_SIZE failures into multiple concurrent batches', async () => {
    const post = vi.fn().mockImplementation(async (_url: string, body: any) => ({
      created: body.records.length, updated: 0, failed: 0,
    }))
    const failures = Array.from({ length: 2500 }, (_, i) => makeFailure({
      originalRecord: { name: `r${i}` },
      payload: { id: `orders-${i}`, data: { name: `r${i}` }, relations: {} },
    }))
    const onProgress = vi.fn()

    const result = await retryImportFailures(post, 'orders', failures, onProgress)

    expect(post).toHaveBeenCalledTimes(3) // 1000 / 1000 / 500
    expect(result).toEqual({ success: 2500, failed: 0, created: 2500, updated: 0, failures: [] })
    const finalCall = onProgress.mock.calls[onProgress.mock.calls.length - 1]
    expect(finalCall).toEqual([2500, 2500])
  })

  it('returns only the still-failing subset, with the latest reason, after a partial retry', async () => {
    const post = vi.fn().mockResolvedValue({
      created: 1,
      updated: 0,
      failed: 1,
      errors: [{ index: 1, error: '仍然重复', record: {} }],
    })
    const failures = [makeFailure({ originalRecord: { name: 'a' } }), makeFailure({ originalRecord: { name: 'b' } })]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].originalRecord).toEqual({ name: 'b' })
    expect(result.failures[0].reason).toBe('仍然重复')
  })

  it('marks all as still-failing with an updated reason when the whole retry request throws', async () => {
    const post = vi.fn().mockRejectedValue(new Error('还是超时'))
    const failures = [makeFailure()]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].reason).toMatch(/请求失败.*还是超时.*可重试/)
  })
})
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: FAIL — `retryImportFailures is not a function`（尚未导出）

- [ ] **Step 3: 实现**

在 `src/utils/importPageRecords.ts` 文件末尾（`importPageRecords` 函数之后）新增：

```ts
/**
 * 重试一批之前失败的记录：直接重新提交它们已经就绪的请求体
 * （不重新解析关联/引用/序列号——那些在首次导入时已经算好并塞进了 payload），
 * 按与首次导入相同的 BATCH_SIZE/CONCURRENCY 重新分批提交。
 */
export async function retryImportFailures(
  post: <T>(url: string, body: any) => Promise<T>,
  collection: string,
  failures: ImportFailure[],
  onProgress?: (current: number, total: number) => void,
): Promise<ImportPageResult> {
  let success = 0, failed = 0, created = 0, updated = 0
  let completed = 0
  const remainingFailures: ImportFailure[] = []

  async function runBatch(batchIdx: number): Promise<void> {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, failures.length)
    const batchFailures = failures.slice(start, end)
    const batchData = batchFailures.map((f) => f.payload)

    try {
      const result = await post<{
        created: number
        updated?: number
        failed: number
        errors?: Array<{ index: number; error: string; validationErrors?: string[]; record: any }>
      }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
      if (result.errors && result.errors.length > 0) {
        for (const err of result.errors) {
          remainingFailures.push({
            originalRecord: batchFailures[err.index].originalRecord,
            payload: batchFailures[err.index].payload,
            reason: err.validationErrors && err.validationErrors.length > 0
              ? err.validationErrors.join('; ')
              : err.error,
          })
        }
      }
    } catch (error) {
      failed += batchFailures.length
      const message = error instanceof Error ? error.message : String(error)
      batchFailures.forEach((f) => {
        remainingFailures.push({
          originalRecord: f.originalRecord,
          payload: f.payload,
          reason: `请求失败：${message}，可重试`,
        })
      })
    }
    completed += batchFailures.length
    onProgress?.(completed, failures.length)
  }

  await runBatchesConcurrently(failures.length, runBatch)

  return { success, failed, created, updated, failures: remainingFailures }
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: PASS（全部用例）

- [ ] **Step 5: 提交**

```bash
git add src/utils/importPageRecords.ts src/utils/__tests__/importPageRecords.test.ts
git commit -m "feat(import): 新增 retryImportFailures，一键重放失败记录已就绪的请求体"
```

---

### Task 3: `excel.ts` — 导出失败清单文件

**Files:**
- Modify: `src/utils/excel.ts`
- Test: `src/utils/__tests__/excel.test.ts`

**Interfaces:**
- Consumes: `ImportFailure`（Task 1，从 `./importPageRecords` import type）、`getExportableFields`（已有）
- Produces: `function exportImportFailures(failures: ImportFailure[], fields: FieldConfig[], filename: string): void`

- [ ] **Step 1: 写失败测试**

在 `src/utils/__tests__/excel.test.ts` 顶部 import 里加上 `exportImportFailures`：

```ts
import {
  getExportableFields,
  exportToExcel,
  generateImportTemplate,
  exportImportFailures,
} from '../excel'
```

在 `describe('Excel Utils', ...)` 块内、`generateImportTemplate` 的 `describe` 之后新增：

```ts
  describe('exportImportFailures', () => {
    const mockWriteFile = vi.mocked(XLSX.writeFile)
    const mockAoatoSheet = vi.mocked(XLSX.utils.aoa_to_sheet)
    const mockBookAppendSheet = vi.mocked(XLSX.utils.book_append_sheet)

    beforeEach(() => {
      vi.clearAllMocks()
    })

    it('表头为字段 label 加末尾"失败原因"列，内容行取 originalRecord 的值', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]
      const failures = [
        { originalRecord: { name: '张三', age: 25 }, payload: { id: 'x', data: {}, relations: {} }, reason: '主键重复' },
      ]

      exportImportFailures(failures, fields, 'test-failures')

      const sheetArgs = mockAoatoSheet.mock.calls[0][0] as any[][]
      expect(sheetArgs[0]).toEqual(['名称', '年龄', '失败原因'])
      expect(sheetArgs[1]).toEqual(['张三', 25, '主键重复'])
      expect(mockBookAppendSheet).toHaveBeenCalled()
      expect(mockWriteFile).toHaveBeenCalledWith(expect.anything(), 'test-failures.xlsx')
    })

    it('originalRecord 里缺失的字段值导出为空字符串', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]
      const failures = [
        { originalRecord: {}, payload: { id: 'x', data: {}, relations: {} }, reason: '缺少必填字段' },
      ]

      exportImportFailures(failures, fields, 'test')

      const sheetArgs = mockAoatoSheet.mock.calls[0][0] as any[][]
      expect(sheetArgs[1]).toEqual(['', '缺少必填字段'])
    })

    it('数组值（如多选）用顿号拼接', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'tags', label: '标签', controlType: 'multiSelect', order: 1 }),
      ]
      const failures = [
        { originalRecord: { tags: ['a', 'b'] }, payload: { id: 'x', data: {}, relations: {} }, reason: '校验失败' },
      ]

      exportImportFailures(failures, fields, 'test')

      const sheetArgs = mockAoatoSheet.mock.calls[0][0] as any[][]
      expect(sheetArgs[1]).toEqual(['a、b', '校验失败'])
    })

    it('空 failures 只生成表头行', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
      ]

      exportImportFailures([], fields, 'test')

      const sheetArgs = mockAoatoSheet.mock.calls[0][0] as any[][]
      expect(sheetArgs).toEqual([['名称', '失败原因']])
    })
  })
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `npx vitest run src/utils/__tests__/excel.test.ts`
Expected: FAIL — `exportImportFailures is not a function`

- [ ] **Step 3: 实现**

在 `src/utils/excel.ts` 顶部 import 区加一行类型 import：

```ts
import type { ImportFailure } from './importPageRecords'
```

在 `generateImportTemplate` 函数结束之后（"导入文件解析"那段注释分隔线之前）新增：

```ts
/**
 * 导出导入失败清单：字段列（与原始导入表头一致）+ 末尾"失败原因"列。
 * 用户改完数据、删掉"失败原因"列，就是一份可以直接重新导入的文件。
 */
export function exportImportFailures(
  failures: ImportFailure[],
  fields: FieldConfig[],
  filename: string
): void {
  const exportFields = getExportableFields(fields)
  const headers = [...exportFields.map((f) => f.label), '失败原因']

  const rows = failures.map((failure) => [
    ...exportFields.map((field) => {
      const value = failure.originalRecord[field.fieldName]
      if (value === null || value === undefined) return ''
      return Array.isArray(value) ? value.join('、') : value
    }),
    failure.reason,
  ])

  const ws = XLSX.utils.aoa_to_sheet([headers, ...rows])
  ws['!cols'] = [
    ...exportFields.map((f) => ({ wch: Math.max(f.label.length * 2 + 4, 12) })),
    { wch: 40 },
  ]

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '失败记录')

  XLSX.writeFile(wb, `${filename}.xlsx`)
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `npx vitest run src/utils/__tests__/excel.test.ts`
Expected: PASS（全部用例）

- [ ] **Step 5: 提交**

```bash
git add src/utils/excel.ts src/utils/__tests__/excel.test.ts
git commit -m "feat(import): 新增 exportImportFailures，导出可直接重新导入的失败清单"
```

---

### Task 4: `DynamicPage.vue` — 导入结果面板接入导出/重试

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

**Interfaces:**
- Consumes: `ImportFailure`/`ImportPageResult`/`retryImportFailures`（`@/utils/importPageRecords`，Task 1&2）、`exportImportFailures`（`@/utils/excel`，Task 3）

此任务是纯 UI 接线（把已经单测覆盖的纯函数接到 Vue 组件上），本项目对 `DynamicPage.vue` 这类巨型 SFC 的既有测试约定是把可测逻辑抽成独立函数单测（见 `__tests__/DynamicPage.test.ts` 顶部注释"复刻 DynamicPage.xxx 的逻辑，用于单元测试"），而不是挂载整个组件；这里新增的胶水代码（合并计数、拼文件名）逻辑足够简单，不额外新增复刻测试，改为 Step 5 的 Playwright 人工验证兜底（这也是 CLAUDE.md 对 UI 改动的强制要求）。

- [ ] **Step 1: 更新 import 语句**

`src/views/dynamic/DynamicPage.vue:949-950` 两行由：

```ts
import { exportToExcel, generateImportTemplate, parseImportFile, parseJsonImportFile } from '@/utils/excel'
import { importPageRecords } from '@/utils/importPageRecords'
```

改为：

```ts
import { exportToExcel, generateImportTemplate, parseImportFile, parseJsonImportFile, exportImportFailures } from '@/utils/excel'
import { importPageRecords, retryImportFailures, type ImportPageResult } from '@/utils/importPageRecords'
```

- [ ] **Step 2: 扩展 `importResult` 的类型**

`src/views/dynamic/DynamicPage.vue:1207` 由：

```ts
const importResult = ref<{ success: number; failed: number; created?: number; updated?: number } | null>(null)
```

改为：

```ts
const importResult = ref<ImportPageResult | null>(null)
```

- [ ] **Step 3: `doImport` 保留完整结果（含 `failures`）**

`src/views/dynamic/DynamicPage.vue` 里的 `doImport` 函数（约 2837-2861 行）由：

```ts
async function doImport(records: Record<string, any>[]): Promise<void> {
  importResult.value = null
  importPhase.value = 'uploading'
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

改为：

```ts
async function doImport(records: Record<string, any>[]): Promise<void> {
  importResult.value = null
  importPhase.value = 'uploading'
  importLoading.value = true
  importProgress.value = 0
  importCurrent.value = 0
  importTotal.value = records.length
  importDialogVisible.value = true

  const result = await importPageRecords({
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
  importResult.value = result
  if (result.success > 0) await loadPageData()
}

/**
 * 重试当前导入结果里仍失败的记录：直接重放已就绪的请求体，不重新解析文件/关联。
 */
async function handleRetryImportFailures(): Promise<void> {
  const prev = importResult.value
  if (!prev || prev.failures.length === 0) return

  const retryTargets = prev.failures
  importPhase.value = 'uploading'
  importLoading.value = true
  importProgress.value = 0
  importCurrent.value = 0
  importTotal.value = retryTargets.length

  const retryResult = await retryImportFailures(post, collection.value, retryTargets, (current, total) => {
    importCurrent.value = current
    importProgress.value = Math.round((current / total) * 100)
  })

  importLoading.value = false
  importResult.value = {
    success: prev.success + retryResult.success,
    created: prev.created + retryResult.created,
    updated: prev.updated + retryResult.updated,
    failed: retryResult.failed,
    failures: retryResult.failures,
  }
  if (retryResult.success > 0) await loadPageData()
}

/**
 * 导出当前导入结果里仍失败的记录：字段列 + 失败原因列，删掉原因列即可重新导入。
 */
function handleExportImportFailures(): void {
  const failures = importResult.value?.failures
  if (!failures || failures.length === 0) return

  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const name = pageConfig.value?.name || '数据'

  exportImportFailures(failures, pageFields.value, `导入失败记录_${name}_${ts}`)
}
```

- [ ] **Step 4: 模板 — 结果面板挂上导出/重试按钮**

`src/views/dynamic/DynamicPage.vue:865-880` 由：

```html
      <div v-else-if="importResult" class="import-result">
        <el-result
          :icon="importResult.failed === 0 ? 'success' : 'warning'"
          :title="importResult.failed === 0 ? '导入完成' : '导入完成（部分失败）'"
        >
          <template #sub-title>
            <p>
              成功 {{ importResult.success }} 条
              <template v-if="importResult.updated">
                （新增 {{ importResult.created || 0 }} / 更新 {{ importResult.updated }}）
              </template>
              ,失败 {{ importResult.failed }} 条
            </p>
          </template>
        </el-result>
      </div>
```

改为：

```html
      <div v-else-if="importResult" class="import-result">
        <el-result
          :icon="importResult.failed === 0 ? 'success' : 'warning'"
          :title="importResult.failed === 0 ? '导入完成' : '导入完成（部分失败）'"
        >
          <template #sub-title>
            <p>
              成功 {{ importResult.success }} 条
              <template v-if="importResult.updated">
                （新增 {{ importResult.created || 0 }} / 更新 {{ importResult.updated }}）
              </template>
              ,失败 {{ importResult.failed }} 条
            </p>
          </template>
          <template v-if="importResult.failed > 0" #extra>
            <el-button @click="handleExportImportFailures">导出失败清单</el-button>
            <el-button type="primary" @click="handleRetryImportFailures">重试失败记录</el-button>
          </template>
        </el-result>
      </div>
```

- [ ] **Step 5: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: 无新增类型错误（确认 `ImportPageResult`/`retryImportFailures`/`exportImportFailures` 的类型在 `DynamicPage.vue` 里用法一致，`prev.created`/`prev.updated` 不再是可选字段——因为 `ImportPageResult` 里两者都是必填 `number`，与旧的 `{ created?: number; updated?: number }` 不同，赋值处不需要 `|| 0` 兜底）

- [ ] **Step 6: 现有前端测试回归**

Run: `npx vitest run`
Expected: PASS（全部现有测试，包括 Task 1-3 新增的）

- [ ] **Step 7: 提交**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(import): 导入结果面板支持导出失败清单与一键重试"
```

---

### Task 5: Playwright 人工验证（CLAUDE.md 强制要求）

本任务不产出代码改动，只验证 Task 1-4 的真实效果——本项目规则要求任何涉及前端页面的改动在合并前必须用 Playwright 走一遍真实流程，单测和类型检查不能替代。

**Files:** 无（验证性任务）

- [ ] **Step 1: 准备一批会失败的测试数据**

选一个已配置主键字段（`pk_fields`）的页面（例如现有 `products` 页/`page-products`，其主键字段是"产品编号"），准备一份导入文件：多数行是新数据，其中至少 1-2 行的主键与库里已有记录重复（触发 `check_primary_key_unique` 失败）——不需要真的 20 万行，几十行即可复现"部分失败"这个场景。

- [ ] **Step 2: 启动服务**

Run: `npm run dev:all`（或分别 `npm run server` + `npm run dev`），确认后端已加载 Task 1-4 的改动（前端 Vite HMR 即可，后端本次未改动、无需重启）。

- [ ] **Step 3: 用 Playwright MCP 走一遍真实导入流程**

登录 `admin`/`admin123` → 打开目标数据页 → 「导入数据」→ 选择 Step 1 准备的文件 → 等待导入完成弹窗显示"导入完成（部分失败）"。

- [ ] **Step 4: 验证导出失败清单**

点击「导出失败清单」，确认浏览器触发下载；打开下载的 `.xlsx`，确认：列结构是字段 label + 末尾"失败原因"列；失败原因列内容与预期的主键重复提示一致；行数与弹窗显示的失败数一致。

- [ ] **Step 5: 验证重试失败记录**

点击「重试失败记录」，确认进度条按"正在导入... X / Y"展示（`Y` 等于失败数，不是原始总数）；由于这批记录本身是"主键已存在"，重试后应仍然失败（预期行为——数据没改，重试当然还是重复）；确认失败数保持不变、按钮仍然可见（未误判为成功）。

- [ ] **Step 6: 验证"改完数据后重试能成功"这条主线**

回到数据库/页面，把其中一条失败记录的主键改成不冲突的值（不通过重新导入，直接用页面的「编辑」把冲突的已存在记录删掉或改名，腾出主键位）；再次点击「重试失败记录」；确认该条这次成功、失败数相应减少；确认成功导入的记录能在列表里查到（`loadPageData()` 有被触发刷新)。

- [ ] **Step 7: 截图存档**

用 Playwright 截图存到 `.playwright-mcp/`，覆盖：导入完成（部分失败）弹窗、导出的 xlsx 文件内容、重试后计数更新的弹窗。

- [ ] **Step 8: 记录验证结论**

在对话里明确说明：三个场景（首次部分失败 → 导出清单 → 重试仍失败 → 改数据后重试成功）均已通过 Playwright 走通，且截图证据已保存路径。
