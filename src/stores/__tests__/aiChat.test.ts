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
  clearSession: vi.fn(),
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

  it('send during an in-flight startNewSession lands in the NEW session', async () => {
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    vi.mocked(api.sendMessage).mockResolvedValue({ messageId: 'msg_1', model: null })
    let release: (v: any) => void = () => {}
    vi.mocked(api.createSession).mockReturnValueOnce(new Promise<any>(res => { release = res }))

    const store = useAiChatStore()
    store.activeSessionId = 'old_session'
    store.messages['old_session'] = []

    const creating = store.startNewSession()
    const sending = store.sendUserMessage('hi')
    // send is parked until creation resolves, then targets the new session
    expect(api.sendMessage).not.toHaveBeenCalled()
    release({ id: 'sess_new', title: '新会话', workspacePath: '/ws' })
    await creating
    await sending
    expect(api.sendMessage).toHaveBeenCalledWith('sess_new', 'hi', [], '', '', [])
    expect(store.messages['sess_new'][0].role).toBe('user')
    expect(store.messages['old_session']).toEqual([])
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

  it('renders task tool delegation as a subtask_use bubble (natural-language path)', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    handlers.onEvent({ event: 'message.part.updated', data: { part: {
      id: 'tp', type: 'tool', messageID: 'm1', tool: 'task',
      state: { status: 'running',
               input: { subagent_type: 'general', description: 'count lines' },
               metadata: { sessionId: 'ses_child' } },
    } } })

    const parts = store.messages['sess_1'][0].content
    expect(parts).toHaveLength(1)
    expect(parts[0]).toMatchObject({ type: 'subtask_use', subtaskId: 'ses_child',
                                     agent: 'general', description: 'count lines',
                                     status: 'running' })
  })

  it('dedupes subtask part + task tool part into one bubble with the child id', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.updated', data: { info: { id: 'm1', role: 'assistant' } } })
    // subtask part 先到——sessionID 是父会话，暂时只能用
    handlers.onEvent({ event: 'message.part.updated', data: { part: {
      id: 'sp', type: 'subtask', messageID: 'm1', sessionID: 'sess_parent_oc',
      agent: 'build', description: 'review changes',
    } } })
    // tool:'task' 后到——带上真实子会话 id，回补到已有气泡上，不新增气泡
    handlers.onEvent({ event: 'message.part.updated', data: { part: {
      id: 'tp', type: 'tool', messageID: 'm1', tool: 'task',
      state: { status: 'running', metadata: { sessionId: 'ses_child_real' } },
    } } })

    const parts = store.messages['sess_1'][0].content
    const stubs = parts.filter((p: any) => p.type === 'subtask_use') as any[]
    expect(stubs).toHaveLength(1)
    expect(stubs[0].subtaskId).toBe('ses_child_real')
    expect(stubs[0].agent).toBe('build')
    expect(stubs[0].description).toBe('review changes')
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

  it('surfaces outputs/ and workspace-root generated files (not uploads) after session.idle', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { handlers = h; return { close: vi.fn() } })
    vi.mocked(api.listFiles).mockResolvedValue({ files: [
      { name: 'out.py', path: 'outputs/out.py', dir: 'outputs', size: 12 },
      { name: 'report.md', path: 'report.md', dir: 'workspace', size: 6 },
      { name: 'in.txt', path: 'uploads/in.txt', dir: 'uploads', size: 5 },
    ] })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'session.idle', data: { sessionID: 'oc' } })
    await Promise.resolve(); await Promise.resolve()  // let loadFiles promise settle

    // generated artifacts (outputs/ + workspace root) surface; uploaded input does not
    expect(store.outputs['sess_1'].map(f => f.name)).toEqual(['out.py', 'report.md'])
  })

  it('clearSession wipes local history/files, keeps the session active, and reopens the stream', async () => {
    vi.mocked(api.createSession).mockResolvedValue({ id: 'sess_1', title: '新会话', workspacePath: '/ws' })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => { void h; return { close: vi.fn() } })
    ;(api.clearSession as any).mockResolvedValue({ ok: true, status: 'active' })

    const store = useAiChatStore()
    await store.startNewSession()
    // seed local per-session state that clear must reset
    store.messages['sess_1'] = [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'hi' }] }]
    store.outputs['sess_1'] = [{ name: 'a.txt', path: 'a.txt', dir: 'workspace', size: 1 }]
    store.changes['sess_1'] = [{ path: 'a.txt', status: 'added' }]
    const streamCallsBefore = vi.mocked(api.createEventStream).mock.calls.length

    await store.clearSession('sess_1')

    expect(api.clearSession).toHaveBeenCalledWith('sess_1')
    expect(store.messages['sess_1']).toEqual([])
    expect(store.outputs['sess_1']).toEqual([])
    expect(store.changes['sess_1']).toEqual([])
    expect(store.sessions.find(s => s.id === 'sess_1')?.status).toBe('active')
    // active session reconnects to the fresh OpenCode context
    expect(vi.mocked(api.createEventStream).mock.calls.length).toBe(streamCallsBefore + 1)
  })
})
