/**
 * 字段导入工具函数
 *
 * 将 Excel/CSV 行数据解析为 FieldConfig 数组，并与现有字段合并。
 * 纯函数，不涉及 Vue 组件状态。
 */

import type { FieldConfig } from '@/types'
import { v4 as uuidv4 } from 'uuid'

const COMPLEX_FIELD_KEYS = [
  'options', 'relationConfig', 'referenceConfig', 'quoteConfig',
  'sequenceConfig', 'workflowConfig', 'optionsSource'
] as const

const BOOL_FIELD_KEYS = [
  'required', 'isPrimaryKey', 'hidden', 'disabled', 'readonly'
] as const

const NUMBER_FIELD_KEYS = ['order', 'min', 'max'] as const

/**
 * 解析 Excel 行数据为字段配置
 */
export function parseFieldRow(row: Record<string, any>): Partial<FieldConfig> {
  const field: Partial<FieldConfig> = {}
  for (const [key, value] of Object.entries(row)) {
    if (value === undefined || value === null || value === '') continue

    if ((COMPLEX_FIELD_KEYS as readonly string[]).includes(key)) {
      try {
        (field as any)[key] = typeof value === 'string' ? JSON.parse(value) : value
      } catch {
        (field as any)[key] = value
      }
    } else if ((BOOL_FIELD_KEYS as readonly string[]).includes(key)) {
      (field as any)[key] = value === 'true' || value === true || value === 1 || value === '1'
    } else if ((NUMBER_FIELD_KEYS as readonly string[]).includes(key)) {
      (field as any)[key] = Number(value) || 0
    } else {
      (field as any)[key] = value
    }
  }
  return field
}

/**
 * 合并导入字段到现有字段中
 *
 * @returns { merged: 合并后的字段数组, updated: 更新数, added: 新增数 }
 */
export function mergeFields(existing: FieldConfig[], imported: Partial<FieldConfig>[]): {
  merged: FieldConfig[]
  updated: number
  added: number
} {
  const result: FieldConfig[] = [...existing]
  let updated = 0
  let added = 0

  for (const imp of imported) {
    if (!imp.id) {
      const newField = { ...imp, id: `field-${uuidv4().slice(0, 8)}` } as FieldConfig
      result.push(newField)
      added++
    } else {
      const idx = result.findIndex(f => f.id === imp.id)
      if (idx >= 0) {
        result[idx] = { ...result[idx], ...imp }
        updated++
      } else {
        result.push(imp as FieldConfig)
        added++
      }
    }
  }

  result.sort((a, b) => (a.order || 0) - (b.order || 0))
  return { merged: result, updated, added }
}
