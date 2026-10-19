# 数据页多文件导入 — Design Spec

## Background

数据页的"导入数据"（Excel/JSON 批量导入）目前只支持一次选择一个文件。用户希望
能一次选择多个文件导入，同时明确要求：

- 前端"一个文件一个文件处理"（顺序处理，不并发处理多个文件）
- 不能出现页面卡死
- 不能出现后端 CPU 资源消耗过大的问题

现有单文件导入管线已经具备这两个安全属性：
- 解析在 Web Worker 里跑（`src/workers/excelImportWorker.ts`），不阻塞主线程；
- 上传阶段用 `importPageRecords`（`src/utils/importPageRecords.ts`）分批
  （`BATCH_SIZE=1000`）+ 有限并发（`CONCURRENCY=3`）调用
  `POST /<collection>/batch-create`。

**核心结论：多文件导入不需要任何后端改动。** 只要严格保证"文件 N+1 的解析/上传
在文件 N 完全结束之后才开始"（顺序处理，不跨文件并发），多文件导入对后端而言
和用户手动依次导入 N 次单文件完全等价——请求节奏、并发度、单批大小都不变，
不会引入新的 CPU/连接池压力模式。这个设计只是在前端加一层"顺序编排 + 分文件
展示"，复用现有的解析函数和 `importPageRecords`/`retryImportFailures`/
`exportImportFailures`。

## Decisions (confirmed with user)

1. **文件级失败（解析失败/损坏/格式错误）**：跳过该文件，继续处理下一个；该
   文件的结果标记为失败并记录原因，不中止整个批次。
2. **结果展示**：按文件分组展示，每个文件一行/一块，各自的成功/失败数、各自的
   "导出失败清单"/"重试失败记录"按钮（只作用于该文件的失败记录）。
3. **选择文件后的流程**：先展示一个"待导入文件清单"（文件名+大小，可移除单个
   文件），用户点击"开始导入"后才真正开始处理——即使只选了 1 个文件也统一走
   这个确认步骤，避免选错文件的分支复杂度。
4. **中途取消**：支持。点击"取消"后，停止处理后面还没开始的文件；当前正在
   处理的文件不会被中断、已写入的数据不回滚（取消只在文件与文件之间生效，
   不在文件内部的批次之间生效）。

## Architecture

### New: `src/composables/useMultiFileImport.ts`

一个不依赖 Vue 组件具体上下文的 composable，持有：

**State（reactive，供组件模板直接绑定）：**
- `stagedFiles: File[]` — 已选择、待确认的文件列表（确认前可移除单个）
- `stageVisible: boolean` — 待导入清单弹窗可见性
- `running: boolean` — 是否正在顺序处理
- `cancelRequested: boolean` — 用户是否点了取消（内部循环在文件边界检查）
- `fileResults: FileImportEntry[]` — 每个文件的处理状态与结果（顺序处理时原地更新）
- `currentFileIndex: number` — 当前处理到第几个文件（-1 表示未开始）
- `currentPhase: 'parsing' | 'uploading'` — 当前文件所处阶段
- `currentProgress: { current: number; total: number }` — 当前文件当前阶段的进度

```typescript
export interface FileImportEntry {
  file: File
  status: 'pending' | 'parsing' | 'uploading' | 'success' | 'partial' | 'failed' | 'empty' | 'skipped'
  result: ImportPageResult | null   // upload 完成后填入；parse-error/skipped 时为 null
  errorMessage: string | null       // 仅 status === 'failed'（解析失败）时有值
}
```

**依赖注入（组件传入，composable 本身不知道 Excel/JSON 解析细节）：**
```typescript
export interface UseMultiFileImportDeps {
  parseFile: (file: File, onProgress: (current: number, total: number) => void) => Promise<Record<string, any>[]>
  uploadRecords: (records: Record<string, any>[], onProgress: (current: number, total: number) => void) => Promise<ImportPageResult>
}
```

**方法：**
- `setFiles(files: File[])` — 设置待导入清单，打开 stage 弹窗
- `removeStagedFile(index: number)` — 从待导入清单移除一个文件
- `start(): Promise<void>` — 关闭 stage 弹窗，顺序处理 `stagedFiles`，逐个更新
  `fileResults`；每个文件开始前检查 `cancelRequested`，为真则将剩余未处理文件
  标记为 `skipped` 并结束
- `cancel(): void` — 设置 `cancelRequested = true`（下一个文件边界生效）
- `retryFileFailures(index: number, post): Promise<void>` — 对 `fileResults[index]`
  调用现有的 `retryImportFailures`，把返回的 `ImportPageResult` 合并回该文件的
  `result`（成功计数累加、`failures` 替换为剩余失败），复用 Phase 2 已有的
  `retryImportFailures` 函数，不重新实现
- `exportFileFailures(index: number, fields, filename): void` — 直接调用现有的
  `exportImportFailures(fileResults[index].result.failures, fields, filename)`

顺序循环的核心逻辑（伪代码，实际实现见 Task 1）：
```
for (i = 0; i < stagedFiles.length; i++) {
  if (cancelRequested) { mark remaining as 'skipped'; break }
  currentFileIndex = i
  fileResults[i].status = 'parsing'
  currentPhase = 'parsing'
  try {
    records = await deps.parseFile(stagedFiles[i], onParseProgress)
  } catch (e) {
    fileResults[i].status = 'failed'
    fileResults[i].errorMessage = e.message || '文件解析失败'
    continue   // 跳过该文件，处理下一个
  }
  if (records.length === 0) {
    fileResults[i].status = 'empty'
    continue
  }
  fileResults[i].status = 'uploading'
  currentPhase = 'uploading'
  result = await deps.uploadRecords(records, onUploadProgress)
  fileResults[i].result = result
  fileResults[i].status = result.failed === 0 ? 'success' : 'partial'
}
running = false
```

### Modified: `src/views/dynamic/DynamicPage.vue`

- 隐藏 `<input type="file">` 加 `multiple` 属性；`handleFileSelected` 改为读取
  `input.files`（FileList → `Array.from`），调用
  `multiImport.setFiles(files)`，不再直接解析。
- 新增"待导入文件清单"`el-dialog`：列出 `multiImport.stagedFiles`（文件名+
  格式化后的大小），每行一个移除按钮；底部"开始导入"按钮调用
  `multiImport.start()`（并传入 `deps`：`parseFile` 封装现有的
  `parseImportFile`/`parseJsonImportFile` 按扩展名分支逻辑；`uploadRecords`
  直接是现有的 `importPageRecords(...)` 调用，`store`/`pageId`/`collection`
  从组件上下文闭包传入）。
- 现有"导入进度对话框"模板替换：
  - loading 态显示 "文件 {currentFileIndex+1}/{stagedFiles.length}：
    {当前文件名}" + 现有的 phase/progress 展示（复用原有的
    parsing/uploading 两态样式）
  - 增加"取消"按钮（仅在 `running` 为真时显示），点击调用
    `multiImport.cancel()`
  - 结果态：把原来单个 `el-result` 换成 `fileResults` 的列表渲染，每项一个
    小卡片/行：状态图标 + 文件名 + "成功 X 条（新增 A / 更新 B），失败 Y 条"
    或 "解析失败：{errorMessage}" 或 "已跳过"；失败数 > 0 时该行内展示
    "导出失败清单"/"重试失败记录"按钮，分别调用
    `multiImport.exportFileFailures(i, ...)` / `multiImport.retryFileFailures(i, ...)`
- 旧的 `handleRetryImportFailures`/`handleExportImportFailures`/`doImport` 单文件
  专用函数被 composable 的等价方法取代并删除，避免重复实现同一逻辑
  （DRY）；`importPageRecords`/`retryImportFailures`/`exportImportFailures`
  本身不改动，只是调用方从"组件直接调用"变成"composable 内部调用"。

### Unmodified

- `src/utils/importPageRecords.ts`（`importPageRecords`/`retryImportFailures`）
- `src/utils/excel.ts`（`exportImportFailures`/`parseImportFile`/`parseJsonImportFile`）
- `src/workers/excelImportWorker.ts`
- 后端所有代码（`server/routes/dynamic.py` 等）——不需要改动

## Data Flow

```
用户选择 N 个文件
  → handleFileSelected 读取 FileList → multiImport.setFiles(files)
  → stage 弹窗展示，用户可移除后点"开始导入"
  → multiImport.start()
      for each file (严格顺序，一次一个):
        parseFile(file)  — Web Worker，不阻塞主线程
          → uploadRecords(records) — 分批+有限并发 POST /batch-create
          → fileResults[i] 原地更新
  → 全部处理完（或取消后跳出）→ running=false
  → 若任意文件有成功写入 → loadPageData() 刷新表格（一次，不是每文件一次）
```

## Error Handling

- 单文件解析失败：捕获异常，`status='failed'`，`errorMessage` 存具体原因，
  循环 `continue` 到下一个文件（不中止批次）。
- 单文件解析出 0 条记录：`status='empty'`，不算失败，也不调用上传。
- 上传阶段的记录级失败：沿用现有 `importPageRecords` 的
  `continueOnError: true` + `failures` 收集机制，不改动。
- 取消：只在文件边界检查，当前文件的批次不中断、不回滚；剩余未开始的文件
  标记为 `skipped`。

## Testing

- **Vitest（composable 单元测试，新增
  `src/composables/__tests__/useMultiFileImport.test.ts`）**：
  - 顺序性：mock `parseFile`/`uploadRecords`，断言文件 2 的 `parseFile` 调用
    发生在文件 1 的 `uploadRecords` resolve 之后（证明不跨文件并发）
  - 单文件解析失败：该文件 `status==='failed'`，其余文件仍正常处理完成
  - 中途取消：在第 2 个文件处理中途调用 `cancel()`，断言第 2 个文件完成后
    循环停止，第 3+ 个文件标记为 `skipped`
  - 每文件结果互相隔离：文件 A 的失败不会污染文件 B 的 `fileResults` 条目
  - `removeStagedFile`：移除后 `stagedFiles`/后续处理不包含被移除的文件
- **Playwright（CLAUDE.md 强制要求，UI 改动）**：真实选择多个文件（含一个
  故意损坏/格式错误的文件）导入到一个真实数据页，验证：
  - 待导入清单正确展示 + 移除功能生效
  - 顺序处理时进度提示正确显示"文件 i/N"
  - 损坏文件被跳过、其余文件正常完成，结果按文件分组展示
  - 某文件失败记录的"导出失败清单"/"重试失败记录"只作用于该文件
  - 取消按钮在处理中途点击后，后续文件不再处理
