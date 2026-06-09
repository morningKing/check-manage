import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  sendMessage: vi.fn().mockResolvedValue({ messageId: 'm1', model: null, agent: null }),
  createSession: vi.fn(),
  listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn().mockResolvedValue({ messages: [] }),
  uploadFile: vi.fn(),
  uploadSkill: vi.fn(),
  listFiles: vi.fn().mockResolvedValue({ files: [] }),
  getChanges: vi.fn().mockResolvedValue({ changes: [], ok: true }),
  getMcpServices: vi.fn().mockResolvedValue({ servers: [] }),
  getCommands: vi.fn().mockResolvedValue({ commands: [], skills: [] }),
  postCommand: vi.fn(),
  abortSession: vi.fn(),
  deleteFromMessage: vi.fn(),
  createEventStream: vi.fn().mockReturnValue({ close: vi.fn() }),
  runScript: vi.fn(),
  listModels: vi.fn(),
  listAgents: vi.fn(),
  getFileDiff: vi.fn(),
  downloadFileUrl: vi.fn(),
}))

import { sendMessage } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

describe('aiChat store agent', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('setSessionAgent persists and hydrateSessionAgent restores', () => {
    const s = useAiChatStore()
    s.setSessionAgent('sid1', 'plan')
    expect(s.agentBySession['sid1']).toBe('plan')
    expect(localStorage.getItem('check-manage:ai-chat:agent:sid1')).toBe('plan')

    const s2 = useAiChatStore()
    delete s2.agentBySession['sid1']
    s2.hydrateSessionAgent('sid1')
    expect(s2.agentBySession['sid1']).toBe('plan')
  })

  it('sendUserMessage forwards the session agent to the api', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 'sid1'
    s.messages['sid1'] = []
    s.attachments['sid1'] = []
    s.streaming['sid1'] = false
    s.reasoning['sid1'] = ''
    s.thinking['sid1'] = false
    s.agentBySession['sid1'] = 'plan'
    await s.sendUserMessage('hello')
    const call = (sendMessage as any).mock.calls.at(-1)
    // sendMessage(sid, content, paths, model, agent)
    expect(call[0]).toBe('sid1')
    expect(call[4]).toBe('plan')
  })
})
