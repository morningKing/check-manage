import { describe, it, expect } from 'vitest'
import { formatDuration, formatTokens, formatCost, summarizeMeta } from '../aiMeta'

describe('formatDuration', () => {
  it('formats ms / s / m', () => {
    expect(formatDuration(5)).toBe('5ms')
    expect(formatDuration(999)).toBe('999ms')
    expect(formatDuration(4378)).toBe('4.4s')
    expect(formatDuration(63000)).toBe('1m3s')
  })
  it('empty for null/negative', () => {
    expect(formatDuration(null)).toBe('')
    expect(formatDuration(undefined)).toBe('')
    expect(formatDuration(-1)).toBe('')
  })
})

describe('formatTokens', () => {
  it('formats with k', () => {
    expect(formatTokens(159)).toBe('159')
    expect(formatTokens(18886)).toBe('18.9k')
  })
  it('empty for null', () => {
    expect(formatTokens(null)).toBe('')
    expect(formatTokens(undefined)).toBe('')
  })
})

describe('formatCost', () => {
  it('formats', () => {
    expect(formatCost(0)).toBe('$0')
    expect(formatCost(0.0021)).toBe('$0.0021')
    expect(formatCost(1.5)).toBe('$1.50')
  })
  it('empty for null', () => {
    expect(formatCost(null)).toBe('')
  })
})

describe('summarizeMeta', () => {
  it('combines duration + tokens + cost', () => {
    expect(summarizeMeta({ durationMs: 4378, tokensInput: 18886, tokensOutput: 159, cost: 0 }))
      .toBe('⏱ 4.4s · ↑18.9k ↓159 · $0')
  })
  it('omits missing pieces', () => {
    expect(summarizeMeta({ durationMs: 2000 })).toBe('⏱ 2.0s')
    expect(summarizeMeta({})).toBe('')
    expect(summarizeMeta(null)).toBe('')
  })
})
