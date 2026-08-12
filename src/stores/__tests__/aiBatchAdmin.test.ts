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

  it('轮询连续失败达到阈值后自动停止并置错误态（不再无限重试）', async () => {
    vi.mocked(listAdminBatches)
      .mockResolvedValueOnce({ items: [{ batchId: 'b1', status: 'running' } as any], total: 1 }) // 初次 fetchList
      .mockRejectedValue(new Error('网络连接失败')) // 之后每次轮询都失败（模拟权限撤销/token 过期/网络抖动）

    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()
    expect(store.pollError).toBe(false)

    // 前两次失败还没到阈值（3），轮询应该继续
    await vi.advanceTimersByTimeAsync(10_000)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.pollError).toBe(false)
    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(3) // 1 次初始 + 2 次轮询

    // 第 3 次失败达到阈值：应停止轮询并置显式错误态，而不是无限重试
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.pollError).toBe(true)
    const callsAtThreshold = vi.mocked(listAdminBatches).mock.calls.length
    expect(callsAtThreshold).toBe(4)

    // 继续推进远超轮询间隔的时间，不应再有新请求 —— 显式断言调用次数，
    // 不能只靠"没抛错"就当作通过（这个分支已经栽过断言没发生某事却写成假绿的坑）
    await vi.advanceTimersByTimeAsync(60_000)
    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(callsAtThreshold)
  })

  it('轮询请求携带 silent，不打断用户（不像手动 fetchList 那样传裸调用）', async () => {
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [{ batchId: 'b1', status: 'running' } as any],
      total: 1,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()

    await vi.advanceTimersByTimeAsync(10_000)

    const pollCall = vi.mocked(listAdminBatches).mock.calls[1]
    expect(pollCall[3]).toEqual({ silent: true })
  })

  it('轮询失败后若恢复成功，失败计数与错误态被清除', async () => {
    vi.mocked(listAdminBatches)
      .mockResolvedValueOnce({ items: [{ batchId: 'b1', status: 'running' } as any], total: 1 })
      .mockRejectedValueOnce(new Error('网络连接失败'))
      .mockRejectedValueOnce(new Error('网络连接失败'))
      .mockResolvedValueOnce({ items: [{ batchId: 'b1', status: 'running' } as any], total: 1 }) // 第三次轮询恢复

    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()

    await vi.advanceTimersByTimeAsync(10_000) // 失败 1
    await vi.advanceTimersByTimeAsync(10_000) // 失败 2（还没到阈值 3）
    await vi.advanceTimersByTimeAsync(10_000) // 恢复成功
    expect(store.pollError).toBe(false)

    // 恢复后再连续失败，应该要再攒够 3 次才会停止（说明计数确实被清零重新计）
    vi.mocked(listAdminBatches).mockRejectedValue(new Error('网络连接失败'))
    await vi.advanceTimersByTimeAsync(10_000)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.pollError).toBe(false)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.pollError).toBe(true)
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
