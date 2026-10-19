# 数据页多文件导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据页"导入数据"支持一次选择多个 Excel/JSON 文件，前端严格顺序（一次一个文件）处理，文件级失败跳过继续、结果按文件分组展示、支持中途取消，且不需要任何后端改动。

**Architecture:** 新增 `useMultiFileImport` composable（纯逻辑，通过 deps 注入解析/上传/重试回调，不感知 Vue 组件细节）承载"选择→确认清单→顺序处理→分文件结果"的编排；`DynamicPage.vue` 只负责接入这个 composable（把现有的 `parseImportFile`/`parseJsonImportFile`/`importPageRecords`/`retryImportFailures`/`exportImportFailures` 包一层闭包传进去）并重写导入相关的对话框模板。

**Tech Stack:** Vue 3 `<script setup>` + `reactive()`，Vitest 单元测试，Playwright 现场验证。

## Global Constraints

- **不改动后端**：多文件导入的安全性完全来自"严格顺序处理、不跨文件并发"这一前端编排约束——不新增/修改任何 `server/` 下的路由或函数。
- **不修改** `src/utils/importPageRecords.ts`（`importPageRecords`/`retryImportFailures`）、`src/utils/excel.ts`（`exportImportFailures`/`parseImportFile`/`parseJsonImportFile`）、`src/workers/excelImportWorker.ts` ——这些函数按现状直接复用。
- **顺序不变式**：文件 N+1 的 `parseFile` 调用必须发生在文件 N 的 `uploadRecords` resolve 之后，任何时刻最多只有一个文件在被解析或上传。
- **文件级失败语义**：单个文件解析失败（含格式错误、损坏、`parseImportFile`/`parseJsonImportFile` 抛出的任何异常）标记该文件失败并记录原因，继续处理下一个文件，不中止整个批次。
- **取消语义**：取消只在文件与文件之间的边界生效——当前正在处理（解析中或上传中）的文件不中断、其已写入数据不回滚；调用取消之后、循环进入下一个文件之前检查取消标志，尚未开始的文件全部标记为 `skipped`。
- **结果展示**：按文件分组，每个文件独立的成功/失败计数、独立的失败原因、独立的"导出失败清单"/"重试失败记录"操作，互不影响。
- **单文件行为保持**：只选 1 个文件时依然先走"待导入清单确认"步骤（不做分支特殊处理），处理完成后的展示和交互与多文件场景使用同一套 UI/状态。
- **CLAUDE.md 强制项**：本次改动是 UI + 前后端交互变更，必须用 Playwright 现场验证（导航、真实选择文件、断言渲染结果），且必须在同一 PR 内同步更新用户手册文档。

---

### Task 1: `useMultiFileImport` composable

**Files:**
- Create: `src/composables/useMultiFileImport.ts`
- Test: `src/composables/__tests__/useMultiFileImport.test.ts`

**Interfaces:**
- Consumes: `ImportFailure`、`ImportPageResult`（来自 `@/utils/importPageRecords`，类型不变）
- Produces（Task 2 依赖）：
  ```typescript
  export type FileImportStatus =
    | 'pending' | 'parsing' | 'uploading'
    | 'success' | 'partial' | 'failed' | 'empty' | 'skipped'

  export interface FileImportEntry {
    file: File
    status: FileImportStatus
    result: ImportPageResult | null
    errorMessage: string | null
    retrying: boolean
  }

  export interface UseMultiFileImportDeps {
    parseFile: (file: File, onProgress: (current: number, total: number) => void) => Promise<Record<string, any>[]>
    uploadRecords: (records: Record<string, any>[], onProgress: (current: number, total: number) => void) => Promise<ImportPageResult>
    retryFailures: (failures: ImportFailure[], onProgress: (current: number, total: number) => void) => Promise<ImportPageResult>
  }

  export interface MultiFileImportState {
    stagedFiles: File[]
    stageVisible: boolean
    running: boolean
    cancelRequested: boolean
    fileResults: FileImportEntry[]
    currentFileIndex: number
    currentPhase: 'parsing' | 'uploading'
    currentProgress: { current: number; total: number }
  }

  export function useMultiFileImport(deps: UseMultiFileImportDeps): {
    state: MultiFileImportState
    setFiles: (files: File[]) => void
    removeStagedFile: (index: number) => void
    cancel: () => void
    start: () => Promise<void>
    retryFileFailures: (index: number) => Promise<void>
  }
  ```

- [ ] **Step 1: Write the failing tests**

Create `src/composables/__tests__/useMultiFileImport.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest'
import { useMultiFileImport } from '../useMultiFileImport'
import type { ImportPageResult } from '@/utils/importPageRecords'

function file(name: string): File {
  return new File(['x'], name)
}

function okResult(overrides: Partial<ImportPageResult> = {}): ImportPageResult {
  return { success: 1, failed: 0, created: 1, updated: 0, failures: [], ...overrides }
}

describe('useMultiFileImport', () => {
  it('processes files strictly in sequence (file 2 does not start before file 1 finishes uploading)', async () => {
    const events: string[] = []
    const parseFile = vi.fn(async (f: File) => {
      events.push(`parse:${f.name}`)
      return [{ n: 1 }]
    })
    const uploadRecords = vi.fn(async () => {
      events.push('upload:start')
      await new Promise((r) => setTimeout(r, 5))
      events.push('upload:end')
      return okResult()
    })
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    await m.start()

    expect(events).toEqual([
      'parse:a.xlsx', 'upload:start', 'upload:end',
      'parse:b.xlsx', 'upload:start', 'upload:end',
    ])
    expect(m.state.fileResults.map((r) => r.status)).toEqual(['success', 'success'])
  })

  it('skips a file that fails to parse and continues with the rest', async () => {
    const parseFile = vi.fn(async (f: File) => {
      if (f.name === 'bad.xlsx') throw new Error('文件已损坏')
      return [{ n: 1 }]
    })
    const uploadRecords = vi.fn(async () => okResult())
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('bad.xlsx'), file('good.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('failed')
    expect(m.state.fileResults[0].errorMessage).toBe('文件已损坏')
    expect(m.state.fileResults[1].status).toBe('success')
    expect(uploadRecords).toHaveBeenCalledTimes(1)
  })

  it('stops remaining files after a mid-way cancel, marking them skipped', async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const retryFailures = vi.fn()
    const uploadRecords = vi.fn(async () => {
      // 模拟用户在第一个文件上传过程中点击取消
      m.cancel()
      return okResult()
    })

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx'), file('c.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('success')
    expect(m.state.fileResults[1].status).toBe('skipped')
    expect(m.state.fileResults[2].status).toBe('skipped')
    expect(parseFile).toHaveBeenCalledTimes(1)
  })

  it("keeps per-file results isolated: one file's failures do not affect another file's result", async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const uploadRecords = vi.fn()
      .mockResolvedValueOnce(okResult({
        failed: 1,
        failures: [{ originalRecord: {}, payload: { id: '1', data: {}, relations: {} }, reason: 'x' }],
      }))
      .mockResolvedValueOnce(okResult())
    const retryFailures = vi.fn()

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    await m.start()

    expect(m.state.fileResults[0].status).toBe('partial')
    expect(m.state.fileResults[0].result?.failed).toBe(1)
    expect(m.state.fileResults[1].status).toBe('success')
    expect(m.state.fileResults[1].result?.failed).toBe(0)
  })

  it('removeStagedFile removes the file from the staged list before start', () => {
    const m = useMultiFileImport({ parseFile: vi.fn(), uploadRecords: vi.fn(), retryFailures: vi.fn() })

    m.setFiles([file('a.xlsx'), file('b.xlsx')])
    m.removeStagedFile(0)

    expect(m.state.stagedFiles.map((f) => f.name)).toEqual(['b.xlsx'])
  })

  it("retryFileFailures merges retry results into that file's result and toggles retrying", async () => {
    const parseFile = vi.fn(async () => [{ n: 1 }])
    const failingFailure = { originalRecord: {}, payload: { id: '1', data: {}, relations: {} }, reason: 'boom' }
    const uploadRecords = vi.fn(async () => okResult({ success: 1, created: 1, failed: 1, failures: [failingFailure] }))
    let resolveRetry: (v: ImportPageResult) => void = () => {}
    const retryFailures = vi.fn(() => new Promise<ImportPageResult>((r) => { resolveRetry = r }))

    const m = useMultiFileImport({ parseFile, uploadRecords, retryFailures })
    m.setFiles([file('a.xlsx')])
    await m.start()
    expect(m.state.fileResults[0].status).toBe('partial')

    const retryPromise = m.retryFileFailures(0)
    expect(m.state.fileResults[0].retrying).toBe(true)
    resolveRetry(okResult({ success: 1, created: 1, failed: 0, failures: [] }))
    await retryPromise

    expect(m.state.fileResults[0].retrying).toBe(false)
    expect(m.state.fileResults[0].status).toBe('success')
    expect(m.state.fileResults[0].result?.created).toBe(2)
    expect(m.state.fileResults[0].result?.failed).toBe(0)
    expect(retryFailures).toHaveBeenCalledWith([failingFailure], expect.any(Function))
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/composables/__tests__/useMultiFileImport.test.ts`
Expected: FAIL — `Cannot find module '../useMultiFileImport'` (module doesn't exist yet).

- [ ] **Step 3: Implement `useMultiFileImport`**

Create `src/composables/useMultiFileImport.ts`:

```typescript
import { reactive } from 'vue'
import type { ImportFailure, ImportPageResult } from '@/utils/importPageRecords'

export type FileImportStatus =
  | 'pending'
  | 'parsing'
  | 'uploading'
  | 'success'
  | 'partial'
  | 'failed'
  | 'empty'
  | 'skipped'

export interface FileImportEntry {
  file: File
  status: FileImportStatus
  result: ImportPageResult | null
  errorMessage: string | null
  retrying: boolean
}

export interface UseMultiFileImportDeps {
  parseFile: (file: File, onProgress: (current: number, total: number) => void) => Promise<Record<string, any>[]>
  uploadRecords: (records: Record<string, any>[], onProgress: (current: number, total: number) => void) => Promise<ImportPageResult>
  retryFailures: (failures: ImportFailure[], onProgress: (current: number, total: number) => void) => Promise<ImportPageResult>
}

export interface MultiFileImportState {
  stagedFiles: File[]
  stageVisible: boolean
  running: boolean
  cancelRequested: boolean
  fileResults: FileImportEntry[]
  currentFileIndex: number
  currentPhase: 'parsing' | 'uploading'
  currentProgress: { current: number; total: number }
}

/**
 * 多文件顺序导入编排：严格一次处理一个文件（不跨文件并发）——见
 * docs/superpowers/specs/2026-07-26-multi-file-import-design.md。
 * 不感知 Excel/JSON 解析细节或具体页面/collection，靠 deps 注入。
 */
export function useMultiFileImport(deps: UseMultiFileImportDeps) {
  const state = reactive<MultiFileImportState>({
    stagedFiles: [],
    stageVisible: false,
    running: false,
    cancelRequested: false,
    fileResults: [],
    currentFileIndex: -1,
    currentPhase: 'parsing',
    currentProgress: { current: 0, total: 0 },
  })

  function setFiles(files: File[]): void {
    state.stagedFiles = files
    state.stageVisible = true
  }

  function removeStagedFile(index: number): void {
    state.stagedFiles.splice(index, 1)
  }

  function cancel(): void {
    state.cancelRequested = true
  }

  async function start(): Promise<void> {
    state.stageVisible = false
    state.running = true
    state.cancelRequested = false
    state.fileResults = state.stagedFiles.map((file) => ({
      file, status: 'pending', result: null, errorMessage: null, retrying: false,
    }))

    for (let i = 0; i < state.fileResults.length; i++) {
      if (state.cancelRequested) {
        for (let j = i; j < state.fileResults.length; j++) {
          state.fileResults[j].status = 'skipped'
        }
        break
      }

      state.currentFileIndex = i
      const entry = state.fileResults[i]
      entry.status = 'parsing'
      state.currentPhase = 'parsing'
      state.currentProgress = { current: 0, total: 0 }

      let records: Record<string, any>[]
      try {
        records = await deps.parseFile(entry.file, (current, total) => {
          state.currentProgress = { current, total }
        })
      } catch (error) {
        entry.status = 'failed'
        entry.errorMessage = error instanceof Error && error.message
          ? error.message
          : '文件解析失败，请检查文件格式'
        continue
      }

      if (records.length === 0) {
        entry.status = 'empty'
        continue
      }

      entry.status = 'uploading'
      state.currentPhase = 'uploading'
      state.currentProgress = { current: 0, total: records.length }
      const result = await deps.uploadRecords(records, (current, total) => {
        state.currentProgress = { current, total }
      })
      entry.result = result
      entry.status = result.failed === 0 ? 'success' : 'partial'
    }

    state.running = false
    state.currentFileIndex = -1
  }

  async function retryFileFailures(index: number): Promise<void> {
    const entry = state.fileResults[index]
    if (!entry?.result || entry.result.failures.length === 0) return

    entry.retrying = true
    try {
      const prev = entry.result
      const retryResult = await deps.retryFailures(prev.failures, () => {})
      entry.result = {
        success: prev.success + retryResult.success,
        created: prev.created + retryResult.created,
        updated: prev.updated + retryResult.updated,
        failed: retryResult.failed,
        failures: retryResult.failures,
      }
      entry.status = entry.result.failed === 0 ? 'success' : 'partial'
    } finally {
      entry.retrying = false
    }
  }

  return { state, setFiles, removeStagedFile, cancel, start, retryFileFailures }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/composables/__tests__/useMultiFileImport.test.ts`
Expected: PASS (6/6).

- [ ] **Step 5: Commit**

```bash
git add src/composables/useMultiFileImport.ts src/composables/__tests__/useMultiFileImport.test.ts
git commit -m "feat(import): 新增多文件顺序导入编排 composable"
```

---

### Task 2: 接入 `DynamicPage.vue` + 用户手册 + 现场验证

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`
- Create: `docs/user-guide/data/import-export.md`
- Modify: `docs/user-guide/README.md`
- Modify: `docs/user-guide/getting-started/overview.md`

**Interfaces:**
- Consumes: `useMultiFileImport`、`FileImportEntry`、`UseMultiFileImportDeps`（Task 1，`src/composables/useMultiFileImport.ts`）
- Consumes（不改动）: `parseImportFile`/`parseJsonImportFile`/`exportImportFailures`/`generateImportTemplate`（`@/utils/excel`），`importPageRecords`/`retryImportFailures`（`@/utils/importPageRecords`）

#### Step 1: 移除旧的单文件导入状态与函数

在 `src/views/dynamic/DynamicPage.vue` 的 `<script setup>` 中：

删除以下 ref（原 1190-1211 行附近，"导入对话框可见性"到"导入结果"注释块）：
`importDialogVisible`、`importLoading`、`importProgress`、`importCurrent`、`importTotal`、`importPhase`、`importElapsedSeconds`、`importResult`。

删除以下函数（原 2780-2910 行附近）：`handleFileSelected`（旧版）、`doImport`、`handleRetryImportFailures`、`handleExportImportFailures`。

（`handleImportCommand`、`handleDownloadTemplate`、`fileInputRef` 保留不动。）

#### Step 2: 新增 composable 实例与新的状态/函数

在删除的位置，插入：

```typescript
import { useMultiFileImport } from '@/composables/useMultiFileImport'
```

第 954 行原：
```typescript
import { importPageRecords, retryImportFailures, type ImportPageResult } from '@/utils/importPageRecords'
```
改为（`ImportPageResult` 具名导入随 `importResult` ref 一起被删除，不再被组件自身使用；新增 `ImportFailure` 类型供 `retryImportedFailures` 的参数类型标注使用）：
```typescript
import { importPageRecords, retryImportFailures, type ImportFailure } from '@/utils/importPageRecords'
```

```typescript
/**
 * 导入对话框可见性（进度 + 分文件结果）
 */
const importDialogVisible = ref(false)

/**
 * 解析阶段"正在读取文件结构"期间的秒数动画——XLSX.read()/JSON.parse() 本身
 * 无法报真实进度，用这个每秒跳动的计时器让用户看出程序仍在工作，不是卡死。
 * 每次进入新文件的解析阶段时重置为 0。
 */
const importElapsedSeconds = ref(0)
let importElapsedTimer: number | null = null

async function parseImportedFile(
  file: File,
  onProgress: (current: number, total: number) => void,
): Promise<Record<string, any>[]> {
  const isJson = file.name.toLowerCase().endsWith('.json')
  return isJson
    ? await parseJsonImportFile(file, pageFields.value, onProgress)
    : await parseImportFile(file, pageFields.value, onProgress)
}

async function uploadImportedRecords(
  records: Record<string, any>[],
  onProgress: (current: number, total: number) => void,
) {
  return importPageRecords({
    store: pageConfigStore,
    post,
    pageId: pageId.value,
    collection: collection.value,
    records,
    onProgress,
  })
}

async function retryImportedFailures(
  failures: ImportFailure[],
  onProgress: (current: number, total: number) => void,
) {
  return retryImportFailures(post, collection.value, failures, onProgress)
}

const multiImport = useMultiFileImport({
  parseFile: parseImportedFile,
  uploadRecords: uploadImportedRecords,
  retryFailures: retryImportedFailures,
})

watch(() => multiImport.state.running, (running) => {
  if (running) {
    importElapsedTimer = window.setInterval(() => { importElapsedSeconds.value += 1 }, 1000)
  } else if (importElapsedTimer !== null) {
    window.clearInterval(importElapsedTimer)
    importElapsedTimer = null
  }
})

watch(() => [multiImport.state.currentFileIndex, multiImport.state.currentPhase], () => {
  importElapsedSeconds.value = 0
})

/**
 * 处理文件选择：读取多选的文件列表，展示待导入清单供用户确认/移除。
 */
function handleFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  const files = input.files
  if (!files || files.length === 0) return
  input.value = ''
  multiImport.setFiles(Array.from(files))
}

/**
 * 待导入清单确认后开始顺序导入；结束后只要有任意文件写入了数据就刷新一次表格。
 */
async function handleStartMultiImport(): Promise<void> {
  importDialogVisible.value = true
  await multiImport.start()
  const anySuccess = multiImport.state.fileResults.some(
    (r) => r.result && r.result.created + r.result.updated > 0,
  )
  if (anySuccess) await loadPageData()
}

/**
 * 导出某个文件仍失败的记录：字段列 + 失败原因列，删掉原因列即可重新导入。
 */
function handleExportFileFailures(index: number): void {
  const entry = multiImport.state.fileResults[index]
  const failures = entry.result?.failures
  if (!failures || failures.length === 0) return

  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const baseName = entry.file.name.replace(/\.[^.]+$/, '')
  exportImportFailures(failures, pageFields.value, `导入失败记录_${baseName}_${ts}`)
}

/**
 * 重试某个文件仍失败的记录；有新增写入则刷新表格。
 */
async function handleRetryFileFailures(index: number): Promise<void> {
  await multiImport.retryFileFailures(index)
  const entry = multiImport.state.fileResults[index]
  if (entry.result && entry.result.created + entry.result.updated > 0) await loadPageData()
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
```

`watch` 需要从 `'vue'` 导入——检查文件顶部 `import { ... } from 'vue'` 是否已包含 `watch`；本文件已大量使用 `watch`（用于其他功能），无需新增导入。

#### Step 3: 隐藏文件选择器加 `multiple`

原（约 837-843 行）：
```html
    <input
      ref="fileInputRef"
      type="file"
      accept=".xlsx,.xls,.json"
      style="display: none"
      @change="handleFileSelected"
    />
```
改为：
```html
    <input
      ref="fileInputRef"
      type="file"
      accept=".xlsx,.xls,.json"
      multiple
      style="display: none"
      @change="handleFileSelected"
    />
```

#### Step 4: 新增"待导入文件清单"对话框

紧跟在上面的 `<input>` 之后、原"导入进度对话框"之前插入：

```html
    <!-- 待导入文件清单：多选文件后先确认/可移除，再开始顺序导入 -->
    <el-dialog
      v-model="multiImport.state.stageVisible"
      title="待导入文件"
      width="480px"
      :close-on-click-modal="false"
    >
      <div class="import-stage-list">
        <div
          v-for="(file, idx) in multiImport.state.stagedFiles"
          :key="idx"
          class="import-stage-row"
        >
          <span class="import-stage-name">{{ file.name }}</span>
          <span class="import-stage-size">{{ formatFileSize(file.size) }}</span>
          <el-button
            :icon="Delete"
            circle
            size="small"
            @click="multiImport.removeStagedFile(idx)"
          />
        </div>
        <el-empty
          v-if="multiImport.state.stagedFiles.length === 0"
          description="没有待导入的文件"
          :image-size="60"
        />
      </div>
      <template #footer>
        <el-button @click="multiImport.state.stageVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="multiImport.state.stagedFiles.length === 0"
          @click="handleStartMultiImport"
        >
          开始导入（{{ multiImport.state.stagedFiles.length }} 个文件）
        </el-button>
      </template>
    </el-dialog>
```

`Delete` 图标已在本文件顶部的 `@element-plus/icons-vue` 具名导入列表中，无需新增导入。

#### Step 5: 重写"导入进度/结果"对话框

原（约 846-894 行）的整个 `<el-dialog v-model="importDialogVisible" ...>` 块替换为：

```html
    <!-- 导入进度 / 分文件结果对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入数据"
      width="520px"
      :close-on-click-modal="false"
      :close-on-press-escape="!multiImport.state.running"
      :show-close="!multiImport.state.running"
    >
      <div v-if="multiImport.state.running" class="import-progress">
        <p class="import-progress-file">
          文件 {{ multiImport.state.currentFileIndex + 1 }} / {{ multiImport.state.fileResults.length }}：
          {{ multiImport.state.fileResults[multiImport.state.currentFileIndex]?.file.name }}
        </p>
        <el-progress
          :percentage="multiImport.state.currentProgress.total > 0
            ? Math.round((multiImport.state.currentProgress.current / multiImport.state.currentProgress.total) * 100)
            : 0"
          :stroke-width="20"
          striped
          striped-flow
        />
        <template v-if="multiImport.state.currentPhase === 'parsing'">
          <p v-if="multiImport.state.currentProgress.total > 0">
            正在解析文件... {{ multiImport.state.currentProgress.current }} / {{ multiImport.state.currentProgress.total }} 行
          </p>
          <p v-else>
            正在读取文件结构...已用时 {{ importElapsedSeconds }} 秒
            <span v-if="importElapsedSeconds >= 10">（文件较大，请耐心等待）</span>
          </p>
        </template>
        <p v-else>
          正在导入... {{ multiImport.state.currentProgress.current }} / {{ multiImport.state.currentProgress.total }}
        </p>
      </div>
      <div v-else-if="multiImport.state.fileResults.length > 0" class="import-file-results">
        <div
          v-for="(entry, idx) in multiImport.state.fileResults"
          :key="idx"
          class="import-file-result-row"
        >
          <div class="import-file-result-header">
            <span class="import-file-result-name">{{ entry.file.name }}</span>
            <el-tag v-if="entry.status === 'success'" type="success">成功</el-tag>
            <el-tag v-else-if="entry.status === 'partial'" type="warning">部分失败</el-tag>
            <el-tag v-else-if="entry.status === 'failed'" type="danger">解析失败</el-tag>
            <el-tag v-else-if="entry.status === 'empty'" type="info">无数据</el-tag>
            <el-tag v-else-if="entry.status === 'skipped'" type="info">已跳过</el-tag>
          </div>
          <p v-if="entry.status === 'failed'" class="import-file-result-detail">
            {{ entry.errorMessage }}
          </p>
          <p v-else-if="entry.result" class="import-file-result-detail">
            成功 {{ entry.result.success }} 条
            <template v-if="entry.result.updated">
              （新增 {{ entry.result.created }} / 更新 {{ entry.result.updated }}）
            </template>
            ，失败 {{ entry.result.failed }} 条
          </p>
          <div v-if="entry.result && entry.result.failed > 0" class="import-file-result-actions">
            <el-button size="small" @click="handleExportFileFailures(idx)">导出失败清单</el-button>
            <el-button
              size="small"
              type="primary"
              :loading="entry.retrying"
              @click="handleRetryFileFailures(idx)"
            >
              重试失败记录
            </el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button v-if="multiImport.state.running" type="danger" plain @click="multiImport.cancel()">
          取消
        </el-button>
        <el-button v-else type="primary" @click="importDialogVisible = false">确定</el-button>
      </template>
    </el-dialog>
```

#### Step 6: CSS

原 `.import-progress`/`.import-result` 规则（约 3589-3601 行）附近，新增：

```scss
.import-progress-file {
  font-weight: 500;
  margin-bottom: 8px;
}

.import-stage-list {
  max-height: 320px;
  overflow-y: auto;
}

.import-stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  .import-stage-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .import-stage-size {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    flex-shrink: 0;
  }
}

.import-file-results {
  max-height: 420px;
  overflow-y: auto;
}

.import-file-result-row {
  padding: 10px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
  }
}

.import-file-result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;

  .import-file-result-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.import-file-result-detail {
  margin-top: 4px;
  color: #606266;
  font-size: 13px;
}

.import-file-result-actions {
  margin-top: 6px;
  display: flex;
  gap: 8px;
}
```

（原 `.import-result { padding: 10px 0; }` 规则不再被模板使用，一并删除。）

- [ ] **Step 7: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: 0 errors（重点检查：删除的 `ImportPageResult`/`importResult` 等旧标识符没有残留引用；`ImportFailure` 类型正确导入）。

- [ ] **Step 8: 运行前端已有测试，确认无回归**

Run: `npx vitest run src/views/dynamic/__tests__/ src/composables/__tests__/`
Expected: 全部通过。

- [ ] **Step 9: 编写用户手册**

Create `docs/user-guide/data/import-export.md`：

```markdown
# 数据导入与导出

## 导入数据

1. 打开任意数据页，点击右上角「更多」→「导入数据」。
2. 在系统文件选择器中**可以一次选择一个或多个** `.xlsx`/`.xls`/`.json` 文件。
3. 选择完成后会先弹出「待导入文件」清单，列出每个文件的文件名和大小；
   选错了可以点击文件行右侧的删除按钮移出清单。
4. 确认无误后点击「开始导入」。

### 处理方式：严格按文件顺序，一次处理一个

系统**不会**同时解析或上传多个文件，而是处理完一个文件（解析 + 写入数据库）
后才开始下一个。这样即使一次选择很多个较大的文件，也不会因为并发处理而
让页面卡顿或让服务器 CPU 突然飙高。

处理过程中弹窗会显示"文件 i / N：文件名"以及当前文件的解析/写入进度。

### 某个文件本身有问题怎么办

如果某个文件本身无法解析（格式不对、文件损坏等），系统会跳过它、
继续处理清单里的下一个文件，不会因为一个坏文件导致整批全部失败。

### 中途取消

处理过程中点击「取消」，系统会在**当前文件处理完成后**停止——后面还没
开始处理的文件不会再处理；当前文件已经成功写入的数据不会回滚。

### 查看结果

全部处理完成后，弹窗按文件分组展示每个文件的结果：
- 成功：显示成功写入的记录数（新增/更新分别计数）
- 部分失败：显示成功数与失败数，可点击「导出失败清单」下载失败记录
  （字段列 + 失败原因列），删除原因列后即可重新导入；也可以直接点击
  「重试失败记录」原地重试，无需重新上传文件
- 解析失败：显示具体错误原因，该文件需要修正后重新选择导入
- 已跳过：因为中途取消，该文件未被处理

失败清单导出、重试都是**按文件单独进行**的——不同文件的失败记录互不影响。

### 下载导入模板

点击「更多」→「下载导入模板」，会按当前数据页的字段生成一份空白模板
（表头对应字段），照着模板格式填写后即可导入。

## 导出数据

点击数据页右上角「导出」，将当前列表数据导出为 `.xlsx` 文件。
```

- [ ] **Step 10: 更新文档索引与快速入门**

在 `docs/user-guide/README.md` 的"📊 数据功能 · data"表格中，`views.md` 那一行之前新增一行：

```markdown
| [import-export.md](./data/import-export.md) | 数据导入（含多文件顺序导入、失败清单导出/重试）与导出 |
```

在 `docs/user-guide/getting-started/overview.md` 第 49 行，原：
```markdown
| **导入 Excel** | 点击「导入」→ 下载模板 → 填写数据 → 上传文件 |
```
改为：
```markdown
| **导入 Excel** | 点击「导入」→ 下载模板 → 填写数据 → 上传一个或多个文件（详见 [数据导入导出](../data/import-export.md)） |
```

- [ ] **Step 11: Playwright 现场验证（CLAUDE.md 强制项）**

1. 确保前后端在跑（`npm run dev:all`，或按 CLAUDE.md 的 Playwright 验证流程重启后端加载改动）。
2. 用 Playwright MCP 打开一个真实数据页，准备至少 3 个测试文件：2 个合法的小
   Excel/JSON（各几条记录）+ 1 个故意损坏/内容非法的文件（比如把 `.xlsx`
   后缀改到一个纯文本文件上，或一个内容错误的 `.json`）。
3. 点击「导入数据」，在系统文件选择器里同时选中这 3 个文件，断言弹出的
   「待导入文件」清单正确列出 3 个文件名+大小。
4. 尝试移除一个文件，断言清单减少到 2 个；重新触发选择，用全部 3 个文件
   点击「开始导入」。
5. 断言进度弹窗显示"文件 i / 3"与文件名递进；断言损坏文件被标记为
   "解析失败"且流程继续处理后续文件；断言最终结果按文件分组展示，
   合法文件显示成功计数。
6. 对一个有失败记录的文件（如果测试数据里有主键冲突之类），点击"重试失败
   记录"，断言只有该文件的失败数变化，其他文件不受影响；点击"导出失败
   清单"，断言触发了文件下载。
7. 用另一批文件重新触发一次多文件导入，处理到中途点击"取消"，断言当前
   文件处理完后停止，后续文件标记为"已跳过"。
8. 用真实 DB 查询或表格刷新结果，交叉验证成功写入的记录数与弹窗显示一致。
9. 截图关键结果（待导入清单、分文件结果、取消后状态）保存到
   `.playwright-mcp/`，并在总结中说明具体验证了什么。
10. 清理本次验证产生的测试数据（临时导入的记录），不遗留脏数据。

- [ ] **Step 12: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue docs/user-guide/data/import-export.md \
        docs/user-guide/README.md docs/user-guide/getting-started/overview.md
git commit -m "feat(import): 数据页导入支持多文件顺序处理，结果按文件分组展示"
```
