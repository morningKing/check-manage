import { describe, it, expect } from 'vitest'
import { getExportableFields } from '../excel'
import type { FieldConfig } from '@/types'

// Only test the pure functions that don't depend on xlsx file I/O

function makeField(overrides: Partial<FieldConfig>): FieldConfig {
  return {
    fieldName: 'field1',
    label: '字段1',
    controlType: 'text',
    order: 0,
    hidden: false,
    required: false,
    ...overrides,
  } as FieldConfig
}

describe('Excel Utils', () => {
  describe('getExportableFields', () => {
    it('过滤可导出类型', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'name', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'avatar', controlType: 'image', order: 2 }),
        makeField({ fieldName: 'count', controlType: 'number', order: 3 }),
        makeField({ fieldName: 'file', controlType: 'file', order: 4 }),
      ]

      const result = getExportableFields(fields)
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('name')
      expect(result[1].fieldName).toBe('count')
    })

    it('排除 hidden 字段', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'visible', controlType: 'text', order: 1, hidden: false }),
        makeField({ fieldName: 'hidden', controlType: 'text', order: 2, hidden: true }),
      ]

      const result = getExportableFields(fields)
      expect(result).toHaveLength(1)
      expect(result[0].fieldName).toBe('visible')
    })

    it('按 order 排序', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'c', controlType: 'text', order: 3 }),
        makeField({ fieldName: 'a', controlType: 'text', order: 1 }),
        makeField({ fieldName: 'b', controlType: 'number', order: 2 }),
      ]

      const result = getExportableFields(fields)
      expect(result.map((f) => f.fieldName)).toEqual(['a', 'b', 'c'])
    })

    it('支持所有可导出类型', () => {
      const exportableTypes = ['text', 'textarea', 'number', 'date', 'datetime', 'select', 'multiSelect', 'radio', 'checkbox', 'relation', 'reference', 'autoTimestamp', 'autoSequence']
      const fields = exportableTypes.map((type, i) =>
        makeField({ fieldName: `f${i}`, controlType: type, order: i })
      )

      const result = getExportableFields(fields)
      expect(result).toHaveLength(exportableTypes.length)
    })

    it('autoSequence 字段可导出', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'seqNo', controlType: 'autoSequence', order: 1 }),
        makeField({ fieldName: 'name', controlType: 'text', order: 2 }),
      ]
      const result = getExportableFields(fields)
      expect(result).toHaveLength(2)
      expect(result[0].fieldName).toBe('seqNo')
    })

    it('autoTimestamp 字段可导出', () => {
      const fields: FieldConfig[] = [
        makeField({ fieldName: 'ts', controlType: 'autoTimestamp', order: 1 }),
      ]
      const result = getExportableFields(fields)
      expect(result).toHaveLength(1)
      expect(result[0].controlType).toBe('autoTimestamp')
    })

    it('空数组返回空数组', () => {
      expect(getExportableFields([])).toEqual([])
    })
  })
})
