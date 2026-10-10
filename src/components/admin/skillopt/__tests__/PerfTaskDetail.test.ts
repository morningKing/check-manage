import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElDrawer, ElAlert, ElTable, ElTableColumn, ElTag } from 'element-plus'

vi.mock('@/api/aiSkills', async (orig) => ({
  ...(await (orig as any)()),
  perfAttempt: vi.fn(async () => ({
    attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
               subagentWaitMs: 30000, idleMs: 10000, sourceType: 'batch',
               status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
               subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
               completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
    coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 30000, idleMs: 10000,
                toolMs: 0 },
    turns: [{ messageId: 'm1', createdAt: null, durationMs: 60000,
              tokensIn: 5000, tokensOut: 500, preview: 'p', toolCount: 0 }],
    subtasks: [{ subtaskId: 'ses_1', agent: 'general', description: 'd',
                 status: 'completed', startedAt: null, finishedAt: null, wallMs: 30000 }],
    tools: { errorCount: 0, repeats: [] },
    completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
  })),
  perfAttemptDiagnosis: vi.fn(async () => ({
    diagnoses: [{ ruleId: 'subagent_wait_dominant', severity: 'warn',
                  text: '68% 时间在等子代理',
                  anchor: { type: 'subtask', ref: 'ses_1' } }],
  })),
}))

vi.mock('../useEcharts', () => ({
  useEcharts: () => ({ ready: { value: false }, setOption: vi.fn() }),
}))

import PerfTaskDetail from '../PerfTaskDetail.vue'

describe('PerfTaskDetail', () => {
  it('渲染覆盖条各段、子代理表与诊断列表', async () => {
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      // ElTableColumn 的作用域插槽依赖真实组件渲染；不注册会退化为普通元素、
      // 插槽被无参调用导致 { row } 解构报错（同 PerfView.test 约定）
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="cov-model"]').attributes('style')).toContain('60%')
    expect(w.find('[data-test="cov-wait"]').attributes('style')).toContain('30%')
    expect(w.text()).toContain('general')
    expect(w.text()).toContain('68% 时间在等子代理')
    // 口径说明：四段互斥 + 表格毛跨度提示（对账指引）
    expect(w.find('[data-test="coverage-note"]').exists()).toBe(true)
    expect(w.text()).toContain('之和 = 墙钟')
    expect(w.text()).toContain('直接相加会大于墙钟')
  })

  it('诊断接口失败不影响主数据渲染（仅提示，不整页报错）', async () => {
    const { perfAttemptDiagnosis } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttemptDiagnosis as any).mockRejectedValueOnce(new Error('diag down'))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="cov-model"]').exists()).toBe(true)   // 主数据在
    expect(w.text()).toContain('诊断加载失败')
    expect(w.text()).not.toContain('68% 时间在等子代理')
  })

  it('工具耗时表渲染 byTool 聚合（时长/占比），覆盖条含工具段', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 30000, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
                 subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 30000, idleMs: 0,
                  toolMs: 20000 },
      turns: [{ messageId: 'm1', createdAt: null, durationMs: 50000,
                tokensIn: 5000, tokensOut: 500, preview: 'p', toolCount: 1 }],
      subtasks: [],
      tools: { errorCount: 1, durationAvailable: true,
               byTool: [{ tool: 'bash', count: 2, totalMs: 40000 },
                        { tool: 'read', count: 3, totalMs: 10000 }],
               repeats: [],
               callsTruncated: false,
               calls: [{ partId: 'pc1', tool: 'bash',
                         args: '{"command": "ls -la /data"}', state: 'completed',
                         startedAt: '2026-10-10T12:00:05', durationMs: 32000,
                         subtaskId: null, turnIndex: 0 },
                       { partId: 'pc2', tool: 'read', args: '{"p": "a.py"}',
                         state: 'error', startedAt: '2026-10-10T12:00:20',
                         durationMs: null, subtaskId: 'ses_9ab', turnIndex: null }] },
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="tool-perf-table"]').exists()).toBe(true)
    expect(w.text()).toContain('bash')
    expect(w.text()).toContain('40.0s')
    expect(w.find('[data-test="cov-tool"]').attributes('style')).toContain('20%')
    // 逐调用明细：bash 命令文本 + 各自耗时（null 显 '-'）+ 子代理归属
    expect(w.find('[data-test="tool-call-list"]').exists()).toBe(true)
    expect(w.text()).toContain('ls -la /data')
    expect(w.text()).toContain('32.0s')
    expect(w.text()).toContain('子代理 ses_9ab')
    // 轮次表工具数列 + 展开行小计（ElTable 展开内容需触发展开才渲染）
    expect(w.text()).toContain('1 次')
    // 推理列：轮毛 50s − 轮内 bash 32s = 18s（全部调用有时长 → 精确值）
    expect(w.find('[data-test="turn-inference-cell"]').text()).toBe('18.0s')
    await w.find('.el-table__expand-icon').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('本轮工具 1 次 · 合计 32.0s')
    expect(w.text()).toContain('ls -la /data')
    expect(w.find('[data-test="turn-inference"]').text()).toContain('18.0s')
  })

  it('轮内有未采集时长调用时推理值显下界（≥）', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 0, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 0, tokensOut: 0,
                 subtaskCount: 0, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 0, idleMs: 0,
                  toolMs: 0 },
      turns: [{ messageId: 'm9', createdAt: null, durationMs: 60000,
                tokensIn: 0, tokensOut: 0, preview: 'p', toolCount: 2 }],
      subtasks: [],
      tools: { errorCount: 0, durationAvailable: true,
               byTool: [], repeats: [],
               callsTruncated: false,
               calls: [{ partId: 'pd1', tool: 'bash', args: '{}',
                         state: 'completed', startedAt: null, durationMs: 10000,
                         subtaskId: null, turnIndex: 0 },
                       { partId: 'pd2', tool: 'read', args: '{}',
                         state: 'completed', startedAt: null, durationMs: null,
                         subtaskId: null, turnIndex: 0 }] },
      skills: [],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    // 60s 毛 − 10s（另一调用未采集）→ 推理 ≥ 50s
    expect(w.find('[data-test="turn-inference-cell"]').text()).toContain('≥')
    expect(w.find('[data-test="turn-inference-cell"]').text()).toContain('50.0s')
  })

  it('Skill 耗时表渲染 runtime 精确值与来源徽标，无记录显示空态', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 0, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
                 subtaskCount: 0, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 0, idleMs: 0,
                  toolMs: 0 },
      turns: [], subtasks: [],
      tools: { errorCount: 0, durationAvailable: false, byTool: [], repeats: [] },
      skills: [{ name: 'stock-analysis', source: 'runtime',
                 evidenceLevel: 'confirmed', invokedAt: null, durationMs: 45000 },
               { name: 'ghost-skill', source: 'heuristic',
                 evidenceLevel: 'inferred', invokedAt: null, durationMs: null }],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn, ElTag },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="skill-perf-table"]').exists()).toBe(true)
    expect(w.text()).toContain('stock-analysis')
    expect(w.text()).toContain('45.0s')
    expect(w.text()).toContain('未采集')          // inferred 无时长 → 宁缺不估
  })

  it('无 skill 调用记录时显示空态文案', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 0, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 0, tokensIn: 0, tokensOut: 0,
                 subtaskCount: 0, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 0, idleMs: 0,
                  toolMs: 0 },
      turns: [], subtasks: [],
      tools: { errorCount: 0, durationAvailable: false, byTool: [], repeats: [] },
      skills: [],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="skill-perf-empty"]').exists()).toBe(true)
  })
})
