/**
 * 批任务事件流客户端（P1 §7.2 / 缺口补齐计划 4.1）。
 *
 * 订阅 `GET /ai/chat/batches/events?ids=...&access_token=<jwt>`：
 *  - `batch_event` 帧（id: `<batchId>:<seq>`）→ onEvent(batchId, event)
 *  - `batch_done` 帧 → onDone(batchId)
 *  - 断线自动重连：携带 `Last-Event-ID`（或重连后回退轮询由调用方决定）
 *
 * 设计约束：/ai-chat 页有常驻 SSE，严禁等待 networkidle（e2e 同源约定）；
 * 事件事实源是 ai_batch_events 表，本客户端只做「推」的通道，轮询保留为降级。
 */
import axios from 'axios'

export interface BatchEvent {
  eventId: string
  eventSeq: number
  type: string
  data: Record<string, unknown>
  batchId: string
}

export interface BatchEventsHandlers {
  onEvent?: (batchId: string, event: BatchEvent) => void
  onDone?: (batchId: string) => void
  onError?: (err: unknown) => void
  /** 连接建立（open）时回调——调用方可借此停掉降级轮询 */
  onOpen?: () => void
}

function readToken(): string {
  const raw = localStorage.getItem('check-manage:token')
  if (!raw) return ''
  try {
    const parsed = JSON.parse(raw)
    return parsed ? String(parsed) : String(raw)
  } catch {
    return String(raw)
  }
}

/** 解析 SSE `id:` 行（形如 `<batchId>:<seq>`）。 */
function parseSseId(id: string): { batchId: string; seq: number } | null {
  const i = id.lastIndexOf(':')
  if (i <= 0) return null
  const seq = Number(id.slice(i + 1))
  if (!Number.isFinite(seq)) return null
  return { batchId: id.slice(0, i), seq }
}

/** 解析 `data:` JSON 帧为事件（含归属 batchId）。 */
function parseEventData(raw: string, idLine: string): BatchEvent | null {
  try {
    const doc = JSON.parse(raw)
    const parsed = parseSseId(idLine || '')
    const batchId = doc.batchId || parsed?.batchId || ''
    if (!batchId) return null
    return {
      batchId,
      eventId: doc.eventId || doc.id || '',
      eventSeq: Number(doc.eventSeq ?? parsed?.seq ?? 0),
      type: doc.type || doc.eventType || '',
      data: doc.data || doc.payload || {},
    }
  } catch {
    return null
  }
}

/** 单个批任务的事件流订阅句柄。 */
export class BatchEventStream {
  private es: EventSource | null = null
  private lastSeqByBatch = new Map<string, number>()
  private closed = false

  constructor(private batchIds: string[], private handlers: BatchEventsHandlers) {}

  open(): void {
    if (this.closed || this.es) return
    const ids = this.batchIds.join(',')
    // 必须带 /api 前缀：生产 proxy.py 只转发 /api/*，裸路径会被 SPA fallback
    // 以 index.html（text/html）兜底，EventSource 报 MIME type 错误。
    const url = `/api/ai/chat/batches/events?ids=${encodeURIComponent(ids)}` +
      `&access_token=${encodeURIComponent(readToken())}`
    const es = new EventSource(url)
    this.es = es
    es.onopen = () => this.handlers.onOpen?.()
    es.onmessage = (ev: MessageEvent) => {
      if (this.closed) return
      const idLine = (ev as MessageEvent & { lastEventId?: string }).lastEventId || ''
      const parsed = parseSseId(idLine)
      const doc = parseEventData(ev.data, idLine)
      if (String(ev.data || '').trim() === 'batch_done' || doc === null && parsed) {
        if (parsed) this.handlers.onDone?.(parsed.batchId)
        return
      }
      if (!doc) return
      const prev = this.lastSeqByBatch.get(doc.batchId) || 0
      if (doc.eventSeq && doc.eventSeq <= prev) return // 重复帧幂等丢弃
      if (doc.eventSeq) this.lastSeqByBatch.set(doc.batchId, doc.eventSeq)
      this.handlers.onEvent?.(doc.batchId, doc)
    }
    es.onerror = () => {
      if (this.closed) return
      // EventSource 自动重连（带 Last-Event-ID，由浏览器维护）
      this.handlers.onError?.(new Error('sse disconnected'))
    }
  }

  /** 增加订阅的批次（重建连接；现有 lastSeq 保留用于去重）。 */
  setBatchIds(ids: string[]): void {
    this.batchIds = ids
    this.close()
    this.closed = false
    this.open()
  }

  close(): void {
    this.closed = true
    this.es?.close()
    this.es = null
  }
}

/** 批任务详情增量拉取（REST 降级/合并通道）。 */
export async function fetchBatchEvents(
  batchId: string, afterSeq: number, limit = 200,
): Promise<{ events: BatchEvent[]; nextAfterSeq: number; hasMore: boolean }> {
  // 裸 axios 没有 request.ts 的 baseURL:'/api'，前缀必须写全（同上 MIME 回归）。
  const { data } = await axios.get(`/api/ai/chat/batches/${batchId}/events`, {
    params: { afterSeq, limit },
  })
  return {
    events: (data.events || []).map((e: Record<string, unknown>) => ({
      batchId,
      eventId: String(e.eventId || ''),
      eventSeq: Number(e.eventSeq || 0),
      type: String(e.type || ''),
      data: (e.data || {}) as Record<string, unknown>,
    })),
    nextAfterSeq: Number(data.nextAfterSeq ?? afterSeq),
    hasMore: Boolean(data.hasMore),
  }
}
