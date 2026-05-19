/**
 * 字段导入工具函数单元测试
 *
 * 测试 parseFieldRow 和 mergeFields 的解析与合并逻辑。
 */

import { describe, it, expect, vi } from 'vitest'
import { parseFieldRow, mergeFields } from '@/utils/fieldImport'
import type { FieldConfig, ControlType } from '@/types'

// Helper to create typed imported fields for tests
type ImportedField = Partial<FieldConfig> & { controlType?: ControlType }

// Mock uuid for predictable IDs
vi.mock('uuid', () => ({
  v4: () => '00000000-0000-0000-0000-000000000001',
}))

// ==================== parseFieldRow ====================

describe('parseFieldRow', () => {
  it('解析基础字段', () => {
    const row = {
      fieldName: 'name',
      label: '名称',
      controlType: 'text',
      required: 'true',
      order: '1',
      placeholder: '请输入名称',
    }

    const field = parseFieldRow(row)

    expect(field.fieldName).toBe('name')
    expect(field.label).toBe('名称')
    expect(field.controlType).toBe('text')
    expect(field.required).toBe(true)
    expect(field.order).toBe(1)
    expect(field.placeholder).toBe('请输入名称')
  })

  it('将字符串 true/false 转为布尔值', () => {
    const row = {
      fieldName: 'name',
      label: '名称',
      controlType: 'text',
      required: 'false',
      isPrimaryKey: 'true',
      hidden: '1',
      disabled: '0',
      readonly: true,
    }

    const field = parseFieldRow(row)

    expect(field.required).toBe(false)
    expect(field.isPrimaryKey).toBe(true)
    expect(field.hidden).toBe(true)
    expect(field.disabled).toBe(false)
  })

  it('解析 options JSON 字符串', () => {
    const row = {
      fieldName: 'status',
      label: '状态',
      controlType: 'select',
      options: JSON.stringify([
        { label: '开启', value: '1' },
        { label: '关闭', value: '0' },
      ]),
    }

    const field = parseFieldRow(row)

    expect(field.options).toEqual([
      { label: '开启', value: '1' },
      { label: '关闭', value: '0' },
    ])
  })

  it('relationConfig JSON 解析正确', () => {
    const row = {
      fieldName: 'relatedProduct',
      label: '关联产品',
      controlType: 'relation',
      relationConfig: JSON.stringify({
        targetCollection: 'test-products',
        displayField: 'productName',
        targetField: 'relatedCases',
      }),
    }

    const field = parseFieldRow(row)

    expect(field.relationConfig).toEqual({
      targetCollection: 'test-products',
      displayField: 'productName',
      targetField: 'relatedCases',
    })
  })

  it('referenceConfig JSON 解析正确', () => {
    const row = {
      fieldName: 'template',
      label: '测试模板',
      controlType: 'reference',
      referenceConfig: JSON.stringify({
        targetCollection: 'test-templates',
        displayField: 'templateName',
        inheritFields: ['description', 'category'],
      }),
    }

    const field = parseFieldRow(row)

    expect(field.referenceConfig).toEqual({
      targetCollection: 'test-templates',
      displayField: 'templateName',
      inheritFields: ['description', 'category'],
    })
  })

  it('quoteConfig JSON 解析正确', () => {
    const row = {
      fieldName: 'relatedCases',
      label: '引用用例',
      controlType: 'quoteSelect',
      quoteConfig: JSON.stringify({
        targetCollection: 'test-cases',
        displayField: 'name',
      }),
    }

    const field = parseFieldRow(row)

    expect(field.quoteConfig).toEqual({
      targetCollection: 'test-cases',
      displayField: 'name',
    })
  })

  it('sequenceConfig JSON 解析正确', () => {
    const row = {
      fieldName: 'caseNo',
      label: '编号',
      controlType: 'autoSequence',
      sequenceConfig: JSON.stringify({ prefix: 'TC-', max: 9999 }),
    }

    const field = parseFieldRow(row)

    expect(field.sequenceConfig).toEqual({ prefix: 'TC-', max: 9999 })
  })

  it('options 已经是对象时不重复 parse', () => {
    const row = {
      fieldName: 'status',
      label: '状态',
      controlType: 'select',
      options: [{ label: '开启', value: '1' }],
    }

    const field = parseFieldRow(row)

    expect(field.options).toEqual([{ label: '开启', value: '1' }])
  })

  it('无效 JSON 保持原字符串', () => {
    const row = {
      fieldName: 'status',
      label: '状态',
      controlType: 'select',
      options: '{invalid json}',
    }

    const field = parseFieldRow(row)

    expect(field.options).toBe('{invalid json}')
  })

  it('空值被跳过', () => {
    const row = {
      fieldName: 'name',
      label: '',
      controlType: 'text',
      required: null,
      order: undefined,
    }

    const field = parseFieldRow(row)

    expect(field.fieldName).toBe('name')
    expect(field.controlType).toBe('text')
    expect(field.label).toBeUndefined()
    expect(field.required).toBeUndefined()
    expect(field.order).toBeUndefined()
  })
})

// ==================== mergeFields ====================

describe('mergeFields', () => {
  function makeField(overrides: Partial<FieldConfig>): FieldConfig {
    return {
      id: 'field-1',
      fieldName: 'name',
      label: '名称',
      controlType: 'text',
      order: 0,
      required: false,
      ...overrides,
    } as FieldConfig
  }

  it('导入无 id 的行生成新 id 并追加', () => {
    const existing = [makeField({ id: 'field-1', fieldName: 'name', order: 1 })]
    const imported: ImportedField[] = [{ fieldName: 'desc', label: '描述', controlType: 'textarea', order: 2 }]

    const { merged, added, updated } = mergeFields(existing, imported)

    expect(merged).toHaveLength(2)
    expect(added).toBe(1)
    expect(updated).toBe(0)
    expect(merged[1].id).toMatch(/^field-/)
  })

  it('导入有 id 且已存在的行执行合并更新', () => {
    const existing = [
      makeField({ id: 'field-1', fieldName: 'name', label: '旧名称', controlType: 'text', order: 1 }),
    ]
    const imported = [{ id: 'field-1', label: '新名称' }]

    const { merged, added, updated } = mergeFields(existing, imported)

    expect(merged).toHaveLength(1)
    expect(updated).toBe(1)
    expect(added).toBe(0)
    expect(merged[0].label).toBe('新名称')
    expect(merged[0].fieldName).toBe('name') // 未更新的字段保持不变
  })

  it('导入有 id 但不存在的行追加', () => {
    const existing = [makeField({ id: 'field-1', fieldName: 'name', order: 1 })]
    const imported: ImportedField[] = [{ id: 'field-2', fieldName: 'status', label: '状态', controlType: 'select', order: 2 }]

    const { merged, added, updated } = mergeFields(existing, imported)

    expect(merged).toHaveLength(2)
    expect(added).toBe(1)
    expect(updated).toBe(0)
    expect(merged[1].fieldName).toBe('status')
  })

  it('按 order 字段重新排序', () => {
    const existing = [
      makeField({ id: 'field-1', fieldName: 'name', order: 3 }),
    ]
    const imported: ImportedField[] = [
      { id: 'field-2', fieldName: 'desc', label: '描述', controlType: 'textarea', order: 1 },
      { fieldName: 'status', label: '状态', controlType: 'select', order: 2 },
    ]

    const { merged } = mergeFields(existing, imported)

    expect(merged[0].order).toBe(1)
    expect(merged[0].fieldName).toBe('desc')
    expect(merged[1].order).toBe(2)
    expect(merged[1].fieldName).toBe('status')
    expect(merged[2].order).toBe(3)
    expect(merged[2].fieldName).toBe('name')
  })

  it('批量导入混合 id 和无 id 的行', () => {
    const existing = [
      makeField({ id: 'f1', fieldName: 'name', order: 1 }),
      makeField({ id: 'f2', fieldName: 'status', order: 2 }),
    ]
    const imported: ImportedField[] = [
      { id: 'f1', label: '用例名称' },            // 更新
      { id: 'f3', fieldName: 'desc', label: '描述', controlType: 'textarea', order: 3 }, // 追加
      { fieldName: 'priority', label: '优先级', controlType: 'radio', order: 4 },          // 生成 id 追加
    ]

    const { merged, updated, added } = mergeFields(existing, imported)

    expect(updated).toBe(1)
    expect(added).toBe(2)
    expect(merged).toHaveLength(4)
    expect(merged.find(f => f.id === 'f1')?.label).toBe('用例名称')
    expect(merged.find(f => f.id === 'f3')?.fieldName).toBe('desc')
    expect(merged.find(f => f.fieldName === 'priority')).toBeDefined()
  })

  it('合并时保留原有未修改字段', () => {
    const existing = [
      makeField({
        id: 'f1',
        fieldName: 'name',
        label: '名称',
        controlType: 'text',
        required: true,
        order: 1,
        placeholder: '请输入',
      }),
    ]
    const imported = [{ id: 'f1', label: '用例名称' }]

    const { merged } = mergeFields(existing, imported)

    expect(merged[0].label).toBe('用例名称')
    expect(merged[0].fieldName).toBe('name')
    expect(merged[0].required).toBe(true)
    expect(merged[0].placeholder).toBe('请输入')
  })

  it('空 existing 时全部追加', () => {
    const imported: ImportedField[] = [
      { id: 'f1', fieldName: 'name', label: '名称', controlType: 'text', order: 1 },
      { id: 'f2', fieldName: 'status', label: '状态', controlType: 'select', order: 2 },
    ]

    const { merged, added } = mergeFields([], imported)

    expect(merged).toHaveLength(2)
    expect(added).toBe(2)
  })

  it('空 imported 时保持原样', () => {
    const existing = [makeField({ id: 'f1', fieldName: 'name', order: 1 })]

    const { merged, updated, added } = mergeFields(existing, [])

    expect(merged).toEqual(existing)
    expect(updated).toBe(0)
    expect(added).toBe(0)
  })

  it('多个导入行更新同一现有行', () => {
    const existing = [makeField({ id: 'f1', fieldName: 'name', label: '初始', order: 1 })]
    const imported = [
      { id: 'f1', label: '第一次更新' },
      { id: 'f1', label: '第二次更新' },
    ]

    const { merged, updated } = mergeFields(existing, imported)

    expect(updated).toBe(2)
    expect(merged[0].label).toBe('第二次更新') // 最后一次覆盖
  })
})
