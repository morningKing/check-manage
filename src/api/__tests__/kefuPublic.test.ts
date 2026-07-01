import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

class FakeEventSource {
  static last: FakeEventSource | null = null
  static constructionCount = 0
  url: string; listeners: Record<string, (e: any) => void> = {}
  onerror: any = null; onopen: any = null
  constructor(url: string) { this.url = url; FakeEventSource.last = this; FakeEventSource.constructionCount += 1 }
  addEventListener(name: string, cb: (e: any) => void) { this.listeners[name] = cb }
  close() {}
  emit(name: string, data: any) { this.listeners[name]?.({ data: JSON.stringify(data) }) }
}

beforeEach(() => {
  ;(globalThis as any).EventSource = FakeEventSource
  localStorage.clear()
  FakeEventSource.constructionCount = 0
})
afterEach(() => { vi.restoreAllMocks() })

describe('getVisitorId', () => {
  it('generates and persists a visitor id', async () => {
    const { getVisitorId } = await import('../kefuPublic')
    const a = getVisitorId()
    expect(a).toBeTruthy()
    expect(getVisitorId()).toBe(a)  // stable
    expect(localStorage.getItem('kefu:visitor_id')).toBe(a)
  })
})

describe('createKefuEventStream', () => {
  it('opens EventSource with visitor_id query param and routes session.idle', async () => {
    const { createKefuEventStream } = await import('../kefuPublic')
    const onIdle = vi.fn()
    createKefuEventStream('sess_1', { onIdle, onError: () => {} })
    expect(FakeEventSource.last!.url).toContain('/api/kefu/sessions/sess_1/events')
    expect(FakeEventSource.last!.url).toContain('visitor_id=')
    FakeEventSource.last!.emit('session.idle', { ok: true })
    expect(onIdle).toHaveBeenCalledTimes(1)
  })

  it('routes session.error to onError', async () => {
    const { createKefuEventStream } = await import('../kefuPublic')
    const onError = vi.fn()
    createKefuEventStream('sess_1', { onIdle: () => {}, onError })
    ;(FakeEventSource as any).last.emit('session.error', { boom: true })
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('does not reconnect after close() when onerror fires', async () => {
    const { createKefuEventStream } = await import('../kefuPublic')
    const onStatus = vi.fn()
    const countBefore = FakeEventSource.constructionCount
    const close = createKefuEventStream('sess_1', { onIdle: () => {}, onError: () => {}, onStatus })
    const firstES = FakeEventSource.last
    expect(FakeEventSource.constructionCount).toBe(countBefore + 1)
    close()
    ;(FakeEventSource as any).last?.onerror?.(new Event('error'))
    expect(FakeEventSource.constructionCount).toBe(countBefore + 1)  // no new EventSource created
    expect(FakeEventSource.last).toBe(firstES)  // still the same instance
  })
})
