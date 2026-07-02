import { get, post } from '@/utils/request'

const VID_KEY = 'kefu:visitor_id'

export function getVisitorId(): string {
  let v = localStorage.getItem(VID_KEY)
  if (!v) { v = (crypto as any).randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2); localStorage.setItem(VID_KEY, v) }
  return v
}

function vh() { return { headers: { 'X-Visitor-Id': getVisitorId() } } }

export interface PanelBlock { id: string; type: 'links' | 'faq' | 'richtext' | 'contact'; title?: string; enabled?: boolean; config: any }
export interface KefuConfig { slug: string; name: string; welcome_message: string | null; guided_questions: string[]; branding: Record<string, any>; enabled: boolean; panel_blocks?: PanelBlock[] }
export interface KefuFaqItem { id: string; question: string; answer: string; category: string | null }
export interface KefuMessage { id: string; role: string; content: any; createdAt: string | null }

export function getKefuConfig(slug: string) { return get<KefuConfig>(`/kefu/i/${slug}`) }
export function createKefuSession(slug: string) { return post<{ id: string; title: string }>(`/kefu/i/${slug}/sessions`, {}, vh()) }
export function getKefuHistory(sid: string) { return get<{ messages: KefuMessage[] }>(`/kefu/sessions/${sid}/messages`, undefined, vh()) }
export function sendKefuMessage(sid: string, content: string) { return post<{ messageId: string }>(`/kefu/sessions/${sid}/messages`, { content }, vh()) }
export function getKefuFaq(slug: string) { return get<{ items: KefuFaqItem[] }>(`/kefu/i/${slug}/faq`) }
export function clickKefuFaq(slug: string, fid: string) { return post(`/kefu/i/${slug}/faq/${fid}/click`, {}, { ...vh(), silent: true }).catch(() => {}) }

const RECONNECT_MS = [1000, 2000, 5000, 10000]
export interface KefuStreamHandlers { onIdle: () => void; onError: (e: any) => void; onStatus?: (s: string) => void }

export function createKefuEventStream(sid: string, h: KefuStreamHandlers): () => void {
  const url = `/api/kefu/sessions/${encodeURIComponent(sid)}/events?visitor_id=${encodeURIComponent(getVisitorId())}`
  let es: EventSource | null = null, closed = false, attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const open = () => {
    if (closed) return
    es = new EventSource(url)
    es.addEventListener('session.idle', () => h.onIdle())
    es.addEventListener('session.error', (e: MessageEvent) => h.onError(e))
    es.onerror = () => {
      es?.close(); if (closed) return
      h.onStatus?.('reconnecting')
      timer = setTimeout(open, RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)]); attempt += 1
    }
    es.onopen = () => { attempt = 0; h.onStatus?.('open') }
  }
  open()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}
