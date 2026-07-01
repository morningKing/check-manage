import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuSelfServicePanel from '../KefuSelfServicePanel.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

const items = [
  { id: 'a', question: '如何部署', answer: '**A**', category: '部署' },
  { id: 'b', question: '价格套餐', answer: 'B', category: '定价' },
  { id: 'c', question: '部署环境要求', answer: 'C', category: '部署' },
]
const stubs = {
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  'el-tag': { template: `<button @click="$emit('click')"><slot/></button>` },
  MdPreview: { template: `<div class="md"><slot/></div>`, props: ['modelValue'] },
}

describe('KefuSelfServicePanel', () => {
  it('filters by text over question', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).filter = '部署'
    await w.vm.$nextTick()
    expect((w.vm as any).visible.map((i: any) => i.id)).toEqual(['a', 'c'])
  })

  it('filters by category tag', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).activeTag = '定价'
    await w.vm.$nextTick()
    expect((w.vm as any).visible.map((i: any) => i.id)).toEqual(['b'])
  })

  it('emits click on expand and escalate on ask-AI', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).toggle('a')
    ;(w.vm as any).askAi(items[0])
    expect(w.emitted('click')?.[0]).toEqual(['a'])
    expect(w.emitted('escalate')?.[0]).toEqual(['如何部署'])
  })
})
