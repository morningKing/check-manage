/**
 * aiChat store tests.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  sendMessage: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('useAiChatStore', () => {
  it('createSession populates activeSession and opens stream', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    const store = useAiChatStore()
    await store.startNewSession()

    expect(store.activeSessionId).toBe('sess_1')
    expect(api.createEventStream).toHaveBeenCalledWith('sess_1', expect.any(Object))
  })

  it('SSE message.part.delta appends to streaming assistant message', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => {
      handlers = h
      return { close: vi.fn() }
    })

    const store = useAiChatStore()
    await store.startNewSession()

    handlers.onEvent({ event: 'message.part.delta', data: { text: 'he' } })
    handlers.onEvent({ event: 'message.part.delta', data: { text: 'llo' } })

    const msgs = store.messages['sess_1']
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    expect((msgs[0].content[0] as any).text).toBe('hello')
  })

  it('message.finished flips streaming flag off', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => {
      handlers = h
      return { close: vi.fn() }
    })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.part.delta', data: { text: 'hi' } })
    handlers.onEvent({ event: 'message.finished', data: {} })

    expect(store.streaming['sess_1']).toBe(false)
  })

  it('sendUserMessage pushes a user msg then calls API', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    vi.mocked(api.sendMessage).mockResolvedValue({ messageId: 'msg_1' })

    const store = useAiChatStore()
    await store.startNewSession()
    await store.sendUserMessage('how are you')

    expect(store.messages['sess_1'][0].role).toBe('user')
    expect(api.sendMessage).toHaveBeenCalledWith('sess_1', 'how are you')
    expect(store.streaming['sess_1']).toBe(true)
  })
})
