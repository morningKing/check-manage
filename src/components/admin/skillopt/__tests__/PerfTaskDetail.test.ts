import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElDrawer, ElAlert, ElTable, ElTableColumn } from 'element-plus'

vi.mock('@/api/aiSkills', async (orig) => ({
  ...(await (orig as any)()),
  perfAttempt: vi.fn(async () => ({
    attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
               subagentWaitMs: 30000, idleMs: 10000, sourceType: 'batch',
               status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
               subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
               completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
    coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 30000, idleMs: 10000 },
    turns: [{ messageId: 'm1', createdAt: null, durationMs: 60000,
              tokensIn: 5000, tokensOut: 500, preview: 'p' }],
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
  it('渲染覆盖条三段、子代理表与诊断列表', async () => {
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1', peerP50Ms: 20000 },
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
  })
})
