/**
 * Excel/JSON 导入解析的纯函数核心逻辑
 *
 * 从 excel.ts 抽出来，专门给 Web Worker（workers/excelImportWorker.ts）用：
 * 全部是同步纯函数（不碰 FileReader/DOM），既能在 worker 里跑（避免解析大文件
 * 卡死主线程页面），也方便直接单测（不用构造 File/走异步读取）。
 */
import * as XLSX from 'xlsx'
import type { FieldConfig } from '@/types'

/**
 * 可导入导出的字段类型（排除文件、图片、关联）
 */
export const EXPORTABLE_TYPES = ['text', 'textarea', 'number', 'date', 'datetime', 'select', 'multiSelect', 'radio', 'checkbox', 'relation', 'reference', 'autoTimestamp', 'autoSequence', 'quoteSelect', 'richText', 'compositeText']

/**
 * 筛选可导入导出的字段
 */
export function getExportableFields(fields: FieldConfig[]): FieldConfig[] {
  return fields
    .filter((f) => EXPORTABLE_TYPES.includes(f.controlType) && !f.hidden)
    .sort((a, b) => a.order - b.order)
}

/**
 * 将显示标签转回选项值
 */
export function labelToValue(label: string, field: FieldConfig): any {
  if (!label) {
    if (['multiSelect', 'checkbox', 'relation', 'quoteSelect'].includes(field.controlType)) return []
    return ''
  }

  if (['select', 'radio'].includes(field.controlType)) {
    const opt = field.options?.find((o) => o.label === label || o.value === label)
    return opt?.value || label
  }

  if (['multiSelect', 'checkbox'].includes(field.controlType)) {
    const labels = label.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)
    return labels.map((l) => {
      const opt = field.options?.find((o) => o.label === l || o.value === l)
      return opt?.value || l
    })
  }

  if (field.controlType === 'relation') {
    return label.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)
  }

  if (field.controlType === 'quoteSelect') {
    return label.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)
  }

  if (field.controlType === 'autoTimestamp') {
    return null
  }

  if (field.controlType === 'autoSequence') {
    return null
  }

  if (field.controlType === 'compositeText') {
    return null
  }

  if (field.controlType === 'richText') {
    return label
  }

  return label
}

function buildHeaderToField(fields: FieldConfig[]): Map<string, FieldConfig> {
  const exportFields = getExportableFields(fields)
  const headerToField = new Map<string, FieldConfig>()
  exportFields.forEach((f) => {
    headerToField.set(f.label, f)
    if (!headerToField.has(f.fieldName)) {
      headerToField.set(f.fieldName, f)
    }
  })
  return headerToField
}

/**
 * 解析 Excel 文件的字节内容（同步）
 *
 * @param buffer - 文件的 ArrayBuffer（调用方负责读取，worker 里用 file.arrayBuffer()）
 * @param fields - 字段配置
 * @returns 解析后的记录数组
 */
export function parseWorkbookBuffer(buffer: ArrayBuffer, fields: FieldConfig[]): Record<string, any>[] {
  const headerToField = buildHeaderToField(fields)

  const data = new Uint8Array(buffer)
  const wb = XLSX.read(data, { type: 'array' })

  // 读取第一个 sheet
  const ws = wb.Sheets[wb.SheetNames[0]]
  const jsonData = XLSX.utils.sheet_to_json<Record<string, any>>(ws, { header: 1 }) as any[][]

  if (jsonData.length < 2) {
    return []
  }

  // 第一行是表头
  const headerRow = jsonData[0].map(String)

  // 匹配表头到字段
  const colFieldMap: (FieldConfig | null)[] = headerRow.map((header) => {
    return headerToField.get(header) || null
  })

  // 解析数据行
  const records: Record<string, any>[] = []
  for (let i = 1; i < jsonData.length; i++) {
    const row = jsonData[i]
    if (!row || row.every((cell: any) => cell === null || cell === undefined || cell === '')) {
      continue // 跳过空行
    }

    const record: Record<string, any> = {}
    let hasData = false

    colFieldMap.forEach((field, colIdx) => {
      if (!field) return
      const cellValue = row[colIdx]
      if (cellValue !== null && cellValue !== undefined && cellValue !== '') {
        record[field.fieldName] = labelToValue(String(cellValue), field)
        hasData = true
      }
    })

    if (hasData) {
      records.push(record)
    }
  }

  return records
}

/**
 * 解析导入 JSON 文件的文本内容（同步）
 *
 * @param text - 文件的文本内容
 * @param fields - 字段配置
 * @returns 解析后的记录数组
 */
export function parseJsonText(text: string, fields: FieldConfig[]): Record<string, any>[] {
  const headerToField = buildHeaderToField(fields)
  const ARRAY_FIELD_TYPES = ['multiSelect', 'checkbox', 'relation', 'quoteSelect']

  const parsed = JSON.parse(text)

  if (!Array.isArray(parsed)) {
    throw new Error('JSON 文件内容必须是数组')
  }

  const records: Record<string, any>[] = []

  for (const obj of parsed) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) continue

    const record: Record<string, any> = {}
    let hasData = false

    for (const [key, value] of Object.entries(obj)) {
      const field = headerToField.get(key)
      if (!field) continue

      if (value === null || value === undefined || value === '') continue

      if (Array.isArray(value) && ARRAY_FIELD_TYPES.includes(field.controlType)) {
        // 数组值：对 select 类字段做 label→value 映射
        if (['multiSelect', 'checkbox'].includes(field.controlType)) {
          record[field.fieldName] = value.map((item) => {
            const opt = field.options?.find((o) => o.label === String(item) || o.value === String(item))
            return opt?.value || String(item)
          })
        } else {
          // relation / quoteSelect — 保持字符串原样
          record[field.fieldName] = value.map((item) => String(item))
        }
        hasData = true
      } else if (typeof value === 'number' && field.controlType === 'number') {
        record[field.fieldName] = value
        hasData = true
      } else {
        record[field.fieldName] = labelToValue(String(value), field)
        hasData = true
      }
    }

    if (hasData) {
      records.push(record)
    }
  }

  return records
}
