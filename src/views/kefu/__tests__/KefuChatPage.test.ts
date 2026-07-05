import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
vi.mock('@/api/kefuPublic', () => ({
  getVisitorId: () => 'v1',
  getKefuConfig: vi.fn().mockResolvedValue({ slug: 's', name: 'KF', welcome_message: 'hi', guided_questions: ['价格?'], branding: {}, enabled: true, panel_blocks: [{ id: '1', type: 'faq', enabled: true, config: {} }] }),
  createKefuSession: vi.fn().mockResolvedValue({ id: 'sess_1', title: 't' }),
  getKefuFaq: vi.fn().mockResolvedValue({ items: [{ id: 'a', question: 'Q', answer: 'A', category: null }] }),
  getKefuHistory: vi.fn(() => Promise.resolve({ messages: [] })),
  sendKefuMessage: vi.fn().mockResolvedValue({ messageId: 'm1' }),
  clickKefuFaq: vi.fn().mockResolvedValue(undefined),
  createKefuEventStream: vi.fn().mockReturnValue(() => {}),
  createKefuHumanEventStream: vi.fn(() => () => {}),
  uploadKefuFile: vi.fn().mockResolvedValue({ name: 'a.txt', path: 'uploads/a.txt', size: 3 }),
  requestHuman: vi.fn().mockResolvedValue({ needsHuman: true }),
}))
import * as api from '@/api/kefuPublic'
import KefuChatPage from '../KefuChatPage.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
beforeEach(() => vi.clearAllMocks())
const stubs = { 'el-drawer': { template: '<div v-if="modelValue"><slot/></div>', props: ['modelValue'] },
  'el-button': { template: `<button @click="$emit('click')"><slot/></button>` },
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  MarkdownView: { template: '<div />', props: ['text'] },
  KefuServiceColumn: { props: ['blocks', 'faqItems'], template: '<div class="col" />' },
  KefuComposer: { template: '<div class="composer-stub" />', props: ['draft', 'pending', 'sending'] },
  KefuMessageBubble: { template: '<div class="bubble-stub" />', props: ['message', 'agentName', 'agentLogo'] } }

describe('KefuChatPage', () => {
  it('bootstraps config + session + faq on mount', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    expect(api.getKefuConfig).toHaveBeenCalledWith('s')
    expect(api.createKefuSession).toHaveBeenCalledWith('s')
    expect(api.getKefuFaq).toHaveBeenCalledWith('s')
    expect((w.vm as any).sessionId).toBe('sess_1')
  })

  it('escalate sends the question as a message', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).onEscalate('如何部署')
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '如何部署', [])
  })

  it('renders prompt bubbles from guided_questions and sends on click', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    // bubble click calls sendKefuMessage with the bubble text
    await (w.vm as any).askBubble('价格?')
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '价格?', [])
  })

  it('passes panel_blocks + faq to KefuServiceColumn', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    expect((w.vm as any).blocks).toHaveLength(1)
  })

  it('uploads picked files into pending', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).onPickFiles([new File(['abc'], 'a.txt')])
    expect((w.vm as any).pending).toEqual([{ name: 'a.txt', path: 'uploads/a.txt' }])
  })

  it('send passes pending attachments and clears them', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    ;(w.vm as any).pending = [{ name: 'a.txt', path: 'uploads/a.txt' }]
    ;(w.vm as any).draft = '看看这个'
    await (w.vm as any).send()
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '看看这个', ['uploads/a.txt'])
    expect((w.vm as any).pending).toEqual([])
  })

  it('can send with only attachments (no text)', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    ;(w.vm as any).pending = [{ name: 'a.txt', path: 'uploads/a.txt' }]
    ;(w.vm as any).draft = ''
    await (w.vm as any).send()
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '', ['uploads/a.txt'])
  })

  it('restores draft and pending on send failure', async () => {
    vi.mocked(api.sendKefuMessage).mockRejectedValueOnce(new Error('Network error'))
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    ;(w.vm as any).pending = [{ name: 'a.txt', path: 'uploads/a.txt' }]
    ;(w.vm as any).draft = '看看这个'
    const initialPending = (w.vm as any).pending
    await (w.vm as any).send()
    // after failure, draft and pending should be restored
    expect((w.vm as any).draft).toBe('看看这个')
    expect((w.vm as any).pending).toStrictEqual(initialPending)
    // optimistic message should be removed
    expect((w.vm as any).messages).toEqual([])
  })

  it('send that returns humanTakeover clears sending immediately + humanMode', async () => {
    vi.mocked(api.sendKefuMessage).mockResolvedValueOnce({ messageId: 'm1', humanTakeover: true } as any)
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    ;(w.vm as any).draft = '我要人工'
    await (w.vm as any).send()
    expect((w.vm as any).sending).toBe(false)
    expect((w.vm as any).humanMode).toBe(true)
  })

  it('opens human-event stream on mount and closes on unmount', async () => {
    const close = vi.fn()
    vi.mocked(api.createKefuHumanEventStream).mockReturnValue(close)
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    expect(api.createKefuHumanEventStream).toHaveBeenCalledWith('sess_1', expect.any(Object))
    w.unmount()
    expect(close).toHaveBeenCalled()
  })

  it('onTakeover enters humanMode and reloads', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    const calls = vi.mocked(api.createKefuHumanEventStream).mock.calls
    const h = (calls[calls.length - 1] as any)[1]
    vi.mocked(api.getKefuHistory).mockClear()
    h.onTakeover(); await flushPromises()
    expect((w.vm as any).humanMode).toBe(true)
    expect(api.getKefuHistory).toHaveBeenCalled()
  })

  it('onHumanMessage reloads history', async () => {
    mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    const calls = vi.mocked(api.createKefuHumanEventStream).mock.calls
    const h = (calls[calls.length - 1] as any)[1]
    vi.mocked(api.getKefuHistory).mockClear()
    h.onHumanMessage(); await flushPromises()
    expect(api.getKefuHistory).toHaveBeenCalled()
  })

  it('onRelease exits humanMode', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).requestHuman()
    expect((w.vm as any).humanMode).toBe(true)
    const calls = vi.mocked(api.createKefuHumanEventStream).mock.calls
    const h = (calls[calls.length - 1] as any)[1]
    h.onRelease(); await flushPromises()
    expect((w.vm as any).humanMode).toBe(false)
  })

  it('requestHuman enters humanMode', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).requestHuman()
    expect(api.requestHuman).toHaveBeenCalledWith('sess_1')
    expect((w.vm as any).humanMode).toBe(true)
  })
})
