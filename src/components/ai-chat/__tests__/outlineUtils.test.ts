import { describe, it, expect } from 'vitest'
import { nearestUserTurnSeq, previewOfParts } from '../outlineUtils'
import type { AiMessage, AiContentPart } from '@/api/aiChat'

const m = (id: string, role: AiMessage['role'], seq?: number): AiMessage =>
  ({ id, role, content: [], seq })

describe('nearestUserTurnSeq', () => {
  const msgs: AiMessage[] = [
    m('u1', 'user', 2), m('a1', 'assistant', 3), m('a2', 'assistant', 5), m('u2', 'user', 8),
  ]
  it('maps a visible index back to the latest user turn at or before it', () => {
    expect(nearestUserTurnSeq(msgs, 2)).toBe(2)
    // brief 原文此行写作 toBe(2)，与测试名「at or before it」、实现及 Task 5
    // scroll-spy 用法（inclusive：msgs[3] 即用户回合 u2, seq 8）矛盾，按语义修正。
    expect(nearestUserTurnSeq(msgs, 3)).toBe(8)
  })
  it('returns null when nothing above the first user turn', () => {
    const above = [m('a0', 'assistant', 1), ...msgs]
    expect(nearestUserTurnSeq(above, 0)).toBe(null)
  })
  it('skips local user messages without seq', () => {
    const withLocal: AiMessage[] = [...msgs, { ...m('local_1', 'user'), seq: undefined }]
    expect(nearestUserTurnSeq(withLocal, 4)).toBe(8)
  })
})

describe('previewOfParts', () => {
  it('prefers first non-empty text part, whitespace-collapsed, capped at 160', () => {
    expect(previewOfParts([{ type: 'text', text: '  hello \n world  ' }])).toBe('hello world')
    expect(previewOfParts([{ type: 'text', text: 'x'.repeat(200) }])).toHaveLength(160)
  })
  it('falls back to file names with 📎 and 等 N 个', () => {
    const f = (n: string): AiContentPart => ({ type: 'file', name: n, path: 'uploads/' + n })
    expect(previewOfParts([f('a.xlsx'), f('b.csv')])).toBe('📎 a.xlsx、b.csv')
    expect(previewOfParts([f('1'), f('2'), f('3'), f('4')])).toBe('📎 1、2、3 等 4 个')
  })
  it('placeholder when nothing to show', () => {
    expect(previewOfParts([])).toBe('（无文本）')
    expect(previewOfParts([{ type: 'text', text: '   ' }])).toBe('（无文本）')
  })
})
