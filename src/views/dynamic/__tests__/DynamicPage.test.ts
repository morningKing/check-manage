/**
 * DynamicPage 查看功能 — 辅助函数单元测试
 *
 * 测试 viewDisplayFields 的字段展开逻辑和日期格式化。
 */
import { describe, it, expect } from 'vitest'
import type { FieldConfig } from '@/types'

/**
 * 复刻 DynamicPage.viewDisplayFields 的逻辑，用于单元测试
 */
function buildViewDisplayFields(
  pageFields: FieldConfig[],
  getTargetFields: (collection: string) => FieldConfig[]
): FieldConfig[] {
  const result: FieldConfig[] = []
  for (const field of pageFields) {
    if (field.hidden) continue
    result.push(field)
    if (field.controlType === 'reference' && field.referenceConfig?.inheritFields?.length) {
      const config = field.referenceConfig
      const targetFields = getTargetFields(config.targetCollection)
      for (const inheritFieldName of config.inheritFields) {
        const parentField = targetFields.find((f) => f.fieldName === inheritFieldName)
        result.push({
          id: `_ref_${field.fieldName}_${inheritFieldName}`,
          fieldName: `_ref_${field.fieldName}_${inheritFieldName}`,
          label: parentField?.label || inheritFieldName,
          controlType: parentField?.controlType || 'text',
          required: false,
          order: field.order + 0.1,
          hidden: false,
          disabled: true,
          options: parentField?.options
        })
      }
    }
  }
  return result.sort((a, b) => a.order - b.order)
}

/**
 * 复刻 DynamicPage.formatViewDate 的逻辑
 */
function formatViewDate(value: any, controlType: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return String(value)
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    if (controlType === 'date') return `${y}-${m}-${d}`
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
  } catch {
    return String(value)
  }
}

/**
 * 复刻 DynamicPage.formatViewValue 的逻辑
 */
function formatViewValue(
  field: FieldConfig,
  record: Record<string, any>
): string {
  const value = record[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}

function makeField(overrides: Partial<FieldConfig> = {}): FieldConfig {
  return {
    id: 'f1',
    fieldName: 'name',
    label: '名称',
    controlType: 'text',
    order: 0,
    required: false,
    hidden: false,
    ...overrides,
  } as FieldConfig
}

describe('DynamicPage — 查看功能', () => {
  describe('viewDisplayFields', () => {
    it('普通字段直接包含在结果中', () => {
      const fields = [
        makeField({ id: 'f1', fieldName: 'name', label: '名称', order: 1 }),
        makeField({ id: 'f2', fieldName: 'age', label: '年龄', controlType: 'number', order: 2 }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('name')
      expect(result[1].fieldName).toBe('age')
    })

    it('隐藏字段不包含在结果中', () => {
      const fields = [
        makeField({ id: 'f1', fieldName: 'name', order: 1, hidden: false }),
        makeField({ id: 'f2', fieldName: 'secret', order: 2, hidden: true }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result).toHaveLength(1)
      expect(result[0].fieldName).toBe('name')
    })

    it('reference 字段展开继承字段', () => {
      const fields = [
        makeField({
          id: 'f1',
          fieldName: 'parentRef',
          label: '父记录',
          controlType: 'reference',
          order: 1,
          referenceConfig: {
            targetCollection: 'parents',
            displayField: 'parentName',
            inheritFields: ['status', 'level'],
          }
        }),
      ]
      const targetFields = [
        makeField({ fieldName: 'status', label: '状态', controlType: 'select', options: [{ label: '启用', value: 'active' }] }),
        makeField({ fieldName: 'level', label: '级别', controlType: 'text' }),
      ]
      const result = buildViewDisplayFields(fields, () => targetFields)
      expect(result).toHaveLength(3)
      expect(result[0].fieldName).toBe('parentRef')
      expect(result[1].fieldName).toBe('_ref_parentRef_status')
      expect(result[1].label).toBe('状态')
      expect(result[2].fieldName).toBe('_ref_parentRef_level')
    })

    it('结果按 order 排序', () => {
      const fields = [
        makeField({ id: 'f2', fieldName: 'b', order: 3 }),
        makeField({ id: 'f1', fieldName: 'a', order: 1 }),
      ]
      const result = buildViewDisplayFields(fields, () => [])
      expect(result[0].fieldName).toBe('a')
      expect(result[1].fieldName).toBe('b')
    })
  })

  describe('formatViewDate', () => {
    it('date 类型只显示日期', () => {
      expect(formatViewDate('2025-06-15T08:30:00Z', 'date')).toBe('2025-06-15')
    })

    it('datetime 类型显示日期和时间', () => {
      const result = formatViewDate('2025-06-15T08:30:00Z', 'datetime')
      expect(result).toContain('2025-06-15')
      expect(result).toContain(':')
    })

    it('空值返回 -', () => {
      expect(formatViewDate(null, 'date')).toBe('-')
      expect(formatViewDate('', 'date')).toBe('-')
      expect(formatViewDate(undefined, 'date')).toBe('-')
    })

    it('无效日期返回原始字符串', () => {
      expect(formatViewDate('not-a-date', 'date')).toBe('not-a-date')
    })
  })

  describe('formatViewValue', () => {
    it('选项字段返回标签', () => {
      const field = makeField({
        fieldName: 'status',
        controlType: 'select',
        options: [
          { label: '启用', value: 'active' },
          { label: '禁用', value: 'inactive' },
        ]
      })
      expect(formatViewValue(field, { status: 'active' })).toBe('启用')
    })

    it('未匹配选项返回原始值', () => {
      const field = makeField({
        fieldName: 'status',
        controlType: 'select',
        options: [{ label: '启用', value: 'active' }]
      })
      expect(formatViewValue(field, { status: 'unknown' })).toBe('unknown')
    })

    it('空值返回 -', () => {
      const field = makeField({ fieldName: 'status', controlType: 'select' })
      expect(formatViewValue(field, { status: null })).toBe('-')
      expect(formatViewValue(field, { status: '' })).toBe('-')
      expect(formatViewValue(field, {})).toBe('-')
    })
  })
})
