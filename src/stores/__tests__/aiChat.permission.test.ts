import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getPendingPermission: vi.fn(),
  replyPermission: vi.fn(),
  // other named exports used by the store module must exist as no-ops
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(),
  deleteSession: vi.fn(), getMessages: vi.fn(), sendMessage: vi.fn(),
  uploadFile: vi.fn(), listFiles: vi.fn(), getChanges: vi.fn(),
  getPendingQuestion: vi.fn(), replyQuestion: vi.fn(), rejectQuestion: vi.fn(),
  createEventStream: vi.fn(() => ({ close() {} })),
}))

import { replyPermission } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const PERMISSION = {
  id: 'per_1',
  sessionID: 'oc_1',
  permission: 'external_directory',
  patterns: ['C:/Users/admin/.check-manage/oc-lab/*'],
  metadata: { filepath: 'C:/Users/admin/.check-manage/oc-lab/secret.txt', parentDir: 'C:/Users/admin/.check-manage/oc-lab' },
  always: ['C:/Users/admin/.check-manage/oc-lab/*'],
}

describe('pendingPermission via SSE', () => {
  it('permission.asked stores the request', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(store as any)._handleEvent('s1', 'permission.asked', PERMISSION)
    expect(store.pendingPermission['s1']).toEqual(PERMISSION)
    expect(store.activePendingPermission).toEqual(PERMISSION)
  })

  it('permission.replied for the matching id clears it', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermission['s1'] = PERMISSION
    ;(store as any)._handleEvent('s1', 'permission.replied', { sessionID: 'oc_1', requestID: 'per_1' })
    expect(store.pendingPermission['s1']).toBeNull()
  })

  it('permission.replied for a DIFFERENT id is ignored (stale echo / multi-tab)', () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermission['s1'] = PERMISSION
    ;(store as any)._handleEvent('s1', 'permission.replied', { sessionID: 'oc_1', requestID: 'per_other' })
    expect(store.pendingPermission['s1']).toEqual(PERMISSION)
  })

  it('answerPendingPermission calls the API with the chosen response and clears locally', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermission['s1'] = PERMISSION
    ;(replyPermission as any).mockResolvedValue({ ok: true })
    await store.answerPendingPermission('s1', 'per_1', 'once')
    expect(replyPermission).toHaveBeenCalledWith('s1', 'per_1', 'once')
    expect(store.pendingPermission['s1']).toBeNull()
  })

  it('answerPendingPermission with a mismatched id is a no-op (stale card)', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.pendingPermission['s1'] = PERMISSION
    await store.answerPendingPermission('s1', 'per_other', 'always')
    expect(replyPermission).not.toHaveBeenCalled()
    expect(store.pendingPermission['s1']).toEqual(PERMISSION)
  })
})
