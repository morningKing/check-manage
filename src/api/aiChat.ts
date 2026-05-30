/**
 * AI Chat API layer.
 *
 * - REST shims over /api/ai/chat/* via the shared axios `request` util.
 * - `createEventStream` opens an EventSource that auto-reconnects on error
 *   with delays 1s → 2s → 5s → 10s (then stops and reports). Caller may
 *   close at any time.
 */

import { get, post, del, patch } from '@/utils/request'

export interface AiSession {
  id: string
  title: string
  workspacePath: string
}

export interface AiSessionSummary {
  id: string
  title: string
  lastActiveAt?: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: AiContentPart[]
  createdAt?: string
}

export type AiContentPart =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; title?: string; status?: string; input?: unknown; result?: unknown }
  | { type: 'file'; name: string; path: string }
  | { type: 'run_result'; filename: string; exitCode: number; timedOut: boolean; stdout: string; stderr: string; outputFiles: string[] }
  | { type: 'mcp_services'; servers: McpServer[] }

export interface AiFile {
  name: string
  path: string
  dir: 'uploads' | 'outputs'
  size: number
}

export interface ChangedFile {
  path: string
  status: 'added' | 'modified' | 'deleted'
}

export interface McpTool { name: string; description: string }
export interface McpServer { name: string; status: string; tools: McpTool[] }

export function createSession(projectMenuId?: string) {
  return post<AiSession>('/ai/chat/sessions', { projectMenuId })
}

export function listSessions() {
  return get<{ sessions: AiSessionSummary[] }>('/ai/chat/sessions')
}

export function renameSession(id: string, title: string) {
  return patch<{ id: string; title: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}`, { title },
  )
}

export function deleteSession(id: string) {
  return del<void>(`/ai/chat/sessions/${encodeURIComponent(id)}`)
}

export function getMessages(id: string, since?: string) {
  const q = since ? `?since=${encodeURIComponent(since)}` : ''
  return get<{ messages: AiMessage[] }>(`/ai/chat/sessions/${encodeURIComponent(id)}/messages${q}`)
}

export function sendMessage(id: string, content: string, attachments: string[] = []) {
  return post<{ messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/messages`,
    { content, attachments },
  )
}

export function uploadFile(id: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return post<{ name: string; path: string; size: number }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/files`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}

export function uploadSkill(id: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return post<{ name: string; path: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/skills`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}

// The four loaders below run in the background on every openSession (and on
// session.idle); the store catches their failures on purpose. Pass `silent` so
// a transient backend blip doesn't fire a toast storm or 401-boot the user.

export function listFiles(id: string) {
  return get<{ files: AiFile[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/files`, undefined, { silent: true },
  )
}

export function getChanges(id: string) {
  return get<{ changes: ChangedFile[]; truncated: boolean }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/changes`, undefined, { silent: true },
  )
}

export function getMcpServices(id: string) {
  return get<{ servers: McpServer[]; error?: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/mcp`, undefined, { silent: true },
  )
}

export interface PaletteCommand { name: string; description: string }
export function getCommands(id: string) {
  return get<{ commands: PaletteCommand[]; skills: PaletteCommand[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/commands`, undefined, { silent: true },
  )
}
export function abortSession(id: string) {
  return post<{ ok: boolean }>(`/ai/chat/sessions/${encodeURIComponent(id)}/abort`)
}

export function postCommand(id: string, command: string, args: string) {
  return post<{ messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/command`, { command, arguments: args },
  )
}

export interface RunResult {
  exitCode: number
  timedOut: boolean
  stdout: string
  stderr: string
  outputFiles: string[]
  messageId?: string
}

export function runScript(id: string, code: string, filename?: string) {
  return post<RunResult & { messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/run`, { code, filename },
  )
}

export function downloadFileUrl(id: string, path: string): string {
  return `/api/ai/chat/sessions/${encodeURIComponent(id)}/files/download?path=${encodeURIComponent(path)}${authParam('&')}`
}

export type StreamStatus = 'open' | 'reconnecting' | 'closed'

export interface StreamHandlers {
  onEvent: (event: { event: string; data: unknown }) => void
  onError: (err: Event) => void
  onStatus?: (status: StreamStatus) => void
}

// Backoff schedule for SSE reconnects (ms). The last value is held as the
// steady-state interval — previously we gave up after the 4th try (~18s) and
// silently stopped polling; now we keep retrying indefinitely so a long
// backend/network blip just appears as "reconnecting" in the UI and resumes
// when service returns.
const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 20000, 60000]

// EventSource / download links can't set an Authorization header, so those
// endpoints accept the JWT via ?access_token=. Read the same token axios uses.
function authParam(prefix: '?' | '&' = '?'): string {
  const raw = localStorage.getItem('check-manage:token')
  if (!raw) return ''
  let token = raw
  try {
    const parsed = JSON.parse(raw)
    if (parsed) token = parsed
  } catch {
    /* raw is already the token string */
  }
  return token ? `${prefix}access_token=${encodeURIComponent(token)}` : ''
}

export function createEventStream(sessionId: string, h: StreamHandlers) {
  const url = `/api/ai/chat/sessions/${encodeURIComponent(sessionId)}/events${authParam('?')}`
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
      h.onStatus?.('reconnecting')
      const i = Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)
      timer = setTimeout(open, RECONNECT_DELAYS_MS[i])
      attempt += 1
    }
    es.onopen = () => { attempt = 0; h.onStatus?.('open') }
  }

  open()

  return {
    close() {
      closed = true
      if (timer) clearTimeout(timer)
      es?.close()
      h.onStatus?.('closed')
    },
  }
}
