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

/**
 * 关联字段显示名称映射
 *
 * key: fieldName, value: Map<recordId, displayName>
 */
export type RelationDisplayMap = Record<string, Map<string, string>>

/**
 * 可导入导出的字段类型（排除文件、图片、关联）
 */
const EXPORTABLE_TYPES = ['text', 'textarea', 'number', 'date', 'datetime', 'select', 'multiSelect', 'radio', 'checkbox', 'relation', 'reference', 'autoTimestamp', 'autoSequence', 'quoteSelect']

/**
 * 筛选可导入导出的字段
 */
export function getExportableFields(fields: FieldConfig[]): FieldConfig[] {
  return fields
    .filter((f) => EXPORTABLE_TYPES.includes(f.controlType) && !f.hidden)
    .sort((a, b) => a.order - b.order)
}

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

  return String(value)
}

/**
 * 将显示标签转回选项值
 */
function labelToValue(label: string, field: FieldConfig): any {
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

  return label
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
      quoteSelect: '引用选择（用 、 分隔多个主键值）'
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

/**
 * 解析导入的 Excel 文件
 *
 * @param file - 上传的文件
 * @param fields - 字段配置
 * @returns 解析后的记录数组
 */
export function parseImportFile(
  file: File,
  fields: FieldConfig[]
): Promise<Record<string, any>[]> {
  const exportFields = getExportableFields(fields)

  // 建立 label / fieldName → field 映射（同时支持两种表头格式）
  const headerToField = new Map<string, FieldConfig>()
  exportFields.forEach((f) => {
    headerToField.set(f.label, f)
    if (!headerToField.has(f.fieldName)) {
      headerToField.set(f.fieldName, f)
    }
  })

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target!.result as ArrayBuffer)
        const wb = XLSX.read(data, { type: 'array' })

        // 读取第一个 sheet
        const ws = wb.Sheets[wb.SheetNames[0]]
        const jsonData = XLSX.utils.sheet_to_json<Record<string, any>>(ws, { header: 1 }) as any[][]

        if (jsonData.length < 2) {
          resolve([])
          return
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

        resolve(records)
      } catch (err) {
        reject(err)
      }
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsArrayBuffer(file)
  })
}

/**
 * 解析导入的 JSON 文件
 *
 * @param file - 上传的文件
 * @param fields - 字段配置
 * @returns 解析后的记录数组
 */
export function parseJsonImportFile(
  file: File,
  fields: FieldConfig[]
): Promise<Record<string, any>[]> {
  const exportFields = getExportableFields(fields)

  // 建立 label / fieldName → field 映射（同时支持两种 key 格式）
  const headerToField = new Map<string, FieldConfig>()
  exportFields.forEach((f) => {
    headerToField.set(f.label, f)
    if (!headerToField.has(f.fieldName)) {
      headerToField.set(f.fieldName, f)
    }
  })

  const ARRAY_FIELD_TYPES = ['multiSelect', 'checkbox', 'relation', 'quoteSelect']

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const text = e.target!.result as string
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

        resolve(records)
      } catch (err) {
        reject(err)
      }
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsText(file)
  })
}
