import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockList = vi.fn(), mockMsgs = vi.fn(), mockTakeover = vi.fn(), mockRelease = vi.fn(), mockReply = vi.fn()
vi.mock('@/api/kefu', () => ({
  listSessions: (...a: any[]) => mockList(...a),
  getSessionMessages: (...a: any[]) => mockMsgs(...a),
  takeoverSession: (...a: any[]) => mockTakeover(...a),
  releaseSession: (...a: any[]) => mockRelease(...a),
  humanReply: (...a: any[]) => mockReply(...a),
}))
vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn() } }))
import KefuTakeoverPanel from '../KefuTakeoverPanel.vue'

const stubs = {
  'el-radio-group': { template: '<div><slot/></div>', props: ['modelValue'] },
  'el-radio-button': { template: '<button><slot/></button>', props: ['label'] },
  'el-button': { template: `<button :disabled="disabled" @click="$emit('click')"><slot/></button>`, props: ['type', 'size', 'text', 'disabled'] },
  'el-input': { template: `<input :disabled="disabled" :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue', 'disabled', 'placeholder'] },
}
const SAMPLE = [{ id: 'sess_1', visitor_id: 'v-abcdef12', needs_human: true, human_takeover: false, human_agent_id: null, status: 'active', last_message: 'hi', last_active_at: null }]
const mountP = (instanceId = 'kf_1') => mount(KefuTakeoverPanel, { props: { instanceId }, global: { stubs } })

describe('KefuTakeoverPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ sessions: SAMPLE })
    mockMsgs.mockResolvedValue({ messages: [{ id: 'm1', role: 'assistant', content: [{ type: 'text', text: 'hello' }], meta: { author: 'human' }, createdAt: null }] })
    mockTakeover.mockResolvedValue({ humanTakeover: true })
    mockRelease.mockResolvedValue({ humanTakeover: false })
    mockReply.mockResolvedValue({ messageId: 'x' })
  })

  it('loads queue scoped to instance on mount', async () => {
    const w = mountP('kf_1'); await flushPromises()
    expect(mockList.mock.calls[0][0].instance).toBe('kf_1')
    expect((w.vm as any).sessions).toHaveLength(1)
  })

  it('filter=needs_human queries needs_human:true', async () => {
    const w = mountP(); await flushPromises()
    ;(w.vm as any).filter = 'needs_human'; await flushPromises()
    expect((mockList as any).mock.calls.at(-1)[0].needs_human).toBe(true)
  })

  it('selectSession loads its messages', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    expect(mockMsgs).toHaveBeenCalledWith('sess_1')
    expect((w.vm as any).messages).toHaveLength(1)
  })

  it('takeover calls api then refreshes queue+conv', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    mockList.mockClear(); mockMsgs.mockClear()
    await (w.vm as any).takeover()
    expect(mockTakeover).toHaveBeenCalledWith('sess_1')
    expect(mockList).toHaveBeenCalled()
    expect(mockMsgs).toHaveBeenCalled()
  })

  it('release calls api', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    await (w.vm as any).release()
    expect(mockRelease).toHaveBeenCalledWith('sess_1')
  })

  it('sendReply only when the selected session is taken over', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    ;(w.vm as any).replyDraft = 'hi'
    await (w.vm as any).sendReply()
    expect(mockReply).not.toHaveBeenCalled()             // not taken over
    mockList.mockResolvedValue({ sessions: [{ ...SAMPLE[0], needs_human: false, human_takeover: true }] })
    await (w.vm as any).loadQueue()
    ;(w.vm as any).replyDraft = 'hello agent'
    await (w.vm as any).sendReply()
    expect(mockReply).toHaveBeenCalledWith('sess_1', 'hello agent')
    expect((w.vm as any).replyDraft).toBe('')
  })

  it('polls queue every 5s and stops on unmount', async () => {
    vi.useFakeTimers()
    const w = mountP(); await flushPromises()
    mockList.mockClear()
    vi.advanceTimersByTime(5000); await flushPromises()
    expect(mockList).toHaveBeenCalled()
    w.unmount(); mockList.mockClear()
    vi.advanceTimersByTime(10000); await flushPromises()
    expect(mockList).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})
