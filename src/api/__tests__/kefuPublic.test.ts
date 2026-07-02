import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Hoisted by Vitest — applies to all imports of @/utils/request in this file and modules loaded from it
vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
}))

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
  vi.clearAllMocks()  // reset post/get call counts between tests
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

describe('uploadKefuFile', () => {
  it('POSTs multipart to the files endpoint with X-Visitor-Id and returns json', async () => {
    localStorage.setItem('kefu:visitor_id', 'vid-1')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ name: 'a.txt', path: 'uploads/a.txt', size: 3 }) })
    ;(globalThis as any).fetch = fetchMock
    const { uploadKefuFile } = await import('../kefuPublic')
    const res = await uploadKefuFile('sess_1', new File(['abc'], 'a.txt', { type: 'text/plain' }))
    expect(res.path).toBe('uploads/a.txt')
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/kefu/sessions/sess_1/files')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Visitor-Id']).toBe('vid-1')
    expect(opts.headers['Content-Type']).toBeUndefined()
    expect(opts.body).toBeInstanceOf(FormData)
  })

  it('throws with backend error message on non-2xx', async () => {
    ;(globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: '文件超过 20MB 上限' }) })
    const { uploadKefuFile } = await import('../kefuPublic')
    await expect(uploadKefuFile('sess_1', new File(['x'], 'x.bin'))).rejects.toThrow('文件超过 20MB 上限')
  })
})

describe('sendKefuMessage attachments', () => {
  it('passes attachments in the body', async () => {
    const { post } = await import('@/utils/request')
    vi.mocked(post).mockResolvedValue({ messageId: 'm1' })
    const { sendKefuMessage } = await import('../kefuPublic')
    await sendKefuMessage('sess_1', 'hi', ['uploads/a.txt'])
    const [, body] = vi.mocked(post).mock.calls[0]
    expect(body).toEqual({ content: 'hi', attachments: ['uploads/a.txt'] })
  })
})
