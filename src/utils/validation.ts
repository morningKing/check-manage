/**
 * 表单验证工具函数
 *
 * 提供通用的表单验证规则和验证方法
 * 用于动态表单的字段验证
 */

import type { FormItemRule } from 'element-plus'
import type { FieldConfig } from '@/types'

/**
 * 生成必填验证规则
 *
 * @param label - 字段标签，用于错误提示
 * @param trigger - 触发时机
 * @returns 验证规则
 */
export function requiredRule(label: string, trigger: 'blur' | 'change' = 'blur'): FormItemRule {
  return {
    required: true,
    message: `${label}不能为空`,
    trigger
  }
}

/**
 * 生成字符串长度验证规则
 *
 * @param min - 最小长度
 * @param max - 最大长度
 * @param trigger - 触发时机
 * @returns 验证规则
 */
export function lengthRule(
  min: number,
  max: number,
  trigger: 'blur' | 'change' = 'blur'
): FormItemRule {
  return {
    min,
    max,
    message: `长度应在 ${min} 到 ${max} 个字符之间`,
    trigger
  }
}

/**
 * 生成正则验证规则
 *
 * @param pattern - 正则表达式
 * @param message - 错误提示
 * @param trigger - 触发时机
 * @returns 验证规则
 */
export function patternRule(
  pattern: RegExp,
  message: string,
  trigger: 'blur' | 'change' = 'blur'
): FormItemRule {
  return {
    pattern,
    message,
    trigger
  }
}

/**
 * 常用正则表达式
 */
export const PATTERNS = {
  /** 邮箱 */
  EMAIL: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
  /** 手机号（中国大陆） */
  PHONE: /^1[3-9]\d{9}$/,
  /** 身份证号 */
  ID_CARD: /(^\d{15}$)|(^\d{18}$)|(^\d{17}(\d|X|x)$)/,
  /** URL */
  URL: /^(https?:\/\/)?([\da-z.-]+)\.([a-z.]{2,6})([/\w .-]*)*\/?$/,
  /** 中文字符 */
  CHINESE: /^[\u4e00-\u9fa5]+$/,
  /** 字母数字 */
  ALPHANUMERIC: /^[a-zA-Z0-9]+$/,
  /** 字母数字下划线 */
  ALPHANUMERIC_UNDERSCORE: /^[a-zA-Z0-9_]+$/
}

/**
 * 根据字段配置生成 Element Plus 验证规则
 *
 * 核心功能：将字段配置转换为 Element Plus 表单验证规则
 *
 * @param field - 字段配置
 * @returns Element Plus 验证规则数组
 */
export function generateFieldRules(field: FieldConfig): FormItemRule[] {
  if (field.controlType === 'autoTimestamp') return []

  const rules: FormItemRule[] = []

  // 必填验证
  if (field.required) {
    // 根据控件类型确定触发时机
    const trigger = ['select', 'multiSelect', 'date', 'datetime', 'radio', 'checkbox', 'relation', 'reference'].includes(
      field.controlType
    )
      ? 'change'
      : 'blur'

    rules.push({
      required: true,
      message: `请${getActionText(field.controlType)}${field.label}`,
      trigger
    })
  }

  // 处理自定义验证规则
  if (field.validation && field.validation.length > 0) {
    field.validation.forEach((rule) => {
      switch (rule.type) {
        case 'min':
          rules.push({
            min: rule.value,
            message: rule.message,
            trigger: 'blur'
          })
          break
        case 'max':
          rules.push({
            max: rule.value,
            message: rule.message,
            trigger: 'blur'
          })
          break
        case 'pattern':
          rules.push({
            pattern: new RegExp(rule.value),
            message: rule.message,
            trigger: 'blur'
          })
          break
      }
    })
  }

  return rules
}

/**
 * 根据控件类型获取操作文本
 *
 * @param controlType - 控件类型
 * @returns 操作文本（输入/选择/上传）
 */
function getActionText(controlType: string): string {
  switch (controlType) {
    case 'select':
    case 'multiSelect':
    case 'radio':
    case 'checkbox':
    case 'date':
    case 'datetime':
    case 'relation':
    case 'reference':
      return '选择'
    case 'file':
    case 'image':
      return '上传'
    default:
      return '输入'
  }
}

/**
 * 根据字段配置列表生成完整的表单验证规则
 *
 * @param fields - 字段配置列表
 * @returns 验证规则对象 { fieldName: rules[] }
 */
export function generateFormRules(
  fields: FieldConfig[]
): Record<string, FormItemRule[]> {
  const rules: Record<string, FormItemRule[]> = {}

  fields.forEach((field) => {
    const fieldRules = generateFieldRules(field)
    if (fieldRules.length > 0) {
      rules[field.fieldName] = fieldRules
    }
  })

  return rules
}
