import { describe, it, expect } from 'vitest'
import { computeUsage, usageLevel, contextPercent, EMPTY_USAGE } from '../aiUsage'
import type { AiMessage } from '@/api/aiChat'

function msg(id: string, meta?: AiMessage['meta']): AiMessage {
  return { id, role: 'assistant', content: [], meta: meta ?? null }
}

const turn = (input: number, output: number, cost = 0, durationMs = 0) => ({
  tokensInput: input, tokensOutput: output, cost, durationMs,
})

describe('computeUsage', () => {
  it('returns empty usage for no messages', () => {
    expect(computeUsage([])).toStrictEqual({ ...EMPTY_USAGE })
    expect(computeUsage(undefined)).toStrictEqual({ ...EMPTY_USAGE })
  })

  it('derives context from the LAST assistant turn with meta', () => {
    const u = computeUsage([
      msg('a1', turn(1000, 200)),
      msg('a2', turn(5000, 800, 0.02)),
    ])
    // 当前上下文 = 最近回合 input+output（OpenCode 每步重发全量上下文，
    // aggregate_metas 回合内取 input 最大值，最后一条即当前上下文真实大小）
    expect(u.contextTokens).toBe(5800)
    expect(u.totalTokens).toBe(7000)
    expect(u.cost).toBe(0.02)
  })

  it('sums total across turns but context follows only the latest', () => {
    const u1 = computeUsage([msg('a1', turn(1000, 200))])
    const u2 = computeUsage([msg('a1', turn(1000, 200)), msg('a2', turn(3000, 500))])
    expect(u1.totalTokens).toBe(1200)
    expect(u2.totalTokens).toBe(4700)          // 累计：成本感知，单调递增
    expect(u2.contextTokens).toBe(3500)        // 上下文：只看最新回合
  })

  it('a compacted session drops back: context follows the small post-compact turn', () => {
    const u = computeUsage([
      msg('a1', turn(190000, 4000)),
      msg('a2', turn(6000, 300)),   // /compact 之后的回合，input 骤降
    ])
    expect(u.contextTokens).toBe(6300)
    expect(u.totalTokens).toBe(200300)
  })

  it('skips user messages and assistant messages without meta', () => {
    const u = computeUsage([
      { id: 'u1', role: 'user', content: [{ type: 'text', text: 'hi' }], meta: turn(999, 999) },
      msg('a0'),                                    // 旧消息（meta 落库前）
      msg('a1', turn(2000, 100, 0.01)),
      msg('a2', { tokensInput: 0, tokensOutput: 0, cost: 0 }),  // 全零 meta 也跳过
    ])
    expect(u.contextTokens).toBe(2100)
    expect(u.totalTokens).toBe(2100)
    expect(u.cost).toBe(0.01)
  })

  it('context is null when no turn ever reported tokens', () => {
    const u = computeUsage([msg('a0'), msg('a1')])
    expect(u.contextTokens).toBeNull()
    expect(u.totalTokens).toBe(0)
  })
})

describe('usageLevel', () => {
  it('thresholds: <70 ok, 70~90 warn, >=90 danger, unknown ok', () => {
    expect(usageLevel(null)).toBe('ok')
    expect(usageLevel(0)).toBe('ok')
    expect(usageLevel(69.9)).toBe('ok')
    expect(usageLevel(70)).toBe('warn')
    expect(usageLevel(89.9)).toBe('warn')
    expect(usageLevel(90)).toBe('danger')
    expect(usageLevel(99)).toBe('danger')
  })
})

describe('computeUsage token speed', () => {
  it('reports the LAST turn speed and the session average', () => {
    const u = computeUsage([
      msg('a1', turn(1000, 200, 0, 4000)),   // 50 tok/s
      msg('a2', turn(5000, 800, 0.02, 8000)), // 100 tok/s
    ])
    expect(u.lastTokPerSec).toBe(100)                 // 最近回合
    expect(u.avgTokPerSec).toBeCloseTo((1000 / 12000) * 1000, 6) // Σout/Σdur
  })

  it('turns without duration still count usage but yield no speed sample', () => {
    const u = computeUsage([
      msg('a1', turn(1000, 200)),              // 无时长（旧数据）
      msg('a2', turn(5000, 800, 0, 8000)),
    ])
    expect(u.totalTokens).toBe(7000)
    expect(u.lastTokPerSec).toBe(100)
    expect(u.avgTokPerSec).toBe(100)
  })

  it('zero-duration / zero-output samples are excluded', () => {
    const u = computeUsage([
      msg('a1', turn(1000, 0, 0, 5000)),   // 无输出 → 无速度意义
      msg('a2', turn(1000, 100, 0, 0)),    // 无时长 → 除零
    ])
    expect(u.lastTokPerSec).toBeNull()
    expect(u.avgTokPerSec).toBeNull()
    expect(u.totalTokens).toBe(2100)       // 用量不受影响
  })

  it('empty usage carries null speeds', () => {
    const u = computeUsage([msg('a1')])
    expect(u.lastTokPerSec).toBeNull()
    expect(u.avgTokPerSec).toBeNull()
  })
})

describe('contextPercent', () => {
  it('computes pct against the model context limit', () => {
    const u = { contextTokens: 10_000, totalTokens: 50_000, cost: 0, lastTokPerSec: null, avgTokPerSec: null }
    expect(contextPercent(u, 200_000)).toBe(5)
  })

  it('returns null without tokens or without a declared limit', () => {
    expect(contextPercent({ ...EMPTY_USAGE }, 200_000)).toBeNull()
    expect(contextPercent({ contextTokens: 1000, totalTokens: 1, cost: 0, lastTokPerSec: null, avgTokPerSec: null }, null)).toBeNull()
    expect(contextPercent({ contextTokens: 1000, totalTokens: 1, cost: 0, lastTokPerSec: null, avgTokPerSec: null }, 0)).toBeNull()
  })
})
