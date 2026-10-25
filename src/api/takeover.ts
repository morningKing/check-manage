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

// 三个函数一律 silent：全局拦截器对非 2xx 会先弹 ElMessage 再 reject，
// 而本页 3s 轮询 + 页面自绘 409 内联警告 / 404 错误卡，全局 toast 只会
// 变成轮询抖动时的连环弹窗与 409 双重提示（kefuPublic.clickKefuFaq 同款做法）。
export function getTakeoverSession(t: string) {
  return get<TakeoverSessionInfo>(`/external/takeover/session${qs(t)}`, undefined, { silent: true })
}
export function getTakeoverMessages(t: string) {
  return get<{ childStatus: string; messages: TakeoverMessage[]; total: number; truncated: boolean }>(
    `/external/takeover/session/messages${qs(t)}`, undefined, { silent: true })
}
export function sendTakeoverMessage(t: string, prompt: string) {
  return post<{ queued: boolean }>(`/external/takeover/session/messages${qs(t)}`, { prompt }, { silent: true })
}
