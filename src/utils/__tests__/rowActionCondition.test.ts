import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { evaluateRowActionCondition } from '../rowActionCondition'
import type { RowActionCondition } from '@/types/rowAction'

/**
 * 与后端 server/tests/test_row_action_condition.py 读同一份夹具。
 * 任何一边改了求值语义而没改另一边，这里就会红。
 */
interface Case {
  name: string
  condition: RowActionCondition | null
  data: Record<string, unknown>
  expected: boolean
}

const cases: Case[] = JSON.parse(
  readFileSync(
    resolve(process.cwd(), 'server/tests/fixtures/row_action_conditions.json'),
    'utf-8',
  ),
)

describe('evaluateRowActionCondition', () => {
  it('夹具非空（防止路径写错导致空跑通过）', () => {
    expect(cases.length).toBeGreaterThan(20)
  })

  for (const c of cases) {
    it(c.name, () => {
      expect(evaluateRowActionCondition(c.condition, c.data)).toBe(c.expected)
    })
  }

  it('空对象条件视为总是通过', () => {
    expect(evaluateRowActionCondition({} as RowActionCondition, { status: 'x' })).toBe(true)
  })
})
