import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuMessageBubble from '../KefuMessageBubble.vue'
import type { KefuMessage } from '@/api/kefuPublic'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

const stubs = { MarkdownView: { template: '<div class="md"><slot/>{{ text }}</div>', props: ['text'] } }
const msg = (o: Partial<KefuMessage>): KefuMessage =>
  ({ id: 'm', role: 'assistant', content: [], createdAt: null, ...o })

describe('KefuMessageBubble', () => {
  it('visitor message: user class, right side, no avatar', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'user', content: [{ type: 'text', text: '你好' }] }) },
      global: { stubs },
    })
    expect(w.classes()).toContain('kmb--user')
    expect(w.find('.kmb__avatar').exists()).toBe(false)
    expect(w.text()).toContain('你好')
  })

  it('agent message: avatar shows name initial, content via MarkdownView', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'assistant', content: [{ type: 'text', text: '**hi**' }] }), agentName: '客服小助手' },
      global: { stubs },
    })
    expect(w.classes()).toContain('kmb--agent')
    expect(w.find('.kmb__avatar').text()).toBe('客')
    expect(w.find('.md').exists()).toBe(true)
  })

  it('agent avatar uses logo image when provided', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'assistant', content: [] }), agentName: '客服', agentLogo: 'https://x/logo.png' },
      global: { stubs },
    })
    const img = w.find('.kmb__avatar img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('https://x/logo.png')
  })

  it('renders HH:mm when createdAt present, hides when null', () => {
    const iso = new Date(2026, 6, 2, 14, 32).toISOString()  // 本地 14:32 → 组件解析回本地
    const w1 = mount(KefuMessageBubble, { props: { message: msg({ createdAt: iso }) }, global: { stubs } })
    expect(w1.find('.kmb__time').text()).toBe('14:32')
    const w2 = mount(KefuMessageBubble, { props: { message: msg({ createdAt: null }) }, global: { stubs } })
    expect(w2.find('.kmb__time').exists()).toBe(false)
  })

  it('renders file chips for file parts', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'user', content: [{ type: 'text', text: '看这个' }, { type: 'file', name: 'a.txt', path: 'uploads/a.txt' }] }) },
      global: { stubs },
    })
    const chips = w.findAll('.kmb__file')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('a.txt')
    expect(w.text()).toContain('看这个')
  })

  it('renders an image file part as an inline <img> pointing at the files endpoint', () => {
    const w = mount(KefuMessageBubble, { props: {
      sessionId: 'sess_1',
      message: { id: 'm1', role: 'user', content: [{ type: 'file', name: 'a.png', path: 'uploads/a.png' }], createdAt: null },
    } })
    const img = w.find('img.kmb__img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('/kefu/sessions/sess_1/files/a.png')
    expect(w.find('a.kmb__file').exists()).toBe(false)
  })

  it('renders a non-image file part as a download link, not an <img>', () => {
    const w = mount(KefuMessageBubble, { props: {
      sessionId: 'sess_1',
      message: { id: 'm2', role: 'user', content: [{ type: 'file', name: 'b.pdf', path: 'uploads/b.pdf' }], createdAt: null },
    } })
    expect(w.find('img.kmb__img').exists()).toBe(false)
    const a = w.find('a.kmb__file')
    expect(a.exists()).toBe(true)
    expect(a.attributes('href')).toContain('/kefu/sessions/sess_1/files/b.pdf')
    expect(a.attributes('download')).toBeDefined()
  })
})
