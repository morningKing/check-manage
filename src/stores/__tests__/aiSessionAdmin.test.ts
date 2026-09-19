import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAiSessionAdminStore } from '../aiSessionAdmin'
import * as api from '@/api/aiSessionAdmin'

vi.mock('@/api/aiSessionAdmin')

const row = (over: Record<string, unknown> = {}) =>
  ({ id: 'sess_1', title: 't', status: 'active', username: 'admin', ...over }) as any

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('aiSessionAdmin store', () => {
  it('fetchList applies items/total and clears poll error', async () => {
    vi.mocked(api.listSessions).mockResolvedValue({ items: [row()], total: 7 } as any)
    const s = useAiSessionAdminStore()
    s.pollError = '旧错误'
    await s.fetchList()
    expect(s.items).toHaveLength(1)
    expect(s.total).toBe(7)
    expect(s.pollError).toBeNull()
    expect(api.listSessions).toHaveBeenCalledWith({}, 1, 20)
  })

  it('fetchList failure records error and counts consecutive failures', async () => {
    vi.mocked(api.listSessions).mockRejectedValue(new Error('网络错误'))
    const s = useAiSessionAdminStore()
    await s.fetchList()
    await s.fetchList()
    expect(s.pollError).toBe('网络错误')
  })

  it('hasRunning detects pending/running sessions only', async () => {
    const s = useAiSessionAdminStore()
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [row({ status: 'archived' }), row({ id: 'x', status: 'completed' })],
      total: 2,
    } as any)
    await s.fetchList()
    expect(s.hasRunning).toBe(false)
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [row({ status: 'running' })], total: 1,
    } as any)
    await s.fetchList()
    expect(s.hasRunning).toBe(true)
  })

  it('openDetail resets tab/messages/files, 404 keeps drawer with null detail', async () => {
    vi.mocked(api.getSessionDetail).mockRejectedValue(new Error('404'))
    const s = useAiSessionAdminStore()
    s.activeTab = 'files'
    await s.openDetail('nope')
    expect(s.detailVisible).toBe(true)
    expect(s.activeTab).toBe('info')
    expect(s.detail).toBeNull()
    expect(s.detailLoading).toBe(false)
  })

  it('fetchMessages/fetchFiles guard on missing detail', async () => {
    const s = useAiSessionAdminStore()
    await s.fetchMessages()
    await s.fetchFiles()
    expect(api.getSessionMessages).not.toHaveBeenCalled()
    expect(api.getSessionFiles).not.toHaveBeenCalled()
  })

  it('setFilter normalizes empty to undefined and resets page', async () => {
    vi.mocked(api.listSessions).mockResolvedValue({ items: [], total: 0 } as any)
    const s = useAiSessionAdminStore()
    s.page = 5
    s.setFilter('username', 'admin')
    expect(s.filters.username).toBe('admin')
    expect(s.page).toBe(1)
    s.setFilter('username', '')
    expect(s.filters.username).toBeUndefined()
  })

  it('doArchive archives then refreshes list', async () => {
    vi.mocked(api.archiveSession).mockResolvedValue({} as any)
    vi.mocked(api.listSessions).mockResolvedValue({ items: [], total: 0 } as any)
    const s = useAiSessionAdminStore()
    await s.doArchive('sess_1')
    expect(api.archiveSession).toHaveBeenCalledWith('sess_1')
    expect(api.listSessions).toHaveBeenCalled()
  })

  it('polling stops itself after 3 consecutive failures', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(api.listSessions).mockRejectedValue(new Error('down'))
      const s = useAiSessionAdminStore()
      s.startPolling()
      for (let i = 0; i < 4; i++) await vi.advanceTimersByTimeAsync(8000)
      const calls = vi.mocked(api.listSessions).mock.calls.length
      await vi.advanceTimersByTimeAsync(8000)
      expect(vi.mocked(api.listSessions).mock.calls.length).toBe(calls) // 已停
      s.stopPolling()
    } finally {
      vi.useRealTimers()
    }
  })
})
