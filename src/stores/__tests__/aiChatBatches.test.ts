import { describe, it, expect, beforeEach, vi, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAiChatBatchesStore } from '../aiChatBatches'
import * as api from '@/api/aiChatBatches'

vi.mock('@/api/aiChatBatches')

beforeAll(() => {
  vi.useFakeTimers()
})

beforeEach(() => {
  // Reset all timers between tests
  vi.clearAllTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  // Reset document.hidden to false for each test
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
})

const mockBatch = {
  id: 'b1', user_id: 'u', name: 'B', prompt: 'p', template_id: null,
  status: 'running' as const, total: 3, done: 1, failed: 0,
  created_at: '', completed_at: null,
}

describe('aiChatBatches store', () => {
  it('fetchList populates items', async () => {
    vi.mocked(api.listBatches).mockResolvedValue({ items: [mockBatch], total: 1 })
    const s = useAiChatBatchesStore()
    await s.fetchList()
    expect(s.items).toEqual([mockBatch])
  })

  it('selectBatch fetches detail and starts polling', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({
      batch: mockBatch, sessions: [],
    })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    expect(api.getBatch).toHaveBeenCalledTimes(1)
    expect(s.activeBatch?.id).toBe('b1')
    expect(s.polling).toBe(true)
    // After 5 seconds another fetch
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getBatch).toHaveBeenCalledTimes(2)
  })

  it('stops polling when batch reaches terminal state', async () => {
    const terminal = { ...mockBatch, status: 'completed' as const, done: 3 }
    vi.mocked(api.getBatch).mockResolvedValue({ batch: terminal, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    await vi.advanceTimersByTimeAsync(5000)
    expect(s.polling).toBe(false)
  })

  it('retryFailed optimistically clears failed count and refetches', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({
      batch: { ...mockBatch, failed: 2, status: 'partial' as const }, sessions: [],
    })
    vi.mocked(api.retryFailedSessions).mockResolvedValue({ retried: 2 })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    await s.retryFailed()
    // immediate optimistic clear
    expect(s.activeBatch?.failed).toBe(0)
    expect(api.getBatch).toHaveBeenCalled()
  })

  it('clearSelection stops polling', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({ batch: mockBatch, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    s.clearSelection()
    expect(s.polling).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    // No further calls beyond the initial selectBatch fetch
    expect(api.getBatch).toHaveBeenCalledTimes(1)
  })

  it('pauses polling on document.hidden, resumes on visible', async () => {
    const getBatchMock = vi.mocked(api.getBatch)
    getBatchMock.mockResolvedValue({ batch: mockBatch, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    // simulate tab hide
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(15000)
    // No further fetches while hidden
    expect(getBatchMock).toHaveBeenCalledTimes(1)
    // simulate tab visible
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.runOnlyPendingTimersAsync()
    expect(getBatchMock.mock.calls.length).toBeGreaterThan(1)
  })
})
