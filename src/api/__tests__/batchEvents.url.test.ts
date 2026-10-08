// 回归：batchEvents 的两个 URL 都必须带 /api 前缀。
// 生产 proxy.py 只把 /api/* 转发给后端，其余路径一律走 SPA fallback 回
// index.html（text/html）——裸路径的 EventSource 因此报
// "response has a MIME type text/html this is not text/event-stream"，
// 裸路径的 axios 轮询则静默拿到 HTML。dev 下 Vite 同样只代理 /api，
// 两者在 dev 里也是坏的（404），只是不报 MIME 错、被轮询降级掩盖。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const { fakeGet } = vi.hoisted(() => ({ fakeGet: vi.fn() }))
vi.mock('axios', () => ({ default: { get: fakeGet } }))

class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  close() { this.closed = true }
  addEventListener() {}
}

import { BatchEventStream, fetchBatchEvents } from '../batchEvents'

describe('batchEvents URL 必须走 /api 前缀（生产反代回归）', () => {
  beforeEach(() => {
    FakeEventSource.last = null
    fakeGet.mockReset()
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('SSE 订阅 URL 以 /api/ai/chat/batches/events 开头', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const stream = new BatchEventStream(['b1', 'b2'], {})
    stream.open()
    expect(FakeEventSource.last).not.toBeNull()
    expect(FakeEventSource.last!.url.startsWith('/api/ai/chat/batches/events?')).toBe(true)
    stream.close()
  })

  it('REST 降级轮询 URL 为 /api/ai/chat/batches/<id>/events', async () => {
    fakeGet.mockResolvedValue({ data: { events: [], nextAfterSeq: 0, hasMore: false } })
    await fetchBatchEvents('b1', 0)
    expect(fakeGet).toHaveBeenCalledTimes(1)
    expect(fakeGet.mock.calls[0][0]).toBe('/api/ai/chat/batches/b1/events')
  })
})
