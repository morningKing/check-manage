/**
 * Excel 导入导出工具
 *
 * 基于 SheetJS (xlsx) 实现：
 * - 数据导出为 Excel 文件
 * - 根据字段配置生成导入模板
 * - 解析导入的 Excel 文件
 */

import * as XLSX from 'xlsx'
import type { FieldConfig } from '@/types'
import { getExportableFields } from './excelParseCore'
import type { ImportWorkerRequest, ImportWorkerResponse } from '@/workers/excelImportWorker'

export { getExportableFields }

/**
 * 关联字段显示名称映射
 *
 * key: fieldName, value: Map<recordId, displayName>
 */
export type RelationDisplayMap = Record<string, Map<string, string>>

/**
 * 将选项值转为显示标签
 */
function valueToLabel(value: any, field: FieldConfig, record?: Record<string, any>, relationDisplayMap?: RelationDisplayMap): string {
  if (value === null || value === undefined || value === '') return ''

  if (['select', 'radio'].includes(field.controlType)) {
    const opt = field.options?.find((o) => o.value === value)
    return opt?.label || String(value)
  }

  if (['multiSelect', 'checkbox'].includes(field.controlType)) {
    if (Array.isArray(value)) {
      return value
        .map((v) => {
          const opt = field.options?.find((o) => o.value === v)
          return opt?.label || String(v)
        })
        .join('、')
    }
    return String(value)
  }

  if (field.controlType === 'relation') {
    if (Array.isArray(value)) {
      const displayMap = relationDisplayMap?.[field.fieldName]
      if (displayMap) {
        return value.map((id) => displayMap.get(id) || id).join('、')
      }
      return value.join('、')
    }
    return String(value)
  }

  if (field.controlType === 'reference') {
    if (record) {
      const displayVal = record[`_ref_${field.fieldName}_display`]
      if (displayVal) return String(displayVal)
    }
    return String(value)
  }

  if (field.controlType === 'quoteSelect') {
    if (Array.isArray(value)) {
      const displayMap = relationDisplayMap?.[field.fieldName]
      if (displayMap) {
        return value.map((id) => displayMap.get(id) || id).join('、')
      }
      return value.join('、')
    }
    return String(value)
  }

  if (field.controlType === 'richText') {
    return value?.replace(/<[^>]*>/g, '') || ''
  }

  return String(value)
}

/**
 * 导出数据到 Excel
 *
 * @param data - 表格数据
 * @param fields - 字段配置
 * @param filename - 文件名（不含扩展名）
 * @param relationDisplayMap - 关联字段显示名称映射（可选）
 */
export function exportToExcel(
  data: Record<string, any>[],
  fields: FieldConfig[],
  filename: string,
  relationDisplayMap?: RelationDisplayMap
): void {
  const exportFields = getExportableFields(fields)

  // 构建表头
  const headers = exportFields.map((f) => f.label)

  // 构建数据行
  const rows = data.map((record) => {
    return exportFields.map((field) => valueToLabel(record[field.fieldName], field, record, relationDisplayMap))
  })

  // 创建工作表
  const wsData = [headers, ...rows]
  const ws = XLSX.utils.aoa_to_sheet(wsData)

  // 设置列宽
  ws['!cols'] = exportFields.map((f) => ({
    wch: Math.max(f.label.length * 2 + 4, 12)
  }))

  // 创建工作簿
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '数据')

  // 下载
  XLSX.writeFile(wb, `${filename}.xlsx`)
}

/**
 * 生成导入模板
 *
 * @param fields - 字段配置
 * @param filename - 文件名（不含扩展名）
 */
export function generateImportTemplate(
  fields: FieldConfig[],
  filename: string
): void {
  const exportFields = getExportableFields(fields)

  // Sheet1: 数据模板（仅表头）
  const headers = exportFields.map((f) => f.label)
  const ws = XLSX.utils.aoa_to_sheet([headers])

  // 设置列宽
  ws['!cols'] = exportFields.map((f) => ({
    wch: Math.max(f.label.length * 2 + 4, 12)
  }))

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '导入数据')

  // Sheet2: 字段说明
  const guideRows: string[][] = [['字段名称', '字段标识', '类型', '是否必填', '可选值']]

  exportFields.forEach((field) => {
    const typeMap: Record<string, string> = {
      text: '文本',
      textarea: '多行文本',
      number: '数字',
      date: '日期（YYYY-MM-DD）',
      datetime: '日期时间（YYYY-MM-DD HH:mm:ss）',
      select: '单选',
      multiSelect: '多选（用 、 分隔多个值）',
      radio: '单选',
      checkbox: '多选（用 、 分隔多个值）',
      relation: '关联（用 、 分隔多个主键ID）',
      reference: '引用记录ID',
      autoTimestamp: '自动时间戳（无需填写）',
      autoSequence: '自增序列（无需填写，自动生成）',
      compositeText: '组合文本（自动生成，无需填写）',
      quoteSelect: '引用选择（用 、 分隔多个主键值）',
      richText: '富文本（纯文本导入，不保留格式）'
    }

    const options = field.options?.map((o) => o.label).join('、') || ''

    guideRows.push([
      field.label,
      field.fieldName,
      typeMap[field.controlType] || field.controlType,
      field.required ? '是' : '否',
      options
    ])
  })

  const guideWs = XLSX.utils.aoa_to_sheet(guideRows)
  guideWs['!cols'] = [{ wch: 16 }, { wch: 20 }, { wch: 28 }, { wch: 10 }, { wch: 40 }]
  XLSX.utils.book_append_sheet(wb, guideWs, '字段说明')

  XLSX.writeFile(wb, `${filename}.xlsx`)
}

// ==================== 导入文件解析：跑在 Web Worker 里 ====================
// 100MB 级别的文件在主线程同步解析（XLSX.read/JSON.parse 都是阻塞调用）会把
// 页面冻结几十秒到几分钟；实际的解析逻辑现在住在 workers/excelImportWorker.ts
// （同步纯函数版本见 excelParseCore.ts），这里只是发消息 + 等回复的胶水代码，
// 对外的函数签名和之前完全一样，调用方（DynamicPage.vue/ImportTab.vue）不用改。

let importWorker: Worker | null = null
let nextRequestId = 0
const pendingRequests = new Map<
  number,
  {
    resolve: (records: Record<string, any>[]) => void
    reject: (err: Error) => void
    records: Record<string, any>[]
    onProgress?: (current: number, total: number) => void
  }
>()

function getImportWorker(): Worker {
  if (importWorker) return importWorker

  const worker = new Worker(new URL('../workers/excelImportWorker.ts', import.meta.url), {
    type: 'module',
  })
  worker.onmessage = (e: MessageEvent<ImportWorkerResponse>) => {
    const msg = e.data
    const pending = pendingRequests.get(msg.id)
    if (!pending) return
    if (msg.type === 'chunk') {
      // 分片累积：百万行级文件如果一次性 postMessage 整个 records 数组，
      // 主线程收到消息时要做一次性 structured-clone 反序列化，实测在百万行
      // 时会造成一次约 2 秒的真实卡顿。按片接收后单次 clone 的数据量小得多。
      pending.records.push(...msg.records)
      pending.onProgress?.(msg.processed, msg.total)
    } else if (msg.type === 'done') {
      pendingRequests.delete(msg.id)
      pending.resolve(pending.records)
    } else {
      pendingRequests.delete(msg.id)
      pending.reject(new Error(msg.error))
    }
  }
  worker.onerror = (ev) => {
    // worker 自身崩溃（比如加载失败）：让所有还在等的请求都失败，
    // 不然调用方会一直悬挂在 await 上，看起来像卡死
    for (const pending of pendingRequests.values()) {
      pending.reject(new Error(`文件解析出错: ${ev.message}`))
    }
    pendingRequests.clear()
  }
  importWorker = worker
  return worker
}

function runImportInWorker(
  mode: ImportWorkerRequest['mode'],
  file: File,
  fields: FieldConfig[],
  onProgress?: (current: number, total: number) => void
): Promise<Record<string, any>[]> {
  return new Promise((resolve, reject) => {
    const worker = getImportWorker()
    const id = ++nextRequestId
    pendingRequests.set(id, { resolve, reject, records: [], onProgress })
    // fields 来自 Pinia store，是 Vue reactive() 包出来的 Proxy；结构化克隆
    // 算法认不出 Proxy，postMessage 会直接同步抛 DataCloneError。JSON 往返
    // 一圈把它变回纯对象（FieldConfig 本身就是可 JSON 序列化的数据，没有
    // 函数/循环引用，这个转换是安全的）。
    const plainFields = JSON.parse(JSON.stringify(fields)) as FieldConfig[]
    worker.postMessage({ id, mode, file, fields: plainFields } satisfies ImportWorkerRequest)
  })
}

/**
 * 解析导入的 Excel 文件（在 Web Worker 里跑，不阻塞页面）
 *
 * @param file - 上传的文件
 * @param fields - 字段配置
 * @param onProgress - 解析进度回调（已处理行数、总行数），分片返回时逐片调用
 * @returns 解析后的记录数组
 */
export function parseImportFile(
  file: File,
  fields: FieldConfig[],
  onProgress?: (current: number, total: number) => void
): Promise<Record<string, any>[]> {
  return runImportInWorker('xlsx', file, fields, onProgress)
}

/**
 * 解析导入的 JSON 文件（在 Web Worker 里跑，不阻塞页面）
 *
 * @param file - 上传的文件
 * @param fields - 字段配置
 * @param onProgress - 解析进度回调（已处理条数、总条数），分片返回时逐片调用
 * @returns 解析后的记录数组
 */
export function parseJsonImportFile(
  file: File,
  fields: FieldConfig[],
  onProgress?: (current: number, total: number) => void
): Promise<Record<string, any>[]> {
  return runImportInWorker('json', file, fields, onProgress)
}
