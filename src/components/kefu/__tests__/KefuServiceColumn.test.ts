import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuServiceColumn from '../KefuServiceColumn.vue'
import type { PanelBlock } from '@/api/kefuPublic'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
const stubs = {
  KefuBlockLinks: { props: ['config'], template: '<div class="b-links" />' },
  KefuBlockRichtext: { props: ['config'], template: '<div class="b-rt" />' },
  KefuBlockContact: { props: ['config'], template: '<div class="b-contact" />' },
  KefuBlockFaq: { props: ['items','config'], emits: ['click','escalate'],
    template: `<div class="b-faq"><button @click="$emit('click','x')"/><button class="e" @click="$emit('escalate','Q')"/></div>` },
}

describe('KefuServiceColumn', () => {
  it('renders only enabled blocks dispatched by type', () => {
    const blocks: PanelBlock[] = [
      { id: '1', type: 'links', enabled: true, config: {} },
      { id: '2', type: 'contact', enabled: false, config: {} },
      { id: '3', type: 'faq', enabled: true, config: { limit: 5 } },
    ]
    const w = mount(KefuServiceColumn, { props: { blocks, faqItems: [] }, global: { stubs } })
    expect(w.find('.b-links').exists()).toBe(true)
    expect(w.find('.b-contact').exists()).toBe(false)  // disabled
    expect(w.find('.b-faq').exists()).toBe(true)
  })

  it('bubbles faq click/escalate', async () => {
    const blocks: PanelBlock[] = [{ id: '3', type: 'faq', enabled: true, config: {} }]
    const w = mount(KefuServiceColumn, { props: { blocks, faqItems: [] }, global: { stubs } })
    await w.find('.b-faq button').trigger('click')
    await w.find('.b-faq button.e').trigger('click')
    expect(w.emitted('faqClick')?.[0]).toEqual(['x'])
    expect(w.emitted('escalate')?.[0]).toEqual(['Q'])
  })
})
