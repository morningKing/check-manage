import { describe, it, expect } from 'vitest'
import { buildTrendOption } from '../chartOptions'
import type { PerfTaskEntry } from '@/api/aiSkills'

const t = (over: Partial<PerfTaskEntry>): PerfTaskEntry => ({
  attemptId: 'a', sessionId: 's', sourceType: 'batch', status: 'completed',
  startedAt: '2026-10-09T10:00:00', finishedAt: null, wallMs: 100000,
  modelMs: 60000, modelRatio: 0.6, subagentWaitMs: 30000, idleMs: 10000,
  turns: 2, tokensIn: 1000, tokensOut: 100, subtaskCount: 1,
  completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 }, ...over })

describe('buildTrendOption', () => {
  it('墙钟柱系列 + 模型占比折线系列，x 轴为时间序', () => {
    const opt = buildTrendOption([t({}), t({ startedAt: '2026-10-09T10:05:00' })])
    expect(opt.xAxis.data).toHaveLength(2)
    const bar = opt.series.find((s: any) => s.name === '墙钟')!
    const line = opt.series.find((s: any) => s.name === '模型占比')!
    expect(bar.data).toEqual([100000, 100000])
    expect(line.data).toEqual([0.6, 0.6])
  })

  it('柱色按 sourceType 映射（__sources 供组件层 color 回调使用）', () => {
    const opt = buildTrendOption([t({ sourceType: 'interactive' })])
    expect((opt.series as any[])[0].__sources).toEqual(['interactive'])
  })
})
