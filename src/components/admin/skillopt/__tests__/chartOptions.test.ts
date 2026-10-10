import { describe, it, expect } from 'vitest'
import { buildTrendOption, buildWaterfallOption, SOURCE_COLORS, sourceColor } from '../chartOptions'
import type { PerfTaskEntry, PerfAttemptDetail } from '@/api/aiSkills'

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

describe('SOURCE_COLORS / sourceColor', () => {
  it('四类来源着色（spec §5.1），未识别来源回落 batch 蓝', () => {
    expect(SOURCE_COLORS).toEqual({
      batch: '#409eff', interactive: '#67c23a',
      open_api: '#e6a23c', scan: '#909399',
    })
    expect(sourceColor('batch')).toBe('#409eff')
    expect(sourceColor('interactive')).toBe('#67c23a')
    expect(sourceColor('open_api')).toBe('#e6a23c')
    expect(sourceColor('scan')).toBe('#909399')
    expect(sourceColor('unknown')).toBe('#409eff')
    expect(sourceColor(undefined)).toBe('#409eff')
  })
})

const detail = (): PerfAttemptDetail => ({
  attempt: t({}) as any,
  coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 30000, idleMs: 10000,
              toolMs: 0 },
  turns: [{ messageId: 'm1', createdAt: '2026-10-09T10:00:00',
            durationMs: 60000, tokensIn: 5000, tokensOut: 500, preview: 'p', toolCount: 0 }],
  subtasks: [{ subtaskId: 'ses_1', agent: 'general', description: 'd',
               status: 'completed', startedAt: '2026-10-09T10:00:10',
               finishedAt: '2026-10-09T10:00:40', wallMs: 30000 }],
  tools: { errorCount: 0, repeats: [], byTool: [], durationAvailable: false,
           calls: [], callsTruncated: false },
  skills: [{ name: 'stock-analysis', source: 'runtime',
             evidenceLevel: 'confirmed', invokedAt: null, durationMs: 45000 }],
  completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
})

describe('buildWaterfallOption', () => {
  it('时间轴系列含模型段与子代理段，y 为段类型、x 为绝对时间', () => {
    const opt = buildWaterfallOption(detail())
    const data = (opt.series as any[])[0].data
    expect(data.length).toBe(2)                       // 1 个模型段 + 1 个子代理段
    expect(data[0].name).toContain('模型')
    expect(data[1].name).toContain('子代理')
    expect(opt.xAxis.type).toBe('time')
  })
})
