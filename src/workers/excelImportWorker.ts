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
 */
import { parseWorkbookBuffer, parseJsonText } from '@/utils/excelParseCore'
import type { FieldConfig } from '@/types'

declare const self: DedicatedWorkerGlobalScope
export {}

export interface ImportWorkerRequest {
  id: number
  mode: 'xlsx' | 'json'
  file: File
  fields: FieldConfig[]
}

export type ImportWorkerResponse =
  | { id: number; ok: true; records: Record<string, any>[] }
  | { id: number; ok: false; error: string }

self.onmessage = async (e: MessageEvent<ImportWorkerRequest>) => {
  const { id, mode, file, fields } = e.data
  try {
    let records: Record<string, any>[]
    if (mode === 'xlsx') {
      const buffer = await file.arrayBuffer()
      records = parseWorkbookBuffer(buffer, fields)
    } else {
      const text = await file.text()
      records = parseJsonText(text, fields)
    }
    const response: ImportWorkerResponse = { id, ok: true, records }
    self.postMessage(response)
  } catch (err) {
    const response: ImportWorkerResponse = {
      id,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    }
    self.postMessage(response)
  }
}
