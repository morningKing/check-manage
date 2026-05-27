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

  it('message.part.updated replaces the part text snapshot (assistant only)', async () => {
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

    // assistant message announced, then its text part streams as full snapshots
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: { id: 'p1', type: 'text', messageID: 'm1', text: 'he' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: { id: 'p1', type: 'text', messageID: 'm1', text: 'hello' } } })

    const msgs = store.messages['sess_1']
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    // snapshot semantics: replaced, not appended → "hello", not "hehello"
    expect(msgs[0].content).toHaveLength(1)
    expect((msgs[0].content[0] as any).text).toBe('hello')
  })

  it('ignores parts that do not belong to an assistant message (e.g. user echo)', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })

    const store = useAiChatStore()
    await store.startNewSession()
    // user message part (messageID never registered as assistant) must not render
    handlers.onEvent({ event: 'message.part.updated', data: { part: { id: 'pu', type: 'text', messageID: 'muser', text: 'echo' } } })
    expect(store.messages['sess_1']).toHaveLength(0)
  })

  it('session.idle flips streaming flag off', async () => {
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
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: { id: 'p1', type: 'text', messageID: 'm1', text: 'hi' } } })
    handlers.onEvent({ event: 'session.idle', data: { sessionID: 'oc' } })

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
