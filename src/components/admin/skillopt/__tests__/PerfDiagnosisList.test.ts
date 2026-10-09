import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import PerfDiagnosisList from '../PerfDiagnosisList.vue'
import type { Diagnosis } from '@/api/aiSkills'

const ds: Diagnosis[] = [
  { ruleId: 'subagent_wait_dominant', severity: 'warn', text: '68% 时间在等子代理',
    anchor: { type: 'subtask', ref: 'ses_1' } },
  { ruleId: 'model_dominant', severity: 'info', text: '时间主要花在模型推理',
    anchor: { type: 'segment', ref: 'turns' } },
]

describe('PerfDiagnosisList', () => {
  it('按 severity 渲染 warn/info 样式与定位按钮', () => {
    const w = mount(PerfDiagnosisList, { props: { diagnoses: ds } })
    expect(w.findAll('.diag--warn')).toHaveLength(1)
    expect(w.findAll('.diag--info')).toHaveLength(1)
    expect(w.findAll('button[data-test="diag-locate"]')).toHaveLength(2)
  })

  it('定位点击触发锚点高亮（subtask ref）', async () => {
    document.body.innerHTML = '<div data-diag-anchor="subtask:ses_1">行</div>'
    // jsdom 未实现 scrollIntoView，stub 掉以免 locate() 中断
    ;(document.querySelector('[data-diag-anchor="subtask:ses_1"]') as any)
      .scrollIntoView = vi.fn()
    const w = mount(PerfDiagnosisList, { props: { diagnoses: ds }, attachTo: document.body })
    await w.findAll('button[data-test="diag-locate"]')[0].trigger('click')
    const el = document.querySelector('[data-diag-anchor="subtask:ses_1"]')!
    expect(el.className).toContain('diag-flash')
    w.unmount()
  })

  it('turn 锚走行类回退链（.diag-anchor-turn-<ref> 优先于 segment 表头）', async () => {
    document.body.innerHTML =
      '<div data-diag-anchor="segment:m9">表头</div>' +
      '<div class="diag-anchor-turn-m9">轮次行</div>'
    for (const el of document.querySelectorAll('.diag-anchor-turn-m9, [data-diag-anchor="segment:m9"]')) {
      ;(el as any).scrollIntoView = vi.fn()
    }
    const w = mount(PerfDiagnosisList, {
      props: { diagnoses: [{ ruleId: 'slow_turn_big_context', severity: 'warn',
                             text: '第 1 轮过慢',
                             anchor: { type: 'turn', ref: 'm9' } }] },
      attachTo: document.body,
    })
    await w.findAll('button[data-test="diag-locate"]')[0].trigger('click')
    expect(document.querySelector('.diag-anchor-turn-m9')!.className)
      .toContain('diag-flash')                       // 命中行锚而非表头
    expect(document.querySelector('[data-diag-anchor="segment:m9"]')!.className)
      .not.toContain('diag-flash')
    w.unmount()
  })
})
