# 导入关联/引用字段解析：进度提示 + 分片让出主线程 — Design Spec

## Background

数据导入（`导入数据` → 解析文件 → 上传）里，"解析文件"这一步已经在 Web
Worker 里跑、且按行分片（见 `src/workers/excelImportWorker.ts`），大文件不会
冻结页面。但解析完成后、真正批量写库之前，还有一段**关联/引用字段解析**：

- `src/utils/importPageRecords.ts::importPageRecords` 并发调用
  `store.resolveRelationImportValues`/`resolveReferenceImportValues`/
  `resolveQuoteImportValues`/`resolveCollectionSelectImportValues`
  （均定义于 `src/stores/pageConfig.ts`）。
- 每个函数对每个符合条件的字段：`await fetchCollectionData(targetCollection)`
  （`all=true`，把目标集合全量拉到内存）→ 建查找 Map → 对**当前导入的全部
  N 条记录**做一次同步 `for` 循环做值替换。
- 这个同步循环完全在主线程跑、不分片、不 yield，导入进度条在这一步也没有
  任何反馈（从"正在解析文件"直接跳到"正在导入"，中间这段时间对用户不可见）。
- 对有 relation/reference/quoteSelect/优先来自集合的 select 字段的页面，
  大文件导入时这段隐藏的主线程工作可能明显卡顿。

本次范围：只解决"这段循环无反馈、不分片"的问题。**不**处理
`fetchCollectionData` 本身（一次性拉取目标集合全量数据）的开销——那是单次
网络请求 + 浏览器原生 `JSON.parse`，量级上通常远小于我们自己写的 JS 循环，
且不易分片（跟 Excel 解析阶段"读取文件结构"同理），本次不动。也不处理
"多文件之间重复拉取同一个目标集合"的问题（用户前面提到的方案①，另外排期）。

## Decisions (confirmed with user)

1. **进度展示**：新增一个独立的文字阶段"正在解析关联/引用字段... X / Y"，
   插在现有"正在解析文件..."和"正在导入... X / Y"之间。
2. **实现方式**：不改 `src/composables/useMultiFileImport.ts`（Task 1 已审查
   完成、正在生产使用，`FileImportStatus`/`currentPhase` 类型保持
   `'parsing' | 'uploading'` 不变）。改为在 `DynamicPage.vue` 本地维护一个
   `resolvingActive`/`resolvingProgress` 状态，由 `importPageRecords` 新增的
   `onResolveProgress` 回调驱动；一旦真正的上传进度（原有 `onProgress`）开始
   触发，立即把 `resolvingActive` 置回 `false`，文案切回"正在导入..."。
3. **四个 resolve 函数从并发改为串行**：为了能在开始前算出一个稳定的总步数
   （`resolveTotal = 参与解析的字段数 × records.length`），把
   `importPageRecords` 里原本 `Promise.all([...])` 的 4 个函数改成顺序
   `await`。副作用：不同字段引用同一个目标集合时，原来可能并发发起重复请求
   （`collectionCache` 在并发下对同一个尚未缓存的 collection 可能被命中两次
   fetch），串行后自然消除——算是顺带修掉的一个小问题，不是本次目标但记录一下。

## Architecture

### `src/stores/pageConfig.ts`

**新增**：`getResolveFieldCount(pageId): number` —— 统计"实际会被处理"的
关联类字段数（跟四个 resolve 函数各自的"参与条件"过滤逻辑保持一致，两处
需同步维护，用注释互相提示）：

```typescript
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

记得在 store 的 `return { ... }` 导出块里加上 `getResolveFieldCount`。

**新增**（文件顶部靠近其他 helper 的位置，不导出，只在本文件内用）：

```typescript
/** 每处理这么多条记录后让出一次主线程，避免同步循环卡住页面交互。 */
const RESOLVE_CHUNK_SIZE = 5000

function yieldToMain(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}
```

**修改**：`resolveRelationImportValues`/`resolveReferenceImportValues`/
`resolveQuoteImportValues`/`resolveCollectionSelectImportValues` 各自新增一个
可选的最后参数 `onChunkProcessed?: (n: number) => void`，把"解析每条记录"的
内层循环改成分片：

`resolveRelationImportValues`（其余三个函数按同样模式改，见下方"逐函数改
动"）：

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

      for (let start = 0; start < records.length; start += RESOLVE_CHUNK_SIZE) {
        const end = Math.min(start + RESOLVE_CHUNK_SIZE, records.length)
        for (let i = start; i < end; i++) {
          const record = records[i]
          const vals = record[field.fieldName]
          if (!Array.isArray(vals) || vals.length === 0) continue
          record[field.fieldName] = vals
            .map((v: string) => {
              if (idSet.has(v)) return v
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

**逐函数改动**（其余三个）：

- `resolveReferenceImportValues`：把原来 `for (const record of records) { ... }`
  的解析循环换成和上面一样的分片写法（`RESOLVE_CHUNK_SIZE` 分块 + 循环体内
  逻辑原样保留 + 每块结束调用 `onChunkProcessed?.(end - start)` +
  `await yieldToMain()`），新增同样的最后一个可选参数。
- `resolveQuoteImportValues`：同样只改"解析每条导入记录"的那个循环（不是
  建查找 Map 的循环），加同样的分片 + `onChunkProcessed` 参数。
- `resolveCollectionSelectImportValues`：该函数每个字段内有两个记录级循环——
  "自引用预生成 `_importId`"的循环（自引用场景较少见，量级通常不大，**本次
  不分片、不计入进度**，保持原样）和"解析每条记录的选项值"的循环（这个才
  分片 + 报进度，对应 `getResolveFieldCount` 里 `collectionSelectCount` 的
  计数口径）。

### `src/utils/importPageRecords.ts`

**修改** `ImportPageParams`：新增可选字段

```typescript
  onResolveProgress?: (current: number, total: number) => void
```

**修改** `importPageRecords` 函数体：把原来的

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
    // 串行执行（而不是原来的 Promise.all）：这样才能在开始前用一个稳定的
    // resolveTotal 报告有意义的百分比；顺带消除了不同字段引用同一个尚未
    // 缓存的目标集合时，并发下可能重复发起 fetch 的小问题。
    await store.resolveRelationImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveReferenceImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveQuoteImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveCollectionSelectImportValues(pageId, records, collectionCache, onChunkProcessed)
  }
```

（`resolveFieldCount === 0` 时——绝大多数没有关联类字段的页面——完全不调用
`onResolveProgress`，UI 不会闪出"正在解析关联字段"这个阶段。）

`ImportPageParams` 解构处相应加上 `onResolveProgress`。

### `src/views/dynamic/DynamicPage.vue`

**新增两个 ref**（跟现有 `importElapsedSeconds` 放在一起）：

```typescript
/**
 * 关联/引用字段解析阶段的进度（导入的隐藏子阶段）。resolvingActive 为
 * true 时，进度弹窗把"正在导入..."文案换成"正在解析关联/引用字段..."；
 * 一旦真正的上传进度开始回调，立即置回 false。
 */
const resolvingActive = ref(false)
const resolvingProgress = ref({ current: 0, total: 0 })
```

**修改** `uploadImportedRecords`（原样保留其余逻辑，只加 `onResolveProgress`
和给 `onProgress` 包一层）：

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

**修改**进度弹窗模板：原来的

```html
        <p v-else>
          正在导入... {{ multiImport.state.currentProgress.current }} / {{ multiImport.state.currentProgress.total }}
        </p>
```

替换为：

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

（`el-progress` 的百分比在"正在解析关联/引用字段"这段时间会停在 0%——跟现有
"正在读取文件结构...已用时 N 秒"阶段的既有表现完全一致，不是新增的不一致。）

## Data Flow

```
uploadImportedRecords(records, onProgress)
  → importPageRecords({ ..., onProgress: 包一层, onResolveProgress })
      resolveFieldCount = getResolveFieldCount(pageId)
      if resolveFieldCount > 0:
        resolveTotal = resolveFieldCount * records.length
        依次 await 四个 resolve 函数（串行），每个函数内部按
        RESOLVE_CHUNK_SIZE 分片处理 + yieldToMain() 让出主线程
          → 每片结束回调 onChunkProcessed(片大小)
            → 累加 resolveProcessed，调用 onResolveProgress(processed, total)
              → DynamicPage: resolvingActive=true，文案更新
      （resolve 全部完成后）批量生成序列值 → 分批 batch-create 上传
        → 每批结束调用原有 onProgress(current, total)
          → DynamicPage: resolvingActive 置回 false，文案切回"正在导入..."
```

## Error Handling

- 沿用现有行为：某个字段的目标集合 `fetchCollectionData` 失败时
  `catch { continue }`，跳过该字段（不算失败、不阻断整体导入）——这个字段
  对应的 `records.length` 份"应处理步数"不会被 `onChunkProcessed` 补上，
  意味着 `resolveProcessed` 可能到不了 `resolveTotal` 就进入下一个字段/下一
  阶段。这是已知的、可接受的显示层小瑕疵（进度文字可能从"1500/2000"直接跳
  到"正在导入..."），不影响功能正确性，本次不特殊处理。
- `retryImportFailures`（重试失败记录）不调用任何 resolve 函数（复用首次
  导入时已经解析好的 payload），不受本次改动影响。

## Testing

- **Vitest（`src/stores/__tests__/pageConfig.test.ts` 或已有的等价测试文件，
  先看现有测试怎么组织 pageConfig store 的测试，跟随其模式）**：
  - `getResolveFieldCount`：0 个关联字段 → 0；1 个 relation + 1 个 reference
    + 1 个 quoteSelect + 1 个来自集合的 select → 4；`relationConfig`/
    `referenceConfig.targetCollection`/`quoteConfig` 为空的字段不计入。
  - 每个 resolve 函数：记录数超过 `RESOLVE_CHUNK_SIZE` 时，`onChunkProcessed`
    被调用多次、每次传入的 n 加总等于 `records.length`；记录数为 0 或字段数
    为 0 时 `onChunkProcessed` 完全不被调用。
  - 现有对 4 个 resolve 函数正确性的测试（值替换逻辑本身）必须保持全部通过
    ——本次只加分片和进度回调，不改变解析结果。
- **`src/utils/__tests__/importPageRecords.test.ts`（如果不存在则新建，先确认
  是否已有同名测试文件并跟随其现有模式）**：
  - mock store 的四个 resolve 函数与 `getResolveFieldCount`，断言
    `onResolveProgress` 收到的 `(current, total)` 序列正确、且在
    `resolveFieldCount === 0` 时完全不被调用。
  - 断言四个 resolve 函数现在是**顺序** `await`（而不是 `Promise.all`）——
    比如让第一个 mock 函数故意延迟，断言第二个 mock 直到第一个 resolve 之后
    才被调用。
- **Playwright（CLAUDE.md 强制项，UI 改动）**：用一个配置了 relation/
  reference 字段、且目标集合有一定数据量的真实数据页，导入一个记录数明显
  超过 `RESOLVE_CHUNK_SIZE`（5000）的文件，验证：
  - 进度弹窗依次显示"正在解析文件..." → "正在解析关联/引用字段... X/Y"
    → "正在导入... X/Y"，中间那一段是新增的、可见的。
  - 导入一个**没有**关联类字段的页面，确认"正在解析关联/引用字段"这个阶段
    完全不出现（直接从解析文件跳到正在导入）。
  - 最终数据正确写入（关联字段被正确解析成内部 ID），跟改动前行为一致。
