import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SkillFitBadge from '../SkillFitBadge.vue'

const stubs = {
  'el-tag': { template: '<span class="tag"><slot /></span>' },
}

const ps = (status: string) => ({ id: 's', name: '步骤', status, evidence: [] as unknown[] })

describe('SkillFitBadge', () => {
  it('renders one dot per perStep entry with the status class', () => {
    const w = mount(SkillFitBadge, {
      props: {
        stepsTotal: 4,
        perStep: [ps('hit'), ps('hit'), ps('miss'), ps('skipped')],
        score: 50,
        status: 'partial',
      },
      global: { stubs },
    })
    const dots = w.findAll('.skillfit-dot')
    expect(dots).toHaveLength(4)
    expect(dots.filter(d => d.classes().includes('is-hit'))).toHaveLength(2)
    expect(dots.filter(d => d.classes().includes('is-miss'))).toHaveLength(1)
    expect(dots.filter(d => d.classes().includes('is-skipped'))).toHaveLength(1)
  })

  it('renders the score as text', () => {
    const w = mount(SkillFitBadge, {
      props: { stepsTotal: 2, perStep: [ps('hit'), ps('hit')], score: 100, status: 'fit' },
      global: { stubs },
    })
    expect(w.find('.skillfit-score').text()).toBe('100')
  })

  it('maps statuses to badge labels', () => {
    const cases: Array<[string, string]> = [
      ['fit', '拟合'],
      ['partial', '部分拟合'],
      ['diverged', '偏离'],
      ['no_trace', '无轨迹'],
      ['parse_error', '解析失败'],
    ]
    for (const [status, label] of cases) {
      const w = mount(SkillFitBadge, {
        props: { stepsTotal: 0, perStep: [], score: 0, status },
        global: { stubs },
      })
      expect(w.find('.skillfit-status').text()).toBe(label)
    }
  })

  it('keeps unknown statuses visible as raw text', () => {
    const w = mount(SkillFitBadge, {
      props: { stepsTotal: 0, perStep: [], score: 0, status: 'whatever' },
      global: { stubs },
    })
    expect(w.find('.skillfit-status').text()).toBe('whatever')
  })
})
