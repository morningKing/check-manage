/**
 * 表单验证工具函数 - 单元测试
 */

import { describe, it, expect } from 'vitest'
import {
  requiredRule,
  lengthRule,
  patternRule,
  PATTERNS,
  generateFieldRules,
  generateFormRules,
} from '../validation'
import type { FieldConfig } from '@/types'

// ==================== requiredRule ====================

describe('requiredRule', () => {
  it('生成必填规则，默认 blur 触发', () => {
    const rule = requiredRule('名称')
    expect(rule.required).toBe(true)
    expect(rule.message).toBe('名称不能为空')
    expect(rule.trigger).toBe('blur')
  })

  it('可指定 change 触发', () => {
    const rule = requiredRule('类型', 'change')
    expect(rule.trigger).toBe('change')
  })
})

// ==================== lengthRule ====================

describe('lengthRule', () => {
  it('生成长度规则', () => {
    const rule = lengthRule(2, 20)
    expect(rule.min).toBe(2)
    expect(rule.max).toBe(20)
    expect(rule.message).toContain('2')
    expect(rule.message).toContain('20')
    expect(rule.trigger).toBe('blur')
  })

  it('可指定 change 触发', () => {
    const rule = lengthRule(1, 10, 'change')
    expect(rule.trigger).toBe('change')
  })
})

// ==================== patternRule ====================

describe('patternRule', () => {
  it('生成正则规则', () => {
    const rule = patternRule(/^\d+$/, '只能输入数字')
    expect(rule.pattern).toEqual(/^\d+$/)
    expect(rule.message).toBe('只能输入数字')
    expect(rule.trigger).toBe('blur')
  })
})

// ==================== PATTERNS ====================

describe('PATTERNS', () => {
  describe('EMAIL', () => {
    it('匹配有效邮箱', () => {
      expect(PATTERNS.EMAIL.test('user@example.com')).toBe(true)
      expect(PATTERNS.EMAIL.test('test.name@domain.cn')).toBe(true)
    })
    it('不匹配无效邮箱', () => {
      expect(PATTERNS.EMAIL.test('invalid')).toBe(false)
      expect(PATTERNS.EMAIL.test('@domain.com')).toBe(false)
      expect(PATTERNS.EMAIL.test('user@')).toBe(false)
    })
  })

  describe('PHONE', () => {
    it('匹配有效手机号', () => {
      expect(PATTERNS.PHONE.test('13800138000')).toBe(true)
      expect(PATTERNS.PHONE.test('19912345678')).toBe(true)
    })
    it('不匹配无效手机号', () => {
      expect(PATTERNS.PHONE.test('12345678901')).toBe(false)
      expect(PATTERNS.PHONE.test('1380013800')).toBe(false)
      expect(PATTERNS.PHONE.test('2380013800')).toBe(false)
    })
  })

  describe('ID_CARD', () => {
    it('匹配 18 位身份证', () => {
      expect(PATTERNS.ID_CARD.test('110101199003071234')).toBe(true)
      expect(PATTERNS.ID_CARD.test('11010119900307123X')).toBe(true)
    })
    it('不匹配无效身份证', () => {
      expect(PATTERNS.ID_CARD.test('1234')).toBe(false)
    })
  })
})

// ==================== generateFieldRules ====================

describe('generateFieldRules', () => {
  it('必填 text 字段生成 blur 触发规则', () => {
    const field: FieldConfig = {
      fieldName: 'name',
      label: '名称',
      controlType: 'text',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules.length).toBeGreaterThanOrEqual(1)
    expect(rules[0].required).toBe(true)
    expect(rules[0].trigger).toBe('blur')
    expect(rules[0].message).toContain('输入')
  })

  it('必填 select 字段生成 change 触发规则', () => {
    const field: FieldConfig = {
      fieldName: 'type',
      label: '类型',
      controlType: 'select',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules[0].trigger).toBe('change')
    expect(rules[0].message).toContain('选择')
  })

  it('必填 date 字段生成 change 触发规则', () => {
    const field: FieldConfig = {
      fieldName: 'date',
      label: '日期',
      controlType: 'date',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules[0].trigger).toBe('change')
  })

  it('必填 relation 字段生成 change 触发规则', () => {
    const field: FieldConfig = {
      fieldName: 'rel',
      label: '关联',
      controlType: 'relation',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules[0].trigger).toBe('change')
  })

  it('非必填字段生成空规则', () => {
    const field: FieldConfig = {
      fieldName: 'desc',
      label: '描述',
      controlType: 'textarea',
      required: false,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules.length).toBe(0)
  })

  it('autoTimestamp 字段跳过验证', () => {
    const field: FieldConfig = {
      fieldName: 'updatedTime',
      label: '更新时间',
      controlType: 'autoTimestamp',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules.length).toBe(0)
  })

  it('autoSequence 字段跳过验证', () => {
    const field: FieldConfig = {
      fieldName: 'seqNo',
      label: '编号',
      controlType: 'autoSequence',
      required: true,
    } as FieldConfig
    const rules = generateFieldRules(field)
    expect(rules.length).toBe(0)
  })

  it('带自定义验证规则的字段', () => {
    const field: FieldConfig = {
      fieldName: 'code',
      label: '编码',
      controlType: 'text',
      required: true,
      validation: [
        { type: 'min', value: 3, message: '最少3个字符' },
        { type: 'max', value: 20, message: '最多20个字符' },
        { type: 'pattern', value: '^[A-Z]+$', message: '只能大写字母' },
      ],
    } as unknown as FieldConfig
    const rules = generateFieldRules(field)
    // 1 required + 3 custom
    expect(rules.length).toBe(4)
  })
})

// ==================== generateFormRules ====================

describe('generateFormRules', () => {
  it('批量生成表单规则', () => {
    const fields: FieldConfig[] = [
      { fieldName: 'name', label: '名称', controlType: 'text', required: true },
      { fieldName: 'desc', label: '描述', controlType: 'textarea', required: false },
      { fieldName: 'type', label: '类型', controlType: 'select', required: true },
    ] as FieldConfig[]

    const rules = generateFormRules(fields)
    expect(Object.keys(rules)).toContain('name')
    expect(Object.keys(rules)).toContain('type')
    // desc 非必填，不应出现在 rules 中
    expect(Object.keys(rules)).not.toContain('desc')
  })

  it('空字段列表返回空对象', () => {
    const rules = generateFormRules([])
    expect(rules).toEqual({})
  })
})
