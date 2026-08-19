import { get, post, authParam } from '@/utils/request'

const BASE = '/ai/chat/admin/sessions/v2'

export interface AdminSession {
  id: string
  userId: string
  username: string
  title: string | null
  status: string
  sourceType: 'regular' | 'batch' | 'api_batch' | 'scan' | 'kefu'
  batchId: string | null
  batchName: string | null
  batchSeq: number | null
  inputFile: string | null
  scanTaskId: string | null
  lastMessagePreview: string | null
  errorMessage: string | null
  createdAt: string | null
  lastActiveAt: string | null
  opencodeSessionId: string | null
}

export interface AdminSessionDetail extends AdminSession {
  workspacePath: string | null
  batchApiKeyId: string | null
}

export interface AdminSessionMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: any[]
  createdAt: string | null
  meta: any
}

export interface AdminSessionFilters {
  status?: string
  sourceType?: string
  owner?: string
  keyword?: string
  batchId?: string
}

export interface AdminSessionFile {
  name: string
  path: string
  dir: string
  size: number
  dataFileId?: string | null
}

export function listSessions(
  filters: AdminSessionFilters, page: number, pageSize: number,
) {
  return get<{ items: AdminSession[]; total: number }>(
    BASE, { ...filters, page, pageSize },
  )
}

export function getSessionDetail(sessionId: string) {
  return get<AdminSessionDetail>(`${BASE}/${sessionId}`)
}

export function getSessionMessages(sessionId: string) {
  return get<{ messages: AdminSessionMessage[]; total: number; truncated: boolean }>(
    `${BASE}/${sessionId}/messages`,
  )
}

export function getSessionFiles(sessionId: string) {
  return get<{ files: AdminSessionFile[]; truncated: boolean }>(
    `${BASE}/${sessionId}/files`,
  )
}

export function archiveSession(sessionId: string) {
  return post(`/ai/chat/sessions/${sessionId}/archive`)
}

/**
 * 返回字符串 URL 而非 axios 请求：`window.open`/`<a href download>` 直接下载，
 * 拿不到 axios 拦截器加的 Authorization 头，靠 `require_permission_sse` 支持的
 * `?access_token=` 查询参数鉴权（跟 aiBatchAdmin.ts::adminChildFileDownloadUrl
 * 同一个模式）。
 */
export function sessionFileDownloadUrl(sessionId: string, path: string): string {
  const sid = encodeURIComponent(sessionId)
  const p = encodeURIComponent(path)
  return `/api${BASE}/${sid}/files/download?path=${p}${authParam('&')}`
}
