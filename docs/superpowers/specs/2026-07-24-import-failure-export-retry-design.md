# 导入失败记录可追溯（导出 + 重试）— 设计

## 背景

`DynamicPage.vue` 的「导入数据」（大文件导入，见 CLAUDE.md「Excel/JSON Data Import」一节）目前只报告汇总数字：导入完成弹窗只显示"成功 X 条，失败 Y 条"。用户导入 20 万条数据，若有 1000 条失败，完全无法知道是哪些行、为什么失败，只能把整批 20 万条数据重新导入一遍——不合理，也没必要，因为后端其实已经算出了每条失败记录的原因，只是前端把它丢弃了。

### 现状代码路径

- `src/utils/importPageRecords.ts::importPageRecords`：解析引用/生成序列 → 分批（`BATCH_SIZE=1000`，`CONCURRENCY=3`）POST `/​<collection>/batch-create`。
- 后端 `server/routes/dynamic.py::batch_create_items`：`continueOnError=true` 时已经会返回 `{ created, updated, failed, errors: [{ index, error, record, validationErrors? }] }`——每条失败记录的原因和数据都在里面。
- `runBatch`（`importPageRecords.ts:104`）目前只是 `console.warn(errors)`，只把 `result.failed` 累加进总数，不保留任何细节；HTTP 请求本身抛错（网络/超时）时更是直接把整批记录数计入 `failed`，连"是哪一批"都不追踪。
- `DynamicPage.vue` 的导入结果面板（`import-result`，约 865 行）只渲染 `success`/`failed`/`created`/`updated` 四个数字，没有任何失败明细入口。

## 范围

**仅** `DynamicPage.vue` 单页导入入口 + 其共用的 `importPageRecords.ts`。`src/composables/useBatchImport.ts`（`DataMigrationPage.vue` 用的跨项目批量导入编排）本次不动——它本来就是多页面顺序导入，量级和使用场景都不同，留待后续需要时再单独处理。

## 已与用户确认的关键决策

1. **失败记录的呈现方式：导出失败清单文件**（而非只在弹窗里列表展示）——量级到千条时，弹窗内表格体验差，导出文件让用户能直接改数据、直接重新导入。
2. **需要「一键重试失败记录」**——很多失败是瞬时的（并发写冲突、超时），没必要强制用户先导出再重新走一遍完整导入流程。
3. **失败记录清单不持久化**——只存在于当前导入弹窗的会话状态里，关闭弹窗即丢弃。如果用户需要留档，导出文件即持久化载体。

## 核心数据流改造（`src/utils/importPageRecords.ts`）

关键洞察：`runBatch` 里为每条记录准备好的请求体 `{ id, data, relations }`（已经过序列号填充、关联关系解析）在准备完成后是自包含、可重复提交的——**重试 = 把这份 payload 原样再 POST 一次**，完全不需要重新跑 `resolveRelationImportValues`/`batchGenerateSequenceValues` 等解析步骤（重新跑会有生成新序列号、与已成功记录冲突的风险）。

### 新增类型

```ts
export interface ImportFailure {
  originalRecord: Record<string, any>   // 解析出的原始行（fieldName 为 key），供导出用
  payload: { id: string; data: any; relations: Record<string, string[]> }  // 已就绪，可直接重新 POST
  reason: string                        // 人类可读，来自 error 或 validationErrors.join('; ')
}

export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
  failures: ImportFailure[]             // 新增
}
```

### `runBatch` 改造

- 后端返回的 `errors[]`（每条含 `index`/`error`/`record`/可选 `validationErrors`）：`index` 是相对当前批次请求体的下标，需要映射回 `batchRecords[index]` 对应的 `originalRecord`（即 `records[start + index]`）和已构建的 `payload`（`batchData[index]`），拼成一条 `ImportFailure` 推入本次调用的 `failures` 累积数组。
- 整批 HTTP 请求失败（`catch (error)` 分支，网络/超时等）：该分支目前完全没有单条粒度信息，改造后为 `batchRecords` 里的每一条都生成一条 `ImportFailure`（`reason` 用统一文案，如`"请求失败：<error.message>，可重试"`），保证即使整批失败也不丢失可追溯性/可重试性。

### 新增导出函数：`retryImportFailures`

```ts
export async function retryImportFailures(
  post: <T>(url: string, body: any) => Promise<T>,
  collection: string,
  failures: ImportFailure[],
): Promise<ImportPageResult>
```

- 直接对 `failures.map(f => f.payload)` 按原有 `BATCH_SIZE`/`CONCURRENCY` 重新分批 POST `/​<collection>/batch-create`（`continueOnError: true` 不变），**不调用**任何 store 方法（不重新解析关联、不重新生成序列号）。
- 返回值结构与 `importPageRecords` 一致，其中 `failures` 是"重试后仍然失败"的子集（`reason` 更新为本次重试拿到的最新原因）。

## UI 改造（`src/views/dynamic/DynamicPage.vue`）

`importResult` 的类型和本地 state 从 `{ success, failed, created, updated }` 扩展为直接持有完整 `ImportPageResult`（含 `failures`）。

导入结果面板（`failed > 0` 时）在现有 `el-result` 下方新增两个按钮：

- **导出失败清单**：始终可点（只要当前 `failures.length > 0`）。用项目已有的 SheetJS（`xlsx`，`excelParseCore.ts` 已依赖）在浏览器端直接生成 `.xlsx` 并触发下载，不经过后端。
- **重试失败记录**：点击后复用现有的 `import-progress` 进度条 UI（同一套"正在导入... X / Y"展示），调用 `retryImportFailures`。完成后：
  - `success`/`created`/`updated` 按新增成功数累加；
  - `failed` 减少、`failures` 替换为本次重试后仍失败的子集；
  - 若 `failed` 归零，两个按钮一并消失，只留成功提示；
  - 若仍有剩余失败，按钮保留，可再次点击重试（不限次数）。

`failures`（含 `originalRecord`/`payload`）只存在于该弹窗的响应式 state 中，随 `importDialogVisible = false` 一起丢弃，不做任何后端持久化。

## 导出文件格式

文件名：`导入失败记录_<页面名>_<yyyyMMddHHmmss>.xlsx`。

列结构：
1. 该页面 `pageConfig.fields` 顺序，每个字段一列，表头用 `field.label`（与原始导入文件表头约定一致），内容取 `failure.originalRecord[field.fieldName]`。内部记账字段（`_importId` 等）不导出。
2. 末尾追加一列 **失败原因**，内容为 `failure.reason`。

用户改完数据后，把"失败原因"这一列删掉，就是一份可以直接重新导入的文件（沿用现有导入解析逻辑按 `field.label` 匹配表头）。

**已知限制（本次不解决）**：`select`/`multiSelect` 字段导出的是当前存储的 value 而非原始 Excel 里录入的 label（因为 `originalRecord` 是解析后的 fieldName-keyed 数据，只保留 value）。重新导入时不受影响——现有解析逻辑 `labelToValue` 本身就是 value/label 双向匹配（`o.label === label || o.value === label`），所以 value 依然能正确解析；只是导出文件里看到的不一定和用户当初在 Excel 里敲的文字完全一致。

## 边界情况

| 场景 | 处理 |
|------|------|
| 后端逐条校验失败（主键重复、唯一性冲突、校验脚本失败/异常） | 原始 `error`/`validationErrors` 就是 reason |
| 整批 HTTP 请求失败（网络/超时） | 该批所有记录标记为失败，统一 reason，可重试 |
| 重试后同一条记录换了个新原因失败 | `reason` 覆盖为最新一次的原因 |
| 重试全部成功 | `failed` 归零，操作按钮消失 |
| 关闭导入弹窗后再重新打开 | 之前的 `failures` 不保留（未持久化，符合已确认的决策） |

## 测试

`src/utils/__tests__/importPageRecords.test.ts`：
- 后端返回 `errors`（含 `validationErrors`）时，`failures` 正确记录 `originalRecord`/`payload`/拼接后的 `reason`。
- 整批请求 `catch` 分支时，`failures` 里每条记录都有对应条目而非只是计数。
- `retryImportFailures`：给定一组 `failures`，验证按 `BATCH_SIZE`/`CONCURRENCY` 重新分批提交、且不调用 store 的任何解析方法（用 mock store 断言未被调用）；部分再次失败时返回的 `failures` 子集正确。

`src/views/dynamic/DynamicPage.vue` 相关测试（若已有导入流程测试文件则加入，否则视现有测试覆盖情况新增）：
- `failed > 0` 时两个按钮渲染；`failed === 0` 时不渲染。
- 「重试失败记录」点击后走 `import-progress` 分支，结束后数字/按钮状态按预期更新。

Playwright 手动验证（本项目 UI 变更强制要求）：真实导入一批含若干条主键重复/校验失败数据的文件，确认失败明细、导出文件内容、重试后计数变化均符合预期。

## 非目标

- `useBatchImport.ts` / `DataMigrationPage.vue` 跨项目批量导入的失败追溯（本次范围外，已与用户确认）。
- 失败记录的服务端持久化/跨会话留存（已确认导出文件即持久化手段，不额外做后端存储）。
- 导出文件里 select/multiSelect 字段还原为原始 label（已知限制，不影响重新导入的正确性，本次不做）。
