import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getCommands: vi.fn(), postCommand: vi.fn(),
  getMcpServices: vi.fn(),
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(), deleteSession: vi.fn(),
  getMessages: vi.fn(), sendMessage: vi.fn(), uploadFile: vi.fn(), listFiles: vi.fn(),
  getChanges: vi.fn(), createEventStream: vi.fn(() => ({ close() {} })),
}))
import { getCommands, postCommand } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('palette store', () => {
  it('loadPaletteItems caches commands and skills', async () => {
    const store = useAiChatStore()
    ;(getCommands as any).mockResolvedValue({ commands: [{ name: 'init', description: 'a' }], skills: [{ name: 'clawhub', description: 'b' }] })
    await store.loadPaletteItems('s1')
    expect(store.isOpencodeCommand('s1', 'init')).toBe(true)
    expect(store.isOpencodeCommand('s1', 'nope')).toBe(false)
  })

  it('runCommand pushes a user line and posts', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'; store.messages['s1'] = []
    ;(postCommand as any).mockResolvedValue({ messageId: 'm1' })
    await store.runCommand('s1', 'init', 'go')
    expect(postCommand).toHaveBeenCalledWith('s1', 'init', 'go')
    const last = store.messages['s1'][store.messages['s1'].length - 1]
    expect(last.role).toBe('user')
    expect((last.content[0] as any).text).toContain('/init go')
    expect(store.streaming['s1']).toBe(true)
  })
})
