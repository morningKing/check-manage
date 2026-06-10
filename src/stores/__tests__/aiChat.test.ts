/**
 * aiChat store tests.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  sendMessage: vi.fn(),
  uploadFile: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ files: [] })),
  getChanges: vi.fn(),
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
    vi.mocked(api.sendMessage).mockResolvedValue({ messageId: 'msg_1', model: null })

    const store = useAiChatStore()
    await store.startNewSession()
    await store.sendUserMessage('how are you')

    expect(store.messages['sess_1'][0].role).toBe('user')
    // args: sessionId, content, attachments, model, agent, agentMentions
    expect(api.sendMessage).toHaveBeenCalledWith('sess_1', 'how are you', [], '', '', [])
    expect(store.streaming['sess_1']).toBe(true)
  })

  it('captures tool-use parts (MCP/built-in tool calls) on the assistant message', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: {
      id: 'tp', type: 'tool', messageID: 'm1', tool: 'list_collections',
      state: { status: 'completed', title: 'list', input: { x: 1 }, output: 'ok' },
    } } })

    const parts = store.messages['sess_1'][0].content
    const tool = parts.find((p: any) => p.type === 'tool_use') as any
    expect(tool).toBeTruthy()
    expect(tool.name).toBe('list_collections')
    expect(tool.status).toBe('completed')
    expect(tool.result).toBe('ok')
  })

  it('accumulates reasoning text and toggles thinking', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: { id: 'r1', type: 'reasoning', messageID: 'm1', text: '思考中…' } } })
    expect(store.reasoning['sess_1']).toBe('思考中…')
    expect(store.thinking['sess_1']).toBe(true)

    handlers.onEvent({ event: 'session.idle', data: { sessionID: 'oc' } })
    expect(store.thinking['sess_1']).toBe(false)
  })

  it('loadChanges populates changes for the session', async () => {
    const store = useAiChatStore()
    ;(api.getChanges as any).mockResolvedValue({
      changes: [{ path: 'repo/new.txt', status: 'added' }], truncated: false,
    })
    store.activeSessionId = 's1'
    await store.loadChanges('s1')
    expect(store.changes['s1']).toEqual([{ path: 'repo/new.txt', status: 'added' }])
    expect(store.activeChanges).toEqual([{ path: 'repo/new.txt', status: 'added' }])
  })

  it('loads outputs/ files after a turn finishes (session.idle)', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })
    vi.mocked(api.listFiles).mockResolvedValue({ files: [
      { name: 'out.py', path: 'outputs/out.py', dir: 'outputs', size: 12 },
      { name: 'in.txt', path: 'uploads/in.txt', dir: 'uploads', size: 5 },
    ] })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'session.idle', data: { sessionID: 'oc' } })
    await Promise.resolve(); await Promise.resolve()  // let loadFiles promise settle

    // only outputs/ files are surfaced as downloadable artifacts
    expect(store.outputs['sess_1'].map(f => f.name)).toEqual(['out.py'])
  })
})
