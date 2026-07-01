import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string; listeners: Record<string, (e: any) => void> = {}
  onerror: any = null; onopen: any = null
  constructor(url: string) { this.url = url; FakeEventSource.last = this }
  addEventListener(name: string, cb: (e: any) => void) { this.listeners[name] = cb }
  close() {}
  emit(name: string, data: any) { this.listeners[name]?.({ data: JSON.stringify(data) }) }
}

beforeEach(() => {
  ;(globalThis as any).EventSource = FakeEventSource
  localStorage.clear()
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
})
