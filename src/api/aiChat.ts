/**
 * AI Chat API layer.
 *
 * - REST shims over /api/ai/chat/* via the shared axios `request` util.
 * - `createEventStream` opens an EventSource that auto-reconnects on error
 *   with delays 1s → 2s → 5s → 10s (then stops and reports). Caller may
 *   close at any time.
 */

import { get, post, del } from '@/utils/request'

export interface AiSession {
  id: string
  title: string
  workspacePath: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: AiContentPart[]
  createdAt?: string
}

export type AiContentPart =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; input: unknown; result?: unknown }

export function createSession(projectMenuId?: string) {
  return post<AiSession>('/ai/chat/sessions', { projectMenuId })
}

export function deleteSession(id: string) {
  return del<void>(`/ai/chat/sessions/${encodeURIComponent(id)}`)
}

export function getMessages(id: string, since?: string) {
  const q = since ? `?since=${encodeURIComponent(since)}` : ''
  return get<{ messages: AiMessage[] }>(`/ai/chat/sessions/${encodeURIComponent(id)}/messages${q}`)
}

export function sendMessage(id: string, content: string) {
  return post<{ messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/messages`,
    { content },
  )
}

export interface StreamHandlers {
  onEvent: (event: { event: string; data: unknown }) => void
  onError: (err: Event) => void
}

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000]

// EventSource can't set an Authorization header, so the SSE endpoint accepts
// the JWT via ?access_token=. Read the same token the axios layer uses.
function authQuery(): string {
  const raw = localStorage.getItem('check-manage:token')
  if (!raw) return ''
  let token = raw
  try {
    const parsed = JSON.parse(raw)
    if (parsed) token = parsed
  } catch {
    /* raw is already the token string */
  }
  return token ? `?access_token=${encodeURIComponent(token)}` : ''
}

export function createEventStream(sessionId: string, h: StreamHandlers) {
  const url = `/api/ai/chat/sessions/${encodeURIComponent(sessionId)}/events${authQuery()}`
  let es: EventSource | null = null
  let closed = false
  let attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null

  const open = () => {
    if (closed) return
    es = new EventSource(url)
    es.onmessage = (e) => {
      try {
        h.onEvent({ event: 'message', data: JSON.parse(e.data) })
      } catch {
        h.onEvent({ event: 'message', data: e.data })
      }
    }
    // Real OpenCode event names (spec §12.4), re-emitted by the Flask SSE proxy
    for (const name of ['message.updated', 'message.part.updated', 'session.idle', 'session.error']) {
      es.addEventListener(name, (e: MessageEvent) => {
        try {
          h.onEvent({ event: name, data: JSON.parse(e.data) })
        } catch {
          h.onEvent({ event: name, data: e.data })
        }
      })
    }
    es.onerror = (err) => {
      h.onError(err)
      es?.close()
      if (closed) return
      if (attempt < RECONNECT_DELAYS_MS.length) {
        timer = setTimeout(open, RECONNECT_DELAYS_MS[attempt])
        attempt += 1
      }
    }
    es.onopen = () => { attempt = 0 }
  }

  open()

  return {
    close() {
      closed = true
      if (timer) clearTimeout(timer)
      es?.close()
    },
  }
}
