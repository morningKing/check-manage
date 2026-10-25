/**
 * 会话大纲 store 侧（spec 2026-10-25-session-outline §5）：
 * loadTurns 状态落位与失败标记；ensureLoadedUntil 跨窗口加载语义。
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
  getTurns: vi.fn(),
  sendMessage: vi.fn(),
  uploadFile: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ files: [] })),
  getChanges: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'
import type { AiMessage } from '@/api/aiChat'

const msg = (id: string, seq: number, role: 'user' | 'assistant' = 'assistant'): AiMessage =>
  ({ id, role, content: [{ type: 'text', text: id }], seq })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('loadTurns', () => {
  it('stores server turns and clears error flag', async () => {
    vi.mocked(api.getTurns).mockResolvedValue({
      turns: [{ id: 'm1', seq: 3, createdAt: '2026-10-25T09:00:00', preview: '第一问' }],
    })
    const store = useAiChatStore()
    store.turnsError['s1'] = true
    await store.loadTurns('s1')
    expect(store.turnsBySession['s1']).toHaveLength(1)
    expect(store.turnsBySession['s1'][0].preview).toBe('第一问')
    expect(store.turnsError['s1']).toBe(false)
  })

  it('marks error and keeps stale turns on failure', async () => {
    vi.mocked(api.getTurns).mockRejectedValue(new Error('boom'))
    const store = useAiChatStore()
    store.turnsBySession['s1'] = [{ id: 'm1', seq: 3, preview: '旧' }]
    await store.loadTurns('s1')
    expect(store.turnsError['s1']).toBe(true)
    expect(store.turnsBySession['s1'][0].preview).toBe('旧')
  })
})

describe('ensureLoadedUntil', () => {
  it('returns true without fetching when target already in window', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('a', 1), msg('b', 2)]
    store.oldestLoadedSeq['s1'] = 1
    store.hasMoreMessages['s1'] = true
    await expect(store.ensureLoadedUntil('s1', 2)).resolves.toBe(true)
    expect(api.getMessages).not.toHaveBeenCalled()
  })

  it('pages backwards with 1000-limit windows, prepends, and flips hasMore', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    vi.mocked(api.getMessages).mockResolvedValueOnce({ messages: [msg('m1', 1)], hasMore: false })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(true)
    expect(api.getMessages).toHaveBeenCalledWith('s1', { before: '9', limit: 1000 })
    expect(store.messages['s1'].map(m => m.id)).toEqual(['m1', 'm9'])
    expect(store.hasMoreMessages['s1']).toBe(false)
  })

  it('returns false when history exhausted without the target', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    vi.mocked(api.getMessages).mockResolvedValueOnce({ messages: [msg('m5', 5)], hasMore: false })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(false)
  })

  it('stops after one page when the cursor does not advance (防死循环)', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    // 异常服务端：同一窗口原样返回（游标不动）
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [msg('m9', 9)], hasMore: true })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(false)
    expect(api.getMessages).toHaveBeenCalledTimes(1)
  })
})
