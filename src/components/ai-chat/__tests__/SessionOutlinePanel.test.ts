import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SessionOutlinePanel from '@/components/ai-chat/SessionOutlinePanel.vue'
import type { SessionTurn } from '@/api/aiChat'

const today = new Date().toISOString()
const oldDay = new Date(Date.now() - 3 * 86400000).toISOString()
const turns: SessionTurn[] = [
  { id: 't1', seq: 1, createdAt: oldDay, preview: '旧回合' },
  { id: 't2', seq: 9, createdAt: today, preview: '今天的问题' },
]

describe('SessionOutlinePanel', () => {
  it('renders turns; cross-day entries get MM-DD prefix, today only HH:mm', () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: null, locatedId: null, error: false },
    })
    expect(w.findAll('[data-test="outline-item"]')).toHaveLength(2)
    expect(w.find('[data-turn-id="t1"] .outline__time').text()).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(w.find('[data-turn-id="t2"] .outline__time').text()).toMatch(/^\d{2}:\d{2}$/)
  })

  it('emits locate with the clicked turn', async () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: null, locatedId: null, error: false },
    })
    await w.find('[data-turn-id="t2"]').trigger('click')
    expect(w.emitted('locate')![0][0]).toEqual(turns[1])
  })

  it('is-active follows activeSeq; is-located follows locatedId (independent)', () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: 9, locatedId: 't1', error: false },
    })
    expect(w.find('[data-turn-id="t2"]').classes()).toContain('is-active')
    expect(w.find('[data-turn-id="t1"]').classes()).toContain('is-located')
  })

  it('error state shows inline retry; header collapse emits', async () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns: [], activeSeq: null, locatedId: null, error: true },
    })
    expect(w.find('[data-test="outline-error"]').exists()).toBe(true)
    await w.find('[data-test="outline-retry"]').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
    await w.find('[data-test="outline-collapse"]').trigger('click')
    expect(w.emitted('collapse')).toHaveLength(1)
  })

  it('flags truncation at 500 entries', () => {
    const many: SessionTurn[] = Array.from({ length: 500 }, (_, i) => ({ id: 'x' + i, preview: 'p' }))
    const w = mount(SessionOutlinePanel, {
      props: { turns: many, activeSeq: null, locatedId: null, error: false },
    })
    expect(w.find('.outline__truncated').exists()).toBe(true)
  })
})
