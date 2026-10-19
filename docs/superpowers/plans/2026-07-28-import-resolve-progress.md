# 导入关联/引用字段解析：进度提示 + 分片让出主线程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据导入里"解析关联/引用字段"这一步（目前完全在主线程、不分片、无进度反馈）改为分片让出主线程，并在导入进度弹窗里新增一个可见阶段"正在解析关联/引用字段... X/Y"。

**Architecture:** `src/stores/pageConfig.ts` 的四个 resolve 函数各自的记录级循环改为按 `RESOLVE_CHUNK_SIZE` 分片 + `yieldToMain()`，并新增 `onChunkProcessed` 回调；`src/utils/importPageRecords.ts` 把原来并发（`Promise.all`）调用这四个函数改为顺序调用，汇总进度通过新增的 `onResolveProgress` 回调上抛；`src/views/dynamic/DynamicPage.vue` 用一个本地状态（不改动已审查完成的 `useMultiFileImport` composable）在进度弹窗里显示这个新阶段。

**Tech Stack:** TypeScript + Pinia + Vitest + Playwright。

## Global Constraints

- **不改动** `src/composables/useMultiFileImport.ts`（已审查完成、生产使用中，`FileImportStatus`/`MultiFileImportState`/`currentPhase` 类型保持不变）。
- **不改动** `resolveRelationImportValues`/`resolveReferenceImportValues`/`resolveQuoteImportValues`/`resolveCollectionSelectImportValues` 的解析结果/正确性行为——只加分片和进度回调，四个函数原有的全部单元测试必须保持全部通过、不修改断言。
- **`onChunkProcessed`/`onResolveProgress` 均为可选参数**，不传时行为与改动前完全一致（各函数可以被不传最后一个参数地调用，不报错、不分片行为异常——分片本身不依赖回调是否存在）。
- **`resolveFieldCount === 0` 时完全不触发 `onResolveProgress`**，也不应该让 UI 出现"正在解析关联/引用字段"这个阶段的任何闪烁。
- **四个 resolve 函数从并发改为顺序 `await`**（`importPageRecords` 内部），这是本次刻意的行为变更，不是意外——为了能在开始前算出稳定的 `resolveTotal`。
- **`retryImportFailures` 不受影响**：它不调用任何 resolve 函数（复用首次导入时已解析好的 payload），本次不touch。
- CLAUDE.md 强制项：本次改动会影响导入进度弹窗（UI 可见），改动完成后须用 Playwright 现场验证。

---

### Task 1: `src/stores/pageConfig.ts` — 分片 + `getResolveFieldCount`

**Files:**
- Modify: `src/stores/pageConfig.ts`
- Modify: `src/stores/__tests__/pageConfig.test.ts`

**Interfaces:**
- Produces（Task 2 依赖）：
  ```typescript
  function getResolveFieldCount(pageId: string): number

  // 以下四个函数新增第 4 个可选参数，其余签名/行为不变：
  function resolveRelationImportValues(
    pageId: string, records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void>

  function resolveReferenceImportValues(
    pageId: string, records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void>

  function resolveQuoteImportValues(
    pageId: string, records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void>

  function resolveCollectionSelectImportValues(
    pageId: string, records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void>
  ```

- [ ] **Step 1: Write the failing tests**

在 `src/stores/__tests__/pageConfig.test.ts` 末尾（文件最后一个 `describe` 块之后）追加：

```typescript
describe('PageConfig Store — getResolveFieldCount', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('returns 0 when the page has no relation-type fields', () => {
    store.$patch({
      pageConfigs: [
        makePageConfig({ id: 'page-test', fields: [makeField({ id: 'f1', fieldName: 'name', controlType: 'text' })] }),
      ],
    })
    expect(store.getResolveFieldCount('page-test')).toBe(0)
  })

  it('counts relation + reference + quoteSelect + collection-sourced select fields', () => {
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'rel', controlType: 'relation', relationConfig: { targetCollection: 'a', displayField: 'name', targetField: 'back' } }),
            makeField({ id: 'f2', fieldName: 'ref', controlType: 'reference', referenceConfig: { targetCollection: 'b', displayField: 'name', inheritFields: [] } }),
            makeField({ id: 'f3', fieldName: 'quo', controlType: 'quoteSelect', quoteConfig: { targetCollection: 'c', displayField: 'name' } }),
            makeField({ id: 'f4', fieldName: 'sel', controlType: 'select', optionsSource: { type: 'collection', collection: 'd', labelField: 'name', valueField: 'id' } }),
          ],
        }),
      ],
    })
    expect(store.getResolveFieldCount('page-test')).toBe(4)
  })

  it('does not count fields whose config is missing/incomplete', () => {
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [
            makeField({ id: 'f1', fieldName: 'rel', controlType: 'relation' }), // no relationConfig
            makeField({ id: 'f2', fieldName: 'ref', controlType: 'reference', referenceConfig: {} as any }), // no targetCollection
            makeField({ id: 'f3', fieldName: 'quo', controlType: 'quoteSelect' }), // no quoteConfig
            makeField({ id: 'f4', fieldName: 'sel', controlType: 'select' }), // no optionsSource
          ],
        }),
      ],
    })
    expect(store.getResolveFieldCount('page-test')).toBe(0)
  })
})

describe('PageConfig Store — resolve import values chunking/progress', () => {
  let store: ReturnType<typeof usePageConfigStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = usePageConfigStore()
  })

  it('resolveRelationImportValues reports chunked progress that sums to records.length', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'rel', controlType: 'relation', relationConfig: { targetCollection: 'items', displayField: 'name', targetField: 'back' } })],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({ data: [{ id: 'item-1', name: 'A' }], total: 1 })

    // 记录数刻意超过 RESOLVE_CHUNK_SIZE（5000），验证真的分了多片
    const records = Array.from({ length: 12000 }, () => ({ rel: ['A'] }))
    const onChunkProcessed = vi.fn()

    await store.resolveRelationImportValues('page-test', records, undefined, onChunkProcessed)

    expect(onChunkProcessed).toHaveBeenCalledTimes(3) // 5000 + 5000 + 2000
    const total = onChunkProcessed.mock.calls.reduce((sum, [n]) => sum + n, 0)
    expect(total).toBe(12000)
    // 分片不影响解析结果
    expect(records[0].rel).toEqual(['item-1'])
    expect(records[11999].rel).toEqual(['item-1'])
  })

  it('resolveReferenceImportValues works correctly when onChunkProcessed is omitted', async () => {
    const mockedGet = vi.mocked(get)
    mockedGet.mockReset()
    store.$patch({
      pageConfigs: [
        makePageConfig({
          id: 'page-test',
          fields: [makeField({ id: 'f1', fieldName: 'ref', controlType: 'reference', referenceConfig: { targetCollection: 'items', displayField: 'name', inheritFields: [] } })],
        }),
      ],
    })
    mockedGet.mockResolvedValueOnce({ data: [{ id: 'item-1', name: 'A' }], total: 1 })

    const records = [{ ref: 'A' }]
    await store.resolveReferenceImportValues('page-test', records) // 不传 onChunkProcessed
    expect(records[0].ref).toBe('item-1')
  })

  it('resolveQuoteImportValues does not call onChunkProcessed when there are no qualifying fields', async () => {
    store.$patch({
      pageConfigs: [makePageConfig({ id: 'page-test', fields: [] })],
    })
    const onChunkProcessed = vi.fn()
    await store.resolveQuoteImportValues('page-test', [{ n: 1 }], undefined, onChunkProcessed)
    expect(onChunkProcessed).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts`
Expected: FAIL — `store.getResolveFieldCount is not a function`（新方法还不存在），以及 resolve 系列测试因为 `onChunkProcessed` 参数被忽略而调用次数为 0。

- [ ] **Step 3: Implement**

在 `resolveQuoteImportValues` 函数定义之前（大约当前第 106 行，紧接着 `check_primary_key_unique`/其他 helper 之后、`getRelationFields` 之前的合适位置——放在文件里第一次用到它的函数附近即可，不强制具体行号）新增：

```typescript
  /** 每处理这么多条记录后让出一次主线程，避免同步循环卡住页面交互。 */
  const RESOLVE_CHUNK_SIZE = 5000

  function yieldToMain(): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, 0))
  }

  /**
   * 统计参与导入解析的关联类字段数（relation/reference/quoteSelect/来自集合的
   * select 类字段），用于 importPageRecords 预估解析阶段的总步数
   * （resolveFieldCount × records.length）。过滤条件必须和
   * resolveRelationImportValues/resolveReferenceImportValues/
   * resolveQuoteImportValues/resolveCollectionSelectImportValues 各自的
   * "参与条件"保持一致，否则进度总数会跟实际处理步数对不上。
   */
  function getResolveFieldCount(pageId: string): number {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return 0
    const relationCount = getRelationFields(pageId).filter((f) => f.relationConfig).length
    const referenceCount = getReferenceFields(pageId).filter((f) => f.referenceConfig?.targetCollection).length
    const quoteCount = getQuoteFields(pageId).filter((f) => f.quoteConfig).length
    const collectionSelectCount = config.fields.filter(
      (f) =>
        ['select', 'multiSelect', 'radio', 'checkbox'].includes(f.controlType) &&
        f.optionsSource?.type === 'collection' &&
        f.optionsSource?.collection
    ).length
    return relationCount + referenceCount + quoteCount + collectionSelectCount
  }
```

`getResolveFieldCount` 引用了 `getRelationFields`/`getReferenceFields`/`getQuoteFields`，这三个函数在文件里定义得比较靠后（分别在第 832/881/890 行附近）；由于都是同一个 `defineStore` setup 函数作用域内的普通函数声明（`function` 声明会被提升，不是箭头函数/`const`），不需要调整声明顺序，直接在任意位置调用都可以——跟这个 store 里其他函数互相调用的现有写法一致。

**修改 `resolveRelationImportValues`**（当前第 1105-1155 行附近）——把内层记录循环换成分片，新增最后一个参数：

```typescript
  async function resolveRelationImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const relationFields = getRelationFields(pageId)
    if (relationFields.length === 0) return

    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      // 构建查找表
      const pkToId = new Map<string, string>()
      const displayToId = new Map<string, string>()
      const idSet = new Set<string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) pkToId.set(String(pkVal), r.id)
        }
        if (config.displayField) {
          const displayVal = r[config.displayField]
          if (displayVal) displayToId.set(String(displayVal), r.id)
        }
      }

      // 解析每条导入记录的关联值（按 RESOLVE_CHUNK_SIZE 分片，避免大批量时卡住主线程）
      for (let start = 0; start < records.length; start += RESOLVE_CHUNK_SIZE) {
        const end = Math.min(start + RESOLVE_CHUNK_SIZE, records.length)
        for (let i = start; i < end; i++) {
          const record = records[i]
          const vals = record[field.fieldName]
          if (!Array.isArray(vals) || vals.length === 0) continue
          record[field.fieldName] = vals
            .map((v: string) => {
              if (idSet.has(v)) return v            // 已经是内部 ID
              return pkToId.get(v) || displayToId.get(v) || null
            })
            .filter((v: string | null): v is string => v !== null)
        }
        onChunkProcessed?.(end - start)
        if (end < records.length) await yieldToMain()
      }
    }
  }
```

**修改 `resolveReferenceImportValues`**（当前第 1165-1213 行附近）——同样只改内层记录循环：

```typescript
  async function resolveReferenceImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const referenceFields = getReferenceFields(pageId)
    if (referenceFields.length === 0) return

    for (const field of referenceFields) {
      const config = field.referenceConfig
      if (!config?.targetCollection) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      // 构建查找表
      const idSet = new Set<string>()
      const pkToId = new Map<string, string>()
      const displayToId = new Map<string, string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) pkToId.set(String(pkVal), r.id)
        }
        const displayVal = r[config.displayField]
        if (displayVal) displayToId.set(String(displayVal), r.id)
      }

      // 解析每条导入记录（分片）
      for (let start = 0; start < records.length; start += RESOLVE_CHUNK_SIZE) {
        const end = Math.min(start + RESOLVE_CHUNK_SIZE, records.length)
        for (let i = start; i < end; i++) {
          const record = records[i]
          const val = record[field.fieldName]
          if (!val || val === '') continue
          const strVal = String(val)
          if (idSet.has(strVal)) continue                             // 已经是内部 ID
          const resolved = pkToId.get(strVal) || displayToId.get(strVal)
          if (resolved) {
            record[field.fieldName] = resolved
          }
        }
        onChunkProcessed?.(end - start)
        if (end < records.length) await yieldToMain()
      }
    }
  }
```

**修改 `resolveQuoteImportValues`**（当前第 1253-1323 行附近）——同样只改"解析每条导入记录"的那个循环：

```typescript
  async function resolveQuoteImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const quoteFields = getQuoteFields(pageId)
    if (quoteFields.length === 0) return

    for (const field of quoteFields) {
      const config = field.quoteConfig
      if (!config) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      const pkToIds = new Map<string, string[]>()
      const displayToIds = new Map<string, string[]>()
      const idSet = new Set<string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) {
            const key = String(pkVal)
            const arr = pkToIds.get(key)
            if (arr) arr.push(r.id)
            else pkToIds.set(key, [r.id])
          }
        }
        if (config.displayField) {
          const displayVal = r[config.displayField]
          if (displayVal) {
            const key = String(displayVal)
            const arr = displayToIds.get(key)
            if (arr) arr.push(r.id)
            else displayToIds.set(key, [r.id])
          }
        }
      }

      // 解析每条导入记录（分片）
      for (let start = 0; start < records.length; start += RESOLVE_CHUNK_SIZE) {
        const end = Math.min(start + RESOLVE_CHUNK_SIZE, records.length)
        for (let i = start; i < end; i++) {
          const record = records[i]
          const vals = record[field.fieldName]
          if (!Array.isArray(vals) || vals.length === 0) continue
          const seen = new Set<string>()
          const resolved: string[] = []
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
          record[field.fieldName] = resolved
        }
        onChunkProcessed?.(end - start)
        if (end < records.length) await yieldToMain()
      }
    }
  }
```

**修改 `resolveCollectionSelectImportValues`**（当前第 1335-1415 行附近）——只改"解析每条记录的选项值"这个循环（不是自引用预生成 `_importId` 的那个循环，那个保持原样不动、不分片、不计入进度）：

```typescript
  async function resolveCollectionSelectImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>,
    onChunkProcessed?: (n: number) => void
  ): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return

    const cache = collectionCache || new Map<string, any[]>()
    const collectionSelectFields = config.fields.filter(
      (f) =>
        ['select', 'multiSelect', 'radio', 'checkbox'].includes(f.controlType) &&
        f.optionsSource?.type === 'collection' &&
        f.optionsSource?.collection
    )
    if (collectionSelectFields.length === 0) return

    const endpoint = pageId.replace('page-', '')

    for (const field of collectionSelectFields) {
      const source = field.optionsSource!
      const collection = source.collection!
      const labelField = source.labelField || 'id'
      const valueField = source.valueField || 'id'

      // 获取目标集合的已有记录
      const targetRecords = await fetchCollectionData(collection, cache)

      // 构建映射: labelField 值 → valueField 值
      const labelToVal = new Map<string, any>()
      for (const r of targetRecords) {
        const label = String(r[labelField] ?? '')
        const value = r[valueField] ?? r.id
        if (label) labelToVal.set(label, value)
      }

      // 自引用：将正在导入的记录也加入映射（不分片——通常量级不大，本次不优化）
      const isSelfReferencing = collection === endpoint
      if (isSelfReferencing) {
        for (const record of records) {
          const label = String(record[labelField] ?? '')
          if (!label) continue

          if (valueField === 'id') {
            // valueField 为 id 时需要预生成 ID
            if (!record._importId) {
              record._importId = `${endpoint}-${uuidv4().slice(0, 8)}`
            }
            labelToVal.set(label, record._importId)
          } else {
            const value = record[valueField]
            if (value !== undefined && value !== null && value !== '') {
              labelToVal.set(label, value)
            }
          }
        }
      }

      // 解析每条记录的选项值（分片）
      for (let start = 0; start < records.length; start += RESOLVE_CHUNK_SIZE) {
        const end = Math.min(start + RESOLVE_CHUNK_SIZE, records.length)
        for (let i = start; i < end; i++) {
          const record = records[i]
          const val = record[field.fieldName]
          if (val === null || val === undefined || val === '') continue

          if (['select', 'radio'].includes(field.controlType)) {
            // 单选：直接查找映射
            const resolved = labelToVal.get(String(val))
            if (resolved !== undefined) {
              record[field.fieldName] = resolved
            }
          } else if (['multiSelect', 'checkbox'].includes(field.controlType)) {
            // 多选：逐个映射
            if (Array.isArray(val)) {
              record[field.fieldName] = val.map((v: any) => {
                const resolved = labelToVal.get(String(v))
                return resolved !== undefined ? resolved : v
              })
            }
          }
        }
        onChunkProcessed?.(end - start)
        if (end < records.length) await yieldToMain()
      }
    }
  }
```

最后，在 store 的 `return { ... }` 导出块（当前第 1554 行开始）里，找到 `resolveCollectionSelectImportValues,`（当前第 1598 行附近）那一行，紧接着加：

```typescript
    getResolveFieldCount,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/stores/__tests__/pageConfig.test.ts`
Expected: 全部通过，包括改动前就存在的所有 `resolveRelationImportValues`/`resolveReferenceImportValues`/`resolveQuoteImportValues`/`resolveCollectionSelectImportValues` 测试（它们都不传第 4 个参数，行为必须和改动前完全一致）。

- [ ] **Step 5: Commit**

```bash
git add src/stores/pageConfig.ts src/stores/__tests__/pageConfig.test.ts
git commit -m "feat(import): 关联/引用字段解析改为分片执行，新增 getResolveFieldCount"
```

---

### Task 2: `src/utils/importPageRecords.ts` — 串行调用 + `onResolveProgress`

**Files:**
- Modify: `src/utils/importPageRecords.ts`
- Modify: `src/utils/__tests__/importPageRecords.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `getResolveFieldCount`、四个 resolve 函数新增的 `onChunkProcessed` 参数
- Produces（Task 3 依赖）：`ImportPageParams` 新增可选字段 `onResolveProgress?: (current: number, total: number) => void`

- [ ] **Step 1: Write the failing tests**

`src/utils/__tests__/importPageRecords.test.ts` 现有的 `makeStore()` 辅助函数（文件顶部，第 5-17 行）**必须**加上 `getResolveFieldCount` 的默认 mock——否则新代码里 `store.getResolveFieldCount(pageId)` 会在所有现有测试里因为 mock 对象没有这个方法而抛错。默认值定为 `1`（保持"默认会调用四个 resolve 函数"这个现有测试们已经依赖的行为，语义上代表"该页面有关联类字段"）：

```typescript
function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    getResolveFieldCount: vi.fn().mockReturnValue(1),
    batchGenerateSequenceValues: vi.fn().mockReturnValue({}),
    stripRelationFields: vi.fn((_p: string, r: any) => ({ ...r })),
    getRelationFields: vi.fn().mockReturnValue([]),
    getPrimaryKeyFields: vi.fn().mockReturnValue([]),
    ...overrides,
  }
}
```

在文件末尾（`describe('importPageRecords', ...)` 块内，跟其他 `it(...)` 平级）追加：

```typescript
  it('calls the four resolve functions sequentially, not concurrently', async () => {
    const callOrder: string[] = []
    const store = makeStore({
      resolveRelationImportValues: vi.fn(async () => { callOrder.push('start:relation'); await new Promise((r) => setTimeout(r, 5)); callOrder.push('end:relation') }),
      resolveReferenceImportValues: vi.fn(async () => { callOrder.push('start:reference'); callOrder.push('end:reference') }),
      resolveQuoteImportValues: vi.fn(async () => { callOrder.push('start:quote'); callOrder.push('end:quote') }),
      resolveCollectionSelectImportValues: vi.fn(async () => { callOrder.push('start:collectionSelect'); callOrder.push('end:collectionSelect') }),
    })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }] })

    expect(callOrder).toEqual([
      'start:relation', 'end:relation',
      'start:reference', 'end:reference',
      'start:quote', 'end:quote',
      'start:collectionSelect', 'end:collectionSelect',
    ])
  })

  it('reports aggregated resolve progress across chunk callbacks from all four functions', async () => {
    const store = makeStore({
      getResolveFieldCount: vi.fn().mockReturnValue(2), // 2 个参与字段（比如 1 个 relation + 1 个 reference）
      resolveRelationImportValues: vi.fn(async (_p, records, _c, onChunkProcessed) => {
        onChunkProcessed?.(records.length)
      }),
      resolveReferenceImportValues: vi.fn(async (_p, records, _c, onChunkProcessed) => {
        onChunkProcessed?.(records.length)
      }),
    })
    const post = vi.fn().mockResolvedValue({ created: 3, updated: 0, failed: 0 })
    const onResolveProgress = vi.fn()
    const records = [{ name: 'a' }, { name: 'b' }, { name: 'c' }]

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records, onResolveProgress })

    // total = resolveFieldCount(2) * records.length(3) = 6；relation 报 3、reference 再报 3 → 累计 3 后 6
    expect(onResolveProgress).toHaveBeenNthCalledWith(1, 3, 6)
    expect(onResolveProgress).toHaveBeenNthCalledWith(2, 6, 6)
  })

  it('does not call onResolveProgress when the page has no resolve-eligible fields', async () => {
    const store = makeStore({ getResolveFieldCount: vi.fn().mockReturnValue(0) })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const onResolveProgress = vi.fn()

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }], onResolveProgress })

    expect(onResolveProgress).not.toHaveBeenCalled()
    // resolveFieldCount === 0 时四个 resolve 函数也完全不必调用
    expect(store.resolveRelationImportValues).not.toHaveBeenCalled()
    expect(store.resolveReferenceImportValues).not.toHaveBeenCalled()
    expect(store.resolveQuoteImportValues).not.toHaveBeenCalled()
    expect(store.resolveCollectionSelectImportValues).not.toHaveBeenCalled()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: FAIL —— 顺序性测试因为当前是 `Promise.all`（并发）而不是顺序，`callOrder` 不会是断言的交错顺序；`onResolveProgress` 相关两个测试因为 `ImportPageParams` 还没有这个字段、且调用没有走新逻辑而失败；已有测试可能因为 `makeStore()` 新增了 `getResolveFieldCount` 字段而暂时无影响（该字段此时还未被生产代码读取）。

- [ ] **Step 3: Implement**

`src/utils/importPageRecords.ts` 顶部 `ImportPageParams` 接口（当前第 17-24 行）新增一个字段：

```typescript
export interface ImportPageParams {
  store: any // pageConfig store（解析/序列/关联方法）
  post: <T>(url: string, body: any) => Promise<T> // 注入 @/utils/request 的 post
  pageId: string
  collection: string
  records: Record<string, any>[]
  onProgress?: (current: number, total: number) => void
  onResolveProgress?: (current: number, total: number) => void
}
```

`importPageRecords` 函数体内（当前第 56-57 行解构参数处）加上 `onResolveProgress`：

```typescript
export async function importPageRecords(params: ImportPageParams): Promise<ImportPageResult> {
  const { store, post, pageId, collection, records, onProgress, onResolveProgress } = params
  const collectionCache = new Map<string, any[]>()
```

把当前（第 80-85 行附近）：

```typescript
  await Promise.all([
    store.resolveRelationImportValues(pageId, records, collectionCache),
    store.resolveReferenceImportValues(pageId, records, collectionCache),
    store.resolveQuoteImportValues(pageId, records, collectionCache),
    store.resolveCollectionSelectImportValues(pageId, records, collectionCache),
  ])
```

替换为：

```typescript
  const resolveFieldCount = store.getResolveFieldCount(pageId)
  if (resolveFieldCount > 0) {
    const resolveTotal = resolveFieldCount * records.length
    let resolveProcessed = 0
    const onChunkProcessed = (n: number): void => {
      resolveProcessed += n
      onResolveProgress?.(resolveProcessed, resolveTotal)
    }
    // 串行执行（而不是 Promise.all）：这样才能在开始前用一个稳定的 resolveTotal
    // 报告有意义的百分比；顺带消除了不同字段引用同一个尚未缓存的目标集合时，
    // 并发下可能重复发起 fetch 的小问题。
    await store.resolveRelationImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveReferenceImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveQuoteImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveCollectionSelectImportValues(pageId, records, collectionCache, onChunkProcessed)
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/utils/__tests__/importPageRecords.test.ts`
Expected: 全部通过，包括改动前已存在的全部测试（`makeStore()` 新增的默认 `getResolveFieldCount: 1` 保证它们的行为跟改动前一致）。

- [ ] **Step 5: Commit**

```bash
git add src/utils/importPageRecords.ts src/utils/__tests__/importPageRecords.test.ts
git commit -m "feat(import): resolve 阶段串行执行并上抛聚合进度"
```

---

### Task 3: `DynamicPage.vue` 接入 + Playwright 现场验证

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

**Interfaces:**
- Consumes: Task 2 的 `ImportPageParams.onResolveProgress`

#### Step 1: 新增本地状态

在 `<script setup>` 里，紧邻现有的 `importElapsedSeconds`/`importElapsedTimer`（多文件导入功能引入的那部分状态，搜索 `importElapsedSeconds` 定位）附近新增：

```typescript
/**
 * 关联/引用字段解析阶段的进度（导入的隐藏子阶段）。resolvingActive 为
 * true 时，进度弹窗把"正在导入..."文案换成"正在解析关联/引用字段..."；
 * 一旦真正的上传进度（onProgress）开始回调，立即置回 false。
 * 有意不放进 useMultiFileImport 的 composable state——那个 composable
 * 已经过审查并在生产使用，这里只是 DynamicPage 自己的 UI 细节。
 */
const resolvingActive = ref(false)
const resolvingProgress = ref({ current: 0, total: 0 })
```

#### Step 2: 接入 `uploadImportedRecords`

找到 `uploadImportedRecords` 函数（多文件导入功能里新增的，包一层 `importPageRecords` 调用），改为：

```typescript
async function uploadImportedRecords(
  records: Record<string, any>[],
  onProgress: (current: number, total: number) => void,
) {
  resolvingActive.value = false
  resolvingProgress.value = { current: 0, total: 0 }
  return importPageRecords({
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
}
```

#### Step 3: 模板改动

在导入进度弹窗模板里，找到 `multiImport.state.currentPhase === 'parsing'` 的 `<template v-if>` 块之后紧跟着的 `<p v-else>正在导入... ...</p>`（多文件导入功能引入的），改为：

```html
        <template v-else>
          <p v-if="resolvingActive">
            正在解析关联/引用字段... {{ resolvingProgress.current }} / {{ resolvingProgress.total }}
          </p>
          <p v-else>
            正在导入... {{ multiImport.state.currentProgress.current }} / {{ multiImport.state.currentProgress.total }}
          </p>
        </template>
```

（`el-progress` 的百分比在"正在解析关联/引用字段"这段时间会停在 0%——跟既有"正在读取文件结构...已用时 N 秒"阶段的表现完全一致，是有意保持的既有模式，不是新的不一致。）

#### Step 4: 类型检查 + 现有测试

- [ ] Run: `npx vue-tsc --noEmit` → 期望 0 错误
- [ ] Run: `npx vitest run src/views/dynamic/__tests__/ src/composables/__tests__/ src/stores/__tests__/pageConfig.test.ts src/utils/__tests__/importPageRecords.test.ts` → 期望全部通过

#### Step 5: Playwright 现场验证（CLAUDE.md 强制项）

1. 确保后端加载了最新代码（改动集中在前端，Vite HMR 即可；如不确定，按 CLAUDE.md 流程重启）。
2. 准备一个配置了至少一个 relation 或 reference 字段的真实数据页，且该字段指向的目标集合有一些数据；准备一个记录数明显超过 5000（`RESOLVE_CHUNK_SIZE`）的导入文件（比如 8000-15000 行，关联字段的值可以是能匹配到目标集合的显示值）。
3. 用 Playwright 触发导入，观察进度弹窗依次显示：
   - "正在解析文件... X/Y"
   - **新增**："正在解析关联/引用字段... X/Y"（应该能看到数字在变化，不是卡住不动）
   - "正在导入... X/Y"
4. 用另一个**没有任何**关联类字段的数据页做一次普通导入，确认"正在解析关联/引用字段"这个阶段完全不出现（从"正在解析文件"直接跳到"正在导入"）。
5. 验证最终数据正确写入（关联字段被正确解析成内部 ID），与改动前行为一致（可以对比同一份文件在改动前后的写入结果，或直接检查写入记录的关联字段值确实是内部 id 而不是原始显示值）。
6. 截图关键状态（"正在解析关联/引用字段"那一帧）保存证据，说明验证了什么。
7. 清理验证产生的测试数据。

- [ ] **Step 6: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(import): 导入进度弹窗新增「正在解析关联/引用字段」阶段"
```
