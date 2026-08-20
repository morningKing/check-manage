/**
 * aiChat API tests: REST shims + EventSource reconnect logic.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  authParam: vi.fn(() => ''),
}))

import { createEventStream } from '../aiChat'

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

  it('registers a named listener for every custom SSE event the store handles', () => {
    // Regression guard: `es.onmessage` only catches the unnamed default SSE
    // event. A named OpenCode event (e.g. `question.asked`) needs an explicit
    // addEventListener() call here or it's silently dropped — the store's
    // _handleEvent switch never even sees it. Bit this exact bug once: the
    // store had a `question.asked` case that worked in unit tests (which call
    // _handleEvent directly) but never fired for real because this list
    // hadn't been updated.
    createEventStream('sess_1', { onEvent: () => {}, onError: () => {} })
    const names = FakeEventSource.last!.addEventListener.mock.calls.map((c: any[]) => c[0])
    expect(names).toEqual(expect.arrayContaining([
      'message.updated', 'message.part.updated', 'session.idle', 'session.error',
      'question.asked', 'question.replied', 'question.rejected',
    ]))
  })
})
