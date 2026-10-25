import { get, post } from '@/utils/request'

export interface TakeoverSessionInfo {
  batchName: string
  batchSeq: number | null
  inputFile: string | null
  title: string | null
  childStatus: string
  batchStatus: string
  expiresAt: string
}
export interface TakeoverMessage {
  id: string
  role: string
  content: Array<{ type: string; text?: string }> | any
  createdAt: string | null
}

const qs = (t: string) => `?t=${encodeURIComponent(t)}`

export function getTakeoverSession(t: string) {
  return get<TakeoverSessionInfo>(`/external/takeover/session${qs(t)}`)
}
export function getTakeoverMessages(t: string) {
  return get<{ childStatus: string; messages: TakeoverMessage[]; total: number; truncated: boolean }>(
    `/external/takeover/session/messages${qs(t)}`)
}
export function sendTakeoverMessage(t: string, prompt: string) {
  return post<{ queued: boolean }>(`/external/takeover/session/messages${qs(t)}`, { prompt })
}
