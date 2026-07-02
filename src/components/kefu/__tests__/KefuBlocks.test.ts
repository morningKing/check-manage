import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuBlockLinks from '../KefuBlockLinks.vue'
import { isSafeUrl } from '../urlSafe'
import KefuBlockContact from '../KefuBlockContact.vue'
import KefuBlockFaq from '../KefuBlockFaq.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

describe('isSafeUrl', () => {
  it('allows http/https/relative, rejects javascript:', () => {
    expect(isSafeUrl('https://x.com')).toBe(true)
    expect(isSafeUrl('http://x.com')).toBe(true)
    expect(isSafeUrl('/docs')).toBe(true)
    expect(isSafeUrl('./local')).toBe(true)
    expect(isSafeUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeUrl('data:text/html,x')).toBe(false)
    expect(isSafeUrl('//evil.com')).toBe(false)
  })
})

describe('KefuBlockLinks', () => {
  it('renders only safe-url items as anchors with rel/target', () => {
    const w = mount(KefuBlockLinks, { props: { config: { items: [
      { label: 'Docs', url: 'https://x.com/docs' },
      { label: 'Bad', url: 'javascript:alert(1)' },
    ] } } })
    const anchors = w.findAll('a')
    expect(anchors).toHaveLength(1)
    expect(anchors[0].attributes('rel')).toContain('noopener')
    expect(anchors[0].attributes('rel')).toContain('noreferrer')
    expect(anchors[0].attributes('target')).toBe('_blank')
  })
})

describe('KefuBlockContact', () => {
  it('renders only provided fields', () => {
    const w = mount(KefuBlockContact, { props: { config: { phone: '123', hours: '9-6' } } })
    expect(w.text()).toContain('123')
    expect(w.text()).toContain('9-6')
    expect(w.text()).not.toContain('undefined')
  })
})

describe('KefuBlockFaq', () => {
  it('limits items and re-emits click/escalate', async () => {
    const items = [
      { id: 'a', question: 'Q1', answer: 'A1', category: null },
      { id: 'b', question: 'Q2', answer: 'A2', category: null },
      { id: 'c', question: 'Q3', answer: 'A3', category: null },
    ]
    const w = mount(KefuBlockFaq, { props: { items, config: { limit: 2 } },
      global: { stubs: { KefuSelfServicePanel: {
        props: ['items'], emits: ['click','escalate'],
        template: `<div><button class="c" @click="$emit('click','a')"/><button class="e" @click="$emit('escalate','Q1')"/></div>` } } } })
    // limit applied: the stub received 2 items
    expect((w.vm as any).limited).toHaveLength(2)
    await w.find('button.c').trigger('click')
    await w.find('button.e').trigger('click')
    expect(w.emitted('click')?.[0]).toEqual(['a'])
    expect(w.emitted('escalate')?.[0]).toEqual(['Q1'])
  })
})
