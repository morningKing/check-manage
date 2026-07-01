import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
vi.mock('@/api/kefuPublic', () => ({
  getVisitorId: () => 'v1',
  getKefuConfig: vi.fn().mockResolvedValue({ slug: 's', name: 'KF', welcome_message: 'hi', guided_questions: [], branding: {}, enabled: true }),
  createKefuSession: vi.fn().mockResolvedValue({ id: 'sess_1', title: 't' }),
  getKefuFaq: vi.fn().mockResolvedValue({ items: [{ id: 'a', question: 'Q', answer: 'A', category: null }] }),
  getKefuHistory: vi.fn().mockResolvedValue({ messages: [] }),
  sendKefuMessage: vi.fn().mockResolvedValue({ messageId: 'm1' }),
  clickKefuFaq: vi.fn().mockResolvedValue(undefined),
  createKefuEventStream: vi.fn().mockReturnValue(() => {}),
}))
import * as api from '@/api/kefuPublic'
import KefuChatPage from '../KefuChatPage.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
beforeEach(() => vi.clearAllMocks())
const stubs = { 'el-drawer': { template: '<div v-if="modelValue"><slot/></div>', props: ['modelValue'] },
  'el-button': { template: `<button @click="$emit('click')"><slot/></button>` },
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  MarkdownView: { template: '<div />', props: ['text'] },
  KefuSelfServicePanel: { template: '<div class="ssp" />', props: ['items'] } }

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
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '如何部署')
  })
})
