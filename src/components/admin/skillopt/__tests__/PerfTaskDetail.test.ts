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
                         subtaskId: null, turnIndex: 0, isDelegation: false },
                       { partId: 'pc2', tool: 'read', args: '{"p": "a.py"}',
                         state: 'error', startedAt: '2026-10-10T12:00:20',
                         durationMs: null, subtaskId: 'ses_9ab', turnIndex: null,
                         isDelegation: false }] },
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
    expect(w.text()).toContain('工具 1 次 · 合计 32.0s')
    expect(w.text()).toContain('ls -la /data')
    expect(w.find('[data-test="turn-inference"]').text()).toContain('18.0s')
  })

  it('时间轴分支：段行显示工具参数（bash 执行了什么直接可见）', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    perfAttempt.mockResolvedValue({
      attempt: { attemptId: 'a1', wallMs: 60000, modelMs: 0, subagentWaitMs: 0,
                 idleMs: 0, toolMs: 0, sourceType: 'batch', status: 'completed',
                 turnCount: 1, subtaskCount: 0, sessionId: 's1', startedAt: null,
                 finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 60000, modelMs: 28000, subagentWaitMs: 0, idleMs: 0,
                  toolMs: 0 },
      turns: [{ messageId: 'm1', createdAt: '2026-10-12T10:00:00',
                durationMs: 30000, tokensIn: 1, tokensOut: 1, preview: 'p',
                toolCount: 1 }],
      subtasks: [],
      subTurns: {},
      tools: { errorCount: 0, durationAvailable: true, byTool: [], repeats: [],
               callsTruncated: false,
               calls: [{ partId: 'px1', tool: 'bash',
                         args: 'command=git clone https://x/y.git\ndescription=克隆',
                         state: 'completed',
                         startedAt: '2026-10-12T10:00:05', durationMs: 5000,
                         subtaskId: null, turnIndex: 0, isDelegation: false }] },
      skills: [],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    } as any)
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    // turn-seq-list 在轮次表展开行内——先触发展开
    await w.find('.el-table__expand-icon').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    const seq = w.find('[data-test="turn-seq-list"]')
    expect(seq.exists()).toBe(true)
    // 段行显示 bash 的参数首行（command=…），不再只有「bash 5.0s」
    expect(seq.text()).toContain('command=git clone https://x/y.git')
  })


  it('task 委派行标注「开子代理」且不计入工具小计', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 30000, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 0, tokensOut: 0,
                 subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 30000, idleMs: 0,
                  toolMs: 0 },
      turns: [{ messageId: 'm1', createdAt: null, durationMs: 50000,
                tokensIn: 0, tokensOut: 0, preview: 'p', toolCount: 2 }],
      subtasks: [{ subtaskId: 'ses_del', agent: 'general', description: 'd',
                   status: 'completed', startedAt: null, finishedAt: null,
                   wallMs: 30000 }],
      tools: { errorCount: 0, durationAvailable: true,
               byTool: [{ tool: 'bash', count: 1, totalMs: 5000 }],
               repeats: [],
               callsTruncated: false,
               calls: [{ partId: 'pk1', tool: 'task',
                         args: '统计行数', state: 'completed',
                         startedAt: '2026-10-10T12:00:10', durationMs: 30000,
                         subtaskId: 'ses_del', turnIndex: 0, isDelegation: true },
                       { partId: 'pk2', tool: 'bash', args: '{"command":"ls"}',
                         state: 'completed', startedAt: '2026-10-10T12:00:40',
                         durationMs: 5000, subtaskId: null, turnIndex: 0,
                         isDelegation: false }] },
      skills: [],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn, ElTag },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.el-table__expand-icon').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    // 委派行显式标注，工具小计只含非委派，委派等待单列
    expect(w.text()).toContain('开子代理')
    expect(w.text()).toContain('工具 1 次 · 合计 5.0s')
    expect(w.text()).toContain('开子代理 1 次 · 等待 30.0s')
    // byTool 聚合表不含 task 行（委派不是工具执行）
    expect(w.find('[data-test="tool-perf-table"]').text()).not.toContain('task')
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
                         subtaskId: null, turnIndex: 0, isDelegation: false },
                       { partId: 'pd2', tool: 'read', args: '{}',
                         state: 'completed', startedAt: null, durationMs: null,
                         subtaskId: null, turnIndex: 0, isDelegation: false }] },
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

  it('涉子代理的轮次：委派等待计入小计、子代理自身调用归嵌套不进根列表', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
                 subagentWaitMs: 20000, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
                 subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 20000, idleMs: 0,
                  toolMs: 10000 },
      // createdAt=null → 时间轴不可重建，走平铺明细分支（断言轮内列表口径）
      turns: [{ messageId: 'm1', createdAt: null, durationMs: 40000,
                tokensIn: 5000, tokensOut: 500, preview: 'p', toolCount: 2 }],
      subtasks: [{ subtaskId: 'ses_ch', agent: 'general', description: 'd',
                   status: 'completed', startedAt: null, finishedAt: null,
                   wallMs: 20000 }],
      tools: { errorCount: 0, durationAvailable: true, byTool: [], repeats: [],
               callsTruncated: false,
               calls: [
                 { partId: 'p-deleg', tool: 'task', args: '统计行数',
                   state: 'completed', startedAt: null, durationMs: 20000,
                   subtaskId: 'ses_ch', turnIndex: 0, isDelegation: true },
                 { partId: 'p-child', tool: 'read', args: '{"p":"a"}',
                   state: 'completed', startedAt: null, durationMs: 5000,
                   subtaskId: 'ses_ch', turnIndex: 0, isDelegation: false },
                 { partId: 'p-root', tool: 'bash', args: '{"command":"ls"}',
                   state: 'completed', startedAt: null, durationMs: 10000,
                   subtaskId: null, turnIndex: 0, isDelegation: false }] },
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn, ElTag },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.el-table__expand-icon').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    const calls = w.find('[data-test="turn-calls"]')
    // 根列表：委派 + 根工具；子代理自己的 read 不进（它归嵌套分解）
    expect(calls.text()).toContain('开子代理')
    expect(calls.text()).toContain('统计行数')
    expect(calls.text()).toContain('ls')
    expect(calls.text()).not.toContain('a"')      // read 的参数不在根列表
    // 小计分行：工具（不含委派/子代理）+ 委托等待；推理 = 40 − 20 − 10
    expect(calls.text()).toContain('工具 1 次 · 合计 10.0s')
    expect(calls.text()).toContain('开子代理 1 次 · 等待 20.0s')
    expect(calls.find('[data-test="turn-inference"]').text()).toContain('10.0s')
  })

  it('时间轴嵌套段：子代理自己的工具调用显示参数（不回落段标签）', async () => {
    const { perfAttempt } = vi.mocked(await import('@/api/aiSkills')) as any
    ;(perfAttempt as any).mockImplementationOnce(async () => ({
      attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 50000, modelRatio: 0.5,
                 subagentWaitMs: 30000, idleMs: 0, sourceType: 'batch',
                 status: 'completed', turns: 1, tokensIn: 0, tokensOut: 0,
                 subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
                 completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
      coverage: { wallMs: 100000, modelMs: 50000, subagentWaitMs: 30000, idleMs: 0,
                  toolMs: 0 },
      turns: [{ messageId: 'm1', createdAt: '2026-10-12T10:00:00',
                durationMs: 30000, tokensIn: 0, tokensOut: 0, preview: 'p',
                toolCount: 2 }],
      subtasks: [{ subtaskId: 'ses_ch', agent: 'general', description: 'd',
                   status: 'completed', startedAt: null, finishedAt: null,
                   wallMs: 15000 }],
      subTurns: {},
      tools: { errorCount: 0, durationAvailable: true, byTool: [], repeats: [],
               callsTruncated: false,
               calls: [
                 { partId: 'pdt', tool: 'task', args: 'description=读取股票清单',
                   state: 'completed', startedAt: '2026-10-12T10:00:05',
                   durationMs: 15000, subtaskId: 'ses_ch', turnIndex: 0,
                   isDelegation: true },
                 { partId: 'pdc', tool: 'read',
                   args: 'filePath=C:\\ws\\stock-list.txt',
                   state: 'completed', startedAt: '2026-10-12T10:00:08',
                   durationMs: 23, subtaskId: 'ses_ch', turnIndex: 0,
                   isDelegation: false },
                 { partId: 'pdr', tool: 'bash', args: 'command=wc -l x',
                   state: 'completed', startedAt: '2026-10-12T10:00:25',
                   durationMs: 2000, subtaskId: null, turnIndex: 0,
                   isDelegation: false }] },
      skills: [],
      completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
    }))
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1' },
      global: { components: { ElDrawer, ElAlert, ElTable, ElTableColumn },
                directives: { loading: {} } },
    })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.el-table__expand-icon').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    const seq = w.find('[data-test="turn-seq-list"]')
    expect(seq.exists()).toBe(true)
    // 嵌套段（子代理自己的 read）显示其参数首行——此前按本轮 turnCalls 查
    // 引用（子代理调用已被排除）查不到，回落成「[general] read 23ms」标签
    expect(seq.text()).toContain('filePath=C:\\ws\\stock-list.txt')
    // 委派段/根工具段参数照常
    expect(seq.text()).toContain('description=读取股票清单')
    expect(seq.text()).toContain('command=wc -l x')
  })
})
