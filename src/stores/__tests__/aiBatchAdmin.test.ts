import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiBatchAdmin', () => ({
  listAdminBatches: vi.fn(() => Promise.resolve({ items: [], total: 0 })),
  getAdminBatch: vi.fn(),
  getAdminChildMessages: vi.fn(),
  retryAdminBatch: vi.fn(),
  reexecuteAdminChild: vi.fn(),
}))

import { listAdminBatches } from '@/api/aiBatchAdmin'
import { useAiBatchAdminStore } from '@/stores/aiBatchAdmin'

describe('aiBatchAdmin store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('把筛选条件原样传给 API', async () => {
    const store = useAiBatchAdminStore()
    store.filters.status = 'failed'
    store.filters.source = 'api'
    store.filters.owner = 'alice'
    store.filters.keyword = '报告'
    await store.fetchList()

    expect(listAdminBatches).toHaveBeenCalledWith(
      { status: 'failed', source: 'api', owner: 'alice', keyword: '报告' },
      1, 20,
    )
  })

  it('筛选项为空或仅空白时不传给 API（而不是传空字符串）', async () => {
    const store = useAiBatchAdminStore()
    store.filters.status = ''
    store.filters.owner = '   '
    store.filters.source = ''
    store.filters.keyword = ''
    await store.fetchList()

    const callArgs = vi.mocked(listAdminBatches).mock.calls[0][0]
    expect(callArgs).not.toHaveProperty('status')
    expect(callArgs).not.toHaveProperty('owner')
    expect(callArgs).not.toHaveProperty('source')
    expect(callArgs).not.toHaveProperty('keyword')
  })

  it('轮询期间列表转为全终态后不再发起后续请求（覆盖 timer 回调内的检查）', async () => {
    vi.mocked(listAdminBatches)
      .mockResolvedValueOnce({ items: [{ batchId: 'b1', status: 'running' } as any], total: 1 }) // 初次 fetchList
      .mockResolvedValueOnce({ items: [{ batchId: 'b1', status: 'completed' } as any], total: 1 }) // 轮询第一次 tick 时拉到的最新状态

    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()

    await vi.advanceTimersByTimeAsync(10_000) // 第一次 tick：此时仍是 running，会拉取，拉到的结果已转终态
    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(2)

    await vi.advanceTimersByTimeAsync(60_000) // 之后应停止轮询，不再发起请求
    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(2)
  })

  it('全部批任务都是终态时不产生多余请求（不启动轮询）', async () => {
    // 这条比"会轮询"更容易写成假绿，所以显式推进定时器后断言调用次数没涨。
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [
        { batchId: 'b1', status: 'completed' } as any,
        { batchId: 'b2', status: 'failed' } as any,
      ],
      total: 2,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    const callsAfterFetch = vi.mocked(listAdminBatches).mock.calls.length

    store.startPolling()
    await vi.advanceTimersByTimeAsync(60_000)   // 远超 10s 间隔

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(callsAfterFetch)
  })

  it('存在非终态批任务时按 10 秒间隔轮询', async () => {
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [{ batchId: 'b1', status: 'running' } as any],
      total: 1,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    const before = vi.mocked(listAdminBatches).mock.calls.length

    store.startPolling()
    await vi.advanceTimersByTimeAsync(10_000)

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(before + 1)
    store.stopPolling()
  })

  it('stopPolling 之后不再发请求', async () => {
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [{ batchId: 'b1', status: 'running' } as any],
      total: 1,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()
    store.stopPolling()
    const before = vi.mocked(listAdminBatches).mock.calls.length

    await vi.advanceTimersByTimeAsync(60_000)

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(before)
  })
})
