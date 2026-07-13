/// <reference lib="webworker" />
/**
 * 导入文件解析 Worker
 *
 * 100MB 级别的 Excel/JSON 导入文件在主线程同步解析（XLSX.read + sheet_to_json /
 * JSON.parse，都是阻塞调用）会冻结整个页面几十秒到几分钟。这里把读文件 +
 * 解析都挪到 worker 线程，主线程只负责收发消息，UI 全程可交互。
 *
 * File 对象可以直接 structured-clone 传给 worker（无需在主线程先用
 * FileReader 读出 buffer），worker 内部用 file.arrayBuffer()/file.text()
 * 异步读取，再调用 excelParseCore.ts 里同步的纯解析函数。
 *
 * 百万行级文件：整份 records 数组一次性 postMessage 回主线程，会让主线程在
 * 收到消息那一刻做一次性的 structured-clone 反序列化，实测在 100 万行时
 * 造成一次约 2 秒的真实主线程卡顿（虽然只有一次，但仍是可感知的冻结）。
 * 这里改为按 CHUNK_SIZE 分片：每处理完一片就 postMessage 一次，主线程分批
 * 接收、分批 clone，单次 clone 的数据量小很多，也顺带给了解析阶段的真实
 * 进度（此前解析阶段只有一个不知道要等多久的转圈动画）。
 */
import {
  readWorkbookMeta,
  processWorkbookRowRange,
  readJsonMeta,
  processJsonItemRange,
} from '@/utils/excelParseCore'
import type { FieldConfig } from '@/types'

declare const self: DedicatedWorkerGlobalScope
export {}

const CHUNK_SIZE = 20000

export interface ImportWorkerRequest {
  id: number
  mode: 'xlsx' | 'json'
  file: File
  fields: FieldConfig[]
}

export type ImportWorkerResponse =
  | { id: number; type: 'chunk'; records: Record<string, any>[]; processed: number; total: number }
  | { id: number; type: 'done' }
  | { id: number; type: 'error'; error: string }

self.onmessage = async (e: MessageEvent<ImportWorkerRequest>) => {
  const { id, mode, file, fields } = e.data
  try {
    if (mode === 'xlsx') {
      const buffer = await file.arrayBuffer()
      const meta = readWorkbookMeta(buffer, fields)
      const total = meta.dataRows.length
      for (let start = 0; start < total; start += CHUNK_SIZE) {
        const end = Math.min(start + CHUNK_SIZE, total)
        const records = processWorkbookRowRange(meta, start, end)
        const response: ImportWorkerResponse = { id, type: 'chunk', records, processed: end, total }
        self.postMessage(response)
      }
    } else {
      const text = await file.text()
      const meta = readJsonMeta(text, fields)
      const total = meta.items.length
      for (let start = 0; start < total; start += CHUNK_SIZE) {
        const end = Math.min(start + CHUNK_SIZE, total)
        const records = processJsonItemRange(meta, start, end)
        const response: ImportWorkerResponse = { id, type: 'chunk', records, processed: end, total }
        self.postMessage(response)
      }
    }
    const done: ImportWorkerResponse = { id, type: 'done' }
    self.postMessage(done)
  } catch (err) {
    const response: ImportWorkerResponse = {
      id,
      type: 'error',
      error: err instanceof Error ? err.message : String(err),
    }
    self.postMessage(response)
  }
}
