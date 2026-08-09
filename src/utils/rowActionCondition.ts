/**
 * 行动作可见性条件求值（前端半边）
 *
 * 与 server/utils/row_action_condition.py 逐条对齐，两边共读
 * server/tests/fixtures/row_action_conditions.json 夹具。
 * 前端求值只决定按钮显不显示；能不能执行由后端复核。
 */
import type { RowActionCondition } from '@/types/rowAction'

const OPERATORS = ['eq', 'ne', 'in', 'notIn', 'empty', 'notEmpty'] as const

/**
 * 与后端 _as_text() 一致：null/undefined -> ''，布尔 -> 'true'/'false'。
 *
 * 后端对整数值浮点数（如 3.0）额外做了归一（str(int(v)) -> '3'），是为了迁就
 * JS 的 String()：JS 里 number 没有 int/float 之分，String(3.0) 本来就是 '3'，
 * 所以这里不需要、也不能加对应分支。
 */
function asText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}

export function evaluateRowActionCondition(
  condition: RowActionCondition | null | undefined,
  data: Record<string, unknown>,
): boolean {
  if (!condition) return true
  const field = condition.field
  if (!field) return true
  const op = condition.operator
  if (!OPERATORS.includes(op)) return false

  const actual = asText((data ?? {})[field])
  const expected = condition.value

  switch (op) {
    case 'empty':
      return actual === ''
    case 'notEmpty':
      return actual !== ''
    case 'eq':
      return actual === asText(expected)
    case 'ne':
      return actual !== asText(expected)
    case 'in':
    case 'notIn': {
      if (!Array.isArray(expected)) return false
      const hit = expected.map(asText).includes(actual)
      return op === 'in' ? hit : !hit
    }
    default:
      return false
  }
}
