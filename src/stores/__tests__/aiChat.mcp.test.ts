import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getMcpServices: vi.fn(),
  // other named exports used by the store module must exist as no-ops
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(),
  deleteSession: vi.fn(), getMessages: vi.fn(), sendMessage: vi.fn(),
  uploadFile: vi.fn(), listFiles: vi.fn(), getChanges: vi.fn(),
  createEventStream: vi.fn(() => ({ close() {} })),
}))

import { getMcpServices } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('showMcpServices', () => {
  it('pushes an mcp_services part with the fetched servers', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.messages['s1'] = []
    ;(getMcpServices as any).mockResolvedValue({
      servers: [{ name: 'check-manage', status: 'connected', tools: [{ name: 'list_collections', description: 'x' }] }],
    })
    await store.showMcpServices()
    const last = store.messages['s1'].at(-1)!
    expect(last.role).toBe('assistant')
    expect(last.content[0]).toMatchObject({ type: 'mcp_services' })
    expect((last.content[0] as any).servers[0].name).toBe('check-manage')
  })

  it('pushes empty servers when the API errors or reports error', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.messages['s1'] = []
    ;(getMcpServices as any).mockResolvedValue({ servers: [], error: 'opencode unavailable' })
    await store.showMcpServices()
    expect((store.messages['s1'].at(-1)!.content[0] as any).servers).toEqual([])
  })
})
