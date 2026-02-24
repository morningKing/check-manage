/**
 * 字段类型定义 - 单元测试
 *
 * 测试 CONTROL_TYPE_OPTIONS、createEmptyFieldFormData 和 SequenceConfig。
 */

import { describe, it, expect } from 'vitest'
import {
  CONTROL_TYPE_OPTIONS,
  createEmptyFieldFormData,
} from '../field'

describe('Field Types', () => {
  describe('CONTROL_TYPE_OPTIONS', () => {
    it('包含 autoTimestamp 选项', () => {
      const opt = CONTROL_TYPE_OPTIONS.find(o => o.value === 'autoTimestamp')
      expect(opt).toBeDefined()
      expect(opt!.label).toBe('自动时间戳')
    })

    it('包含 autoSequence 选项', () => {
      const opt = CONTROL_TYPE_OPTIONS.find(o => o.value === 'autoSequence')
      expect(opt).toBeDefined()
      expect(opt!.label).toBe('自增序列')
    })

    it('包含 quoteSelect 选项', () => {
      const opt = CONTROL_TYPE_OPTIONS.find(o => o.value === 'quoteSelect')
      expect(opt).toBeDefined()
      expect(opt!.label).toBe('引用选择')
    })

    it('共 16 种控件类型', () => {
      expect(CONTROL_TYPE_OPTIONS).toHaveLength(16)
    })
  })

  describe('createEmptyFieldFormData', () => {
    it('返回默认表单数据', () => {
      const data = createEmptyFieldFormData()
      expect(data.controlType).toBe('text')
      expect(data.required).toBe(false)
      expect(data.order).toBe(1)
      expect(data.options).toEqual([])
    })

    it('包含 sequenceConfig 默认值', () => {
      const data = createEmptyFieldFormData()
      expect(data.sequenceConfig).toBeDefined()
      expect(data.sequenceConfig!.prefix).toBe('')
      expect(data.sequenceConfig!.max).toBe(999)
    })

    it('包含 referenceConfig 默认值', () => {
      const data = createEmptyFieldFormData()
      expect(data.referenceConfig).toBeDefined()
      expect(data.referenceConfig!.targetCollection).toBe('')
      expect(data.referenceConfig!.inheritFields).toEqual([])
    })

    it('包含 quoteConfig 默认值', () => {
      const data = createEmptyFieldFormData()
      expect(data.quoteConfig).toBeDefined()
      expect(data.quoteConfig!.targetCollection).toBe('')
      expect(data.quoteConfig!.displayField).toBe('')
    })

    it('接受自定义 order', () => {
      const data = createEmptyFieldFormData(5)
      expect(data.order).toBe(5)
    })
  })
})
