import { get, post, patch, del } from '@/utils/request'

export interface KefuInstance { id: string; slug: string; name: string; enabled: boolean }
export interface KefuFaq {
  id: string; instance_id: string; question: string; answer: string
  category: string | null; sort_order: number; click_count: number; enabled: boolean
}

export function listInstances() { return get<{ instances: KefuInstance[] }>('/admin/kefu/instances') }
export function listFaq(iid: string) { return get<{ items: KefuFaq[] }>(`/admin/kefu/instances/${iid}/faq`) }
export function createFaq(iid: string, data: Partial<KefuFaq>) { return post<KefuFaq>(`/admin/kefu/instances/${iid}/faq`, data) }
export function updateFaq(iid: string, fid: string, data: Partial<KefuFaq>) { return patch<KefuFaq>(`/admin/kefu/instances/${iid}/faq/${fid}`, data) }
export function deleteFaq(iid: string, fid: string) { return del(`/admin/kefu/instances/${iid}/faq/${fid}`) }
export function reorderFaq(iid: string, order: string[]) { return patch(`/admin/kefu/instances/${iid}/faq/reorder`, { order }) }

export interface KefuInstanceFull extends KefuInstance {
  agent?: string | null
  model?: string | null
  system_prompt?: string | null
  welcome_message?: string | null
  branding?: Record<string, any>
  guided_questions?: string[]
  panel_blocks?: any[]
}
export function getInstance(iid: string) { return get<KefuInstanceFull>(`/admin/kefu/instances/${iid}`) }
export function updateInstance(iid: string, data: Partial<KefuInstanceFull>) { return patch<KefuInstanceFull>(`/admin/kefu/instances/${iid}`, data) }
export function createInstance(data: Partial<KefuInstanceFull> & { slug: string; name: string }) {
  return post<KefuInstance>('/admin/kefu/instances', data)
}
export function deleteInstance(iid: string) {
  return del(`/admin/kefu/instances/${iid}`)
}

export function listSessions(params: { instance?: string; needs_human?: boolean; takeover?: boolean; status?: string }) {
  return get<{ sessions: any[] }>('/admin/kefu/sessions', params)
}
export function getSessionMessages(sid: string) { return get<{ messages: any[] }>(`/admin/kefu/sessions/${sid}/messages`) }
export function takeoverSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/takeover`, {}) }
export function releaseSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/release`, {}) }
export function humanReply(sid: string, content: string) { return post<{ messageId: string }>(`/admin/kefu/sessions/${sid}/messages`, { content }) }

export function requestSseTicket() { return post<{ ticket: string }>('/admin/kefu/events/ticket', {}) }

const ADMIN_SSE_BACKOFF = [1000, 2000, 5000, 10000]

export function createKefuAdminEventStream(
  instanceId: string,
  handlers: { onReady?: () => void; onEvent?: (e: { sid?: string; type?: string }) => void }
): () => void {
  let es: EventSource | null = null, closed = false, attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const EVENTS = ['visitor_message', 'needs_human', 'takeover', 'release', 'human_message']
  const retry = () => {
    if (closed) return
    timer = setTimeout(open, ADMIN_SSE_BACKOFF[Math.min(attempt, ADMIN_SSE_BACKOFF.length - 1)]); attempt += 1
  }
  const open = async () => {
    if (closed) return
    let ticket = ''
    try { ticket = (await requestSseTicket()).ticket } catch { retry(); return }
    if (closed) return
    es = new EventSource(`/api/admin/kefu/events?instance=${encodeURIComponent(instanceId)}&ticket=${encodeURIComponent(ticket)}`)
    es.addEventListener('ready', () => handlers.onReady?.())
    for (const name of EVENTS) {
      es.addEventListener(name, (e: MessageEvent) => {
        let data: any = {}
        try { data = JSON.parse(e.data) } catch { /* ignore */ }
        handlers.onEvent?.(data)
      })
    }
    es.onopen = () => { attempt = 0 }
    es.onerror = () => { es?.close(); if (closed) return; retry() }
  }
  open()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}
