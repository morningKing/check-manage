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
    if (state.running) return
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
