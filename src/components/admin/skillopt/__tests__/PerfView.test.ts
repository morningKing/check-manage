import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElTabs, ElTabPane, ElTable, ElTableColumn, ElCard, ElAlert, ElButton } from 'element-plus'

// PerfTrendChart 实体化后，PerfView 渲染会触发 echarts 动态 import——
// jsdom 下虽已 try/catch 降级，mock 掉更快更稳
vi.mock('../useEcharts', () => ({
  useEcharts: () => ({ ready: { value: false }, setOption: vi.fn(), on: vi.fn() }),
}))

vi.mock('@/api/aiSkills', () => ({
  perfOverview: vi.fn(async () => ({
    defs: [{ defKind: 'skill', defName: 'stock-analysis', tasks: 3,
             p50Ms: 20000, p95Ms: 90000, avgModelRatio: 0.6,
             lastActivity: '2026-10-09T10:00:00' }],
  })),
  perfDefTasks: vi.fn(async () => ({
    tasks: [
      { attemptId: 'a1', sessionId: 's1', sourceType: 'batch', status: 'completed',
        startedAt: '2026-10-09T10:00:00', finishedAt: '2026-10-09T10:01:40',
        wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
        subagentWaitMs: 30000, idleMs: 10000, turns: 3,
        tokensIn: 50000, tokensOut: 4000, subtaskCount: 1,
        completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      { attemptId: 'a2', sessionId: 's2', sourceType: 'batch', status: 'completed',
        startedAt: '2026-10-09T11:00:00', finishedAt: '2026-10-09T11:01:00',
        wallMs: 60000, modelMs: 10000, modelRatio: 0.2,
        subagentWaitMs: 0, idleMs: 50000, turns: 1,
        tokensIn: 8000, tokensOut: 500, subtaskCount: 0,
        completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
    ],
  })),
  perfDefCauseDistribution: vi.fn(async () => ({
    taskCount: 2,
    shares: { wallMs: 160000, modelMs: 70000, toolMs: 20000,
              subagentWaitMs: 30000, idleMs: 60000 },
    dominantCounts: { model: 1, wait: 0, tool: 0, idle: 1 },
    taskCauses: [{ attemptId: 'a1', dominant: 'model' },
                 { attemptId: 'a2', dominant: 'idle' }],
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

  it('定义视图渲染慢因分布，点击主导慢因筛选任务表', async () => {
    const w = mount(PerfView, { global: { components: { ElTabs, ElTabPane, ElTable, ElTableColumn, ElCard, ElAlert, ElButton }, directives: { loading: {} } } })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.perf-def-item').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="cause-dist-block"]').exists()).toBe(true)
    expect(w.text()).toContain('慢因分布')
    // 未筛选：两行任务，各行带慢因徽标
    expect(w.findAll('.el-table__row').length).toBe(2)
    expect(w.text()).toContain('引擎间隙')
    // 点击「引擎间隙」芯片 → 只剩 a2（idle 主导）
    await w.find('[data-test="cause-chip-idle"]').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.findAll('.el-table__row').length).toBe(1)
    expect(w.text()).not.toContain('1m40s')   // a1 被筛掉
    // 再点一次取消筛选 → 两行恢复
    await w.find('[data-test="cause-chip-idle"]').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.findAll('.el-table__row').length).toBe(2)
  })
})
