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
 * 与后端 _as_text() 一致：null/undefined -> ''，布尔 -> 'true'/'false'，
 * 数组/对象（非标量）-> ''。
 *
 * 数组/对象一律视为 ''：JS 对它们的 String() 结果（Array 会 join(',')，单元素
 * 数组会退化成元素本身如 String(['a'])==='a'；对象是 '[object Object]'）既跟
 * 后端 Python 的 str() 对不上，语义上也不是"标量值等于/属于"该有的比较对象。
 * multiSelect/checkbox/file/image 存的就是数组，relation/reference/quoteSelect
 * 干脆不在这行的 data 里——统一按"不是可比较的标量"处理，empty/notEmpty 仍按
 * 直觉工作（空数组=空），eq/in 这类精确比较则总是不命中。
 *
 * 后端对整数值浮点数（如 3.0）额外做了归一（str(int(v)) -> '3'），是为了迁就
 * JS 的 String()：JS 里 number 没有 int/float 之分，String(3.0) 本来就是 '3'，
 * 所以这里不需要、也不能加对应分支。
 */
function asText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return ''
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
