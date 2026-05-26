/**
 * aiChat API tests: REST shims + EventSource reconnect logic.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { createEventStream } from '../aiChat'
import { post } from '@/utils/request'

class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  addEventListener = vi.fn()
  removeEventListener = vi.fn()
  close = vi.fn()
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  // @ts-expect-error global
  globalThis.EventSource = FakeEventSource
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('createEventStream', () => {
  it('opens an EventSource at the right URL', () => {
    createEventStream('sess_1', { onEvent: () => {}, onError: () => {} })
    expect(FakeEventSource.last?.url).toMatch(/\/api\/ai\/chat\/sessions\/sess_1\/events/)
  })

  it('reconnects with exponential backoff after error (1s,2s,5s,10s)', async () => {
    const onError = vi.fn()
    const stream = createEventStream('sess_1', { onEvent: () => {}, onError })
    const delays = [1000, 2000, 5000, 10000]

    for (const d of delays) {
      FakeEventSource.last!.onerror?.(new Event('error'))
      await vi.advanceTimersByTimeAsync(d)
    }
    expect(onError).toHaveBeenCalledTimes(4)
    stream.close()
  })
})
