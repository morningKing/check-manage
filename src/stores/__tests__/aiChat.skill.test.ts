import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  uploadSkill: vi.fn(),
  getCommands: vi.fn(), postCommand: vi.fn(),
  getMcpServices: vi.fn(),
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(), deleteSession: vi.fn(),
  getMessages: vi.fn(), sendMessage: vi.fn(), uploadFile: vi.fn(), listFiles: vi.fn(),
  getChanges: vi.fn(), createEventStream: vi.fn(() => ({ close() {} })),
}))
import { uploadSkill, getCommands } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('uploadSkill store action', () => {
  it('throws if no active session', async () => {
    const store = useAiChatStore()
    await expect(store.uploadSkill(new File(['x'], 'a.zip'))).rejects.toThrow()
  })

  it('uploads and then reloads palette items', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(uploadSkill as any).mockResolvedValue({ name: 'hello', path: '.opencode/skills/hello' })
    ;(getCommands as any).mockResolvedValue({ commands: [], skills: [{ name: 'hello', description: '' }] })
    const res = await store.uploadSkill(new File(['z'], 'h.zip'))
    expect(res.name).toBe('hello')
    expect(uploadSkill).toHaveBeenCalledWith('s1', expect.any(File))
    expect(getCommands).toHaveBeenCalledWith('s1')
    expect(store.paletteItems['s1'].skills[0].name).toBe('hello')
    expect(store.uploading).toBe(false)
  })

  it('clears uploading on failure and rethrows', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(uploadSkill as any).mockRejectedValue(new Error('boom'))
    await expect(store.uploadSkill(new File(['z'], 'h.zip'))).rejects.toThrow('boom')
    expect(store.uploading).toBe(false)
  })
})
