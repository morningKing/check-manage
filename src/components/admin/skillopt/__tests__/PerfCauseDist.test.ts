import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PerfCauseDist from '../PerfCauseDist.vue'
import type { PerfCauseDistribution, PerfCauseSegment } from '@/api/aiSkills'

const dist: PerfCauseDistribution = {
  taskCount: 3,
  shares: { wallMs: 300000, modelMs: 150000, toolMs: 60000,
            subagentWaitMs: 60000, idleMs: 30000 },
  dominantCounts: { model: 2, wait: 1, tool: 0, idle: 0 },
  taskCauses: [
    { attemptId: 'a1', dominant: 'model' },
    { attemptId: 'a2', dominant: 'model' },
    { attemptId: 'a3', dominant: 'wait' },
  ],
}

function mountDist(d: PerfCauseDistribution,
                  active: PerfCauseSegment | null = null) {
  return mount(PerfCauseDist, { props: { dist: d, active } })
}

describe('PerfCauseDist', () => {
  it('堆叠条按四段占比铺排且图例带百分比与时长', () => {
    const w = mountDist(dist)
    expect(w.find('[data-test="cause-seg-model"]').attributes('style'))
      .toContain('width: 50%')     // jsdom 会把 50.00% 规范化为 50%
    expect(w.find('[data-test="cause-seg-tool"]').attributes('style'))
      .toContain('width: 20%')
    const legend = w.find('[data-test="cause-legend"]').text()
    expect(legend).toContain('模型推理 50%')
    expect(legend).toContain('2m30s')        // 150000ms
    expect(legend).toContain('引擎间隙 10%')
  })

  it('主导慢因计数只列非零段并点击发出 toggle', async () => {
    const w = mountDist(dist)
    const chips = w.findAll('.cause-chip')
    expect(chips.length).toBe(2)             // tool/idle 为 0 不出芯片
    expect(chips[0].text()).toContain('模型推理 2 个')
    expect(chips[1].text()).toContain('子代理等待 1 个')
    await chips[0].trigger('click')
    expect(w.emitted('toggle')![0]).toEqual(['model'])
  })

  it('激活芯片带 active 态（父组件据此高亮）', () => {
    const w = mountDist(dist, 'model')
    expect(w.find('[data-test="cause-chip-model"]').classes()).toContain('active')
    expect(w.find('[data-test="cause-chip-wait"]').classes()).not.toContain('active')
  })

  it('零任务显示占位文案不渲染条形', () => {
    const w = mountDist({ ...dist, taskCount: 0, taskCauses: [],
      dominantCounts: { model: 0, wait: 0, tool: 0, idle: 0 },
      shares: { wallMs: 0, modelMs: 0, toolMs: 0, subagentWaitMs: 0, idleMs: 0 } })
    expect(w.find('[data-test="cause-empty"]').exists()).toBe(true)
    expect(w.find('[data-test="cause-bar"]').exists()).toBe(false)
  })
})
