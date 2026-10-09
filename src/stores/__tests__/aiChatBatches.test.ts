import { describe, it, expect, beforeEach, vi, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAiChatBatchesStore } from '../aiChatBatches'
import * as api from '@/api/aiChatBatches'

// P1-A4：SSE 消费者 mock——store 只用 BatchEventStream([ids], handlers) +
// open()/close()，fake 类把 handlers 暴露出来供用例直接发帧。
const { FakeBatchEventStream } = vi.hoisted(() => {
  class FakeBatchEventStream {
    static last: FakeBatchEventStream | null = null
    static instances: FakeBatchEventStream[] = []
    batchIds: string[]
    handlers: Record<string, (...args: unknown[]) => void>
    opened = false
    closed = false
    constructor(batchIds: string[], handlers: Record<string, (...args: unknown[]) => void>) {
      this.batchIds = batchIds
      this.handlers = handlers
      FakeBatchEventStream.last = this
      FakeBatchEventStream.instances.push(this)
    }
    open() { this.opened = true }
    close() { this.closed = true }
    emitEvent(batchId: string, event: unknown) { this.handlers.onEvent?.(batchId, event) }
    emitDone(batchId: string) { this.handlers.onDone?.(batchId) }
  }
  return { FakeBatchEventStream }
})

vi.mock('@/api/batchEvents', () => ({ BatchEventStream: FakeBatchEventStream }))
vi.mock('@/api/aiChatBatches')

beforeAll(() => {
  vi.useFakeTimers()
})

beforeEach(() => {
  // Reset all timers between tests
  vi.clearAllTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  FakeBatchEventStream.last = null
  FakeBatchEventStream.instances = []
  // Reset document.hidden to false for each test
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
})

const mockBatch = {
  id: 'b1', user_id: 'u', name: 'B', prompt: 'p', template_id: null, agent: null, model: null,
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

// P0 §7.1：人工 continuation——终态批子会话的"发送"走批通道
  it('continueChild routes through the batch channel and restarts polling', async () => {
    vi.mocked(api.continueChild).mockResolvedValue({
      batch: { ...mockBatch, status: 'running' as const }, sessions: [],
    })
    const s = useAiChatBatchesStore()
    s.items = [mockBatch]
    s.activeBatch = mockBatch
    await s.continueChild('b1', 's-1', '补充一下结论')
    expect(api.continueChild).toHaveBeenCalledWith('b1', 's-1', '补充一下结论')
    expect(s.polling).toBe(true)  // 重回运行态 → 恢复轮询
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

  // ------------------------------------------------------------------
  // P1-A4：SSE 批任务事件消费者
  // ------------------------------------------------------------------
  it('selectBatch subscribes SSE for a running batch', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({ batch: mockBatch, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    expect(FakeBatchEventStream.last).not.toBeNull()
    expect(FakeBatchEventStream.last!.batchIds).toEqual(['b1'])
    expect(FakeBatchEventStream.last!.opened).toBe(true)
  })

  it('selectBatch skips SSE for a terminal batch', async () => {
    const terminal = { ...mockBatch, status: 'completed' as const, done: 3 }
    vi.mocked(api.getBatch).mockResolvedValue({ batch: terminal, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    expect(FakeBatchEventStream.last).toBeNull()
  })

  it('SSE batch_event triggers detail refresh into store', async () => {
    // 首次 selectBatch 拉到 done=1；事件帧到达后再拉到 done=2
    vi.mocked(api.getBatch)
      .mockResolvedValueOnce({ batch: mockBatch, sessions: [] })
      .mockResolvedValue({
        batch: { ...mockBatch, done: 2 },
        sessions: [{ id: 's-2' } as never],
      })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    expect(s.activeBatch?.done).toBe(1)
    FakeBatchEventStream.last!.emitEvent('b1', {
      batchId: 'b1', eventSeq: 1, type: 'child_done', data: {},
    })
    // onEvent → _applyRunningDetail（异步 refetch + applyDetail）
    await vi.advanceTimersByTimeAsync(0)
    expect(s.activeBatch?.done).toBe(2)
    // 轮询仍作为降级路径保留（未被 SSE 停掉）
    expect(s.polling).toBe(true)
  })

  it('SSE batch_done triggers final detail refresh', async () => {
    vi.mocked(api.getBatch)
      .mockResolvedValueOnce({ batch: mockBatch, sessions: [] })
      .mockResolvedValue({ batch: { ...mockBatch, status: 'completed' as const, done: 3 }, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    FakeBatchEventStream.last!.emitDone('b1')
    // 推进一个轮询周期：终态 detail 到达后，下一次 tick 观察到终态并停止轮询
    await vi.advanceTimersByTimeAsync(5000)
    expect(s.activeBatch?.status).toBe('completed')
    // 终态到达后轮询停止
    expect(s.polling).toBe(false)
  })

  it('clearSelection closes the SSE stream', async () => {
    vi.mocked(api.getBatch).mockResolvedValue({ batch: mockBatch, sessions: [] })
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    const stream = FakeBatchEventStream.last!
    s.clearSelection()
    expect(stream.closed).toBe(true)
    expect(s.polling).toBe(false)
  })

  it('switching batches replaces the SSE subscription', async () => {
    vi.mocked(api.getBatch).mockImplementation(async (id: string) => ({
      batch: { ...mockBatch, id }, sessions: [],
    }))
    const s = useAiChatBatchesStore()
    await s.selectBatch('b1')
    await s.selectBatch('b2')
    expect(FakeBatchEventStream.instances).toHaveLength(2)
    // 旧订阅已被关闭，只保留最新批次的订阅
    expect(FakeBatchEventStream.instances[0].closed).toBe(true)
    expect(FakeBatchEventStream.instances[1].closed).toBe(false)
    expect(FakeBatchEventStream.instances[1].batchIds).toEqual(['b2'])
  })

  // ------------------------------------------------------------------
  // P3-M11：列表级 SSE——当页非终态批次的事件触发防抖 fetchList
  // ------------------------------------------------------------------
  it('list SSE event triggers debounced fetchList', async () => {
    vi.mocked(api.listBatches).mockResolvedValue({ items: [mockBatch], total: 1 })
    const s = useAiChatBatchesStore()
    s.startListPolling()
    // 首个轮询 tick（10s）：fetchList 落地 running 批次 → subscribeListEvents
    await vi.advanceTimersByTimeAsync(10000)
    expect(vi.mocked(api.listBatches)).toHaveBeenCalledTimes(1)
    const listStream = FakeBatchEventStream.last!
    expect(listStream).not.toBeNull()
    expect(listStream.batchIds).toEqual(['b1'])
    expect(listStream.opened).toBe(true)
    // 3s 防抖窗口内多次事件合并为一次 fetchList
    listStream.emitEvent('b1', { batchId: 'b1', eventSeq: 1, type: 'child_done', data: {} })
    listStream.emitEvent('b1', { batchId: 'b1', eventSeq: 2, type: 'child_done', data: {} })
    expect(vi.mocked(api.listBatches)).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(vi.mocked(api.listBatches)).toHaveBeenCalledTimes(2)
    s.stopListPolling()
  })

  it('list SSE skips subscription when the page has only terminal batches', async () => {
    const doneBatch = { ...mockBatch, status: 'completed' as const, done: 3 }
    vi.mocked(api.listBatches).mockResolvedValue({ items: [doneBatch], total: 1 })
    const s = useAiChatBatchesStore()
    s.startListPolling()
    await vi.advanceTimersByTimeAsync(10000)
    expect(vi.mocked(api.listBatches)).toHaveBeenCalledTimes(1)
    // 全部终态 → 不建立列表级 SSE 订阅，仅靠轮询降级
    expect(FakeBatchEventStream.last).toBeNull()
    s.stopListPolling()
  })

  // ------------------------------------------------------------------
  // 2026-10-09：列表轮询复用 SSE 连接——ids 未变不重建。原先每 10s tick
  // 都 close+open，后端每次重连占新线程 + 归属查询，是生产请求风暴源之一。
  // ------------------------------------------------------------------
  it('list polling reuses the SSE connection while active ids are unchanged', async () => {
    vi.mocked(api.listBatches).mockResolvedValue({
      items: [mockBatch, { ...mockBatch, id: 'b2' }], total: 2,
    })
    const s = useAiChatBatchesStore()
    s.startListPolling()
    await vi.advanceTimersByTimeAsync(10000)   // tick 1：建立 ['b1','b2'] 订阅
    expect(FakeBatchEventStream.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(10000)   // tick 2：ids 未变 → 复用
    await vi.advanceTimersByTimeAsync(10000)   // tick 3：仍然复用
    expect(FakeBatchEventStream.instances).toHaveLength(1)
    expect(FakeBatchEventStream.instances[0].opened).toBe(true)
    expect(FakeBatchEventStream.instances[0].closed).toBe(false)
    s.stopListPolling()
  })

  it('list polling rebuilds the SSE connection when active ids change', async () => {
    vi.mocked(api.listBatches)
      .mockResolvedValueOnce({ items: [mockBatch], total: 1 })
      .mockResolvedValue({ items: [mockBatch, { ...mockBatch, id: 'b3' }], total: 2 })
    const s = useAiChatBatchesStore()
    s.startListPolling()
    await vi.advanceTimersByTimeAsync(10000)
    expect(FakeBatchEventStream.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(10000)   // 新增非终态 b3 → 重建
    expect(FakeBatchEventStream.instances).toHaveLength(2)
    expect(FakeBatchEventStream.instances[0].closed).toBe(true)
    expect(FakeBatchEventStream.instances[1].batchIds).toEqual(['b1', 'b3'])
    s.stopListPolling()
  })

  it('list polling closes the SSE connection when all batches turn terminal', async () => {
    vi.mocked(api.listBatches)
      .mockResolvedValueOnce({ items: [mockBatch], total: 1 })
      .mockResolvedValue({
        items: [{ ...mockBatch, status: 'completed' as const, done: 3 }], total: 1,
      })
    const s = useAiChatBatchesStore()
    s.startListPolling()
    await vi.advanceTimersByTimeAsync(10000)
    expect(FakeBatchEventStream.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(10000)   // 全部终态 → 关流不重建
    expect(FakeBatchEventStream.instances).toHaveLength(1)
    expect(FakeBatchEventStream.instances[0].closed).toBe(true)
    s.stopListPolling()
  })
})
