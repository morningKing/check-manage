import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElTabs, ElTabPane, ElTable, ElTableColumn, ElCard, ElAlert, ElButton } from 'element-plus'

// PerfTrendChart 实体化后，PerfView 渲染会触发 echarts 动态 import——
// jsdom 下虽已 try/catch 降级，mock 掉更快更稳
vi.mock('../useEcharts', () => ({
  useEcharts: () => ({ ready: { value: false }, setOption: vi.fn() }),
}))

vi.mock('@/api/aiSkills', () => ({
  perfOverview: vi.fn(async () => ({
    defs: [{ defKind: 'skill', defName: 'stock-analysis', tasks: 3,
             p50Ms: 20000, p95Ms: 90000, avgModelRatio: 0.6,
             lastActivity: '2026-10-09T10:00:00' }],
  })),
  perfDefTasks: vi.fn(async () => ({
    tasks: [{ attemptId: 'a1', sessionId: 's1', sourceType: 'batch', status: 'completed',
              startedAt: '2026-10-09T10:00:00', finishedAt: '2026-10-09T10:01:40',
              wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
              subagentWaitMs: 30000, idleMs: 10000, turns: 3,
              tokensIn: 50000, tokensOut: 4000, subtaskCount: 1,
              completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } }],
  })),
  perfSlowTasks: vi.fn(async () => ({ tasks: [] })),
  perfAttempt: vi.fn(),
  perfAttemptDiagnosis: vi.fn(),
}))

import PerfView from '../PerfView.vue'

describe('PerfView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('首屏渲染定义清单与慢任务 Top 卡片', async () => {
    const w = mount(PerfView, { global: { components: { ElTabs, ElTabPane, ElTable, ElTableColumn, ElCard, ElAlert, ElButton }, directives: { loading: {} } } })
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('stock-analysis')
    expect(w.text()).toContain('最慢任务')
  })

  it('点击定义加载任务列表并渲染墙钟', async () => {
    const w = mount(PerfView, { global: { components: { ElTabs, ElTabPane, ElTable, ElTableColumn, ElCard, ElAlert, ElButton }, directives: { loading: {} } } })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.perf-def-item').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('1m40s')       // wallMs=100000 经 fmtMs 格式化后出现在任务表格
  })
})
