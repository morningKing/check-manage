import { get, post, authParam } from '@/utils/request'
import type { SubtaskMessagesResult } from './aiChat'

export interface AdminBatch {
  batchId: string
  name: string
  status: string
  total: number
  done: number
  failed: number
  agent: string | null
  model: string | null
  createdAt: string | null
  completedAt: string | null
  ownerUsername: string
  source: 'ui' | 'api'
}

export interface AdminChild {
  sessionId: string
  seq: number
  name: string
  status: string
  error: string | null
  preview: string | null
}

export interface AdminMessage {
  id: string
  role: string
  content: any[]
  createdAt: string | null
  meta: any
}

export interface AdminFilters {
  status?: string
  owner?: string
  source?: string
  keyword?: string
}

const BASE = '/ai/chat/admin/batches'

export function listAdminBatches(
  filters: AdminFilters, page: number, pageSize: number,
  config?: { silent?: boolean },
) {
  return get<{ items: AdminBatch[]; total: number }>(BASE, { ...filters, page, pageSize }, config)
}

export function getAdminBatch(batchId: string) {
  return get<{ batch: AdminBatch; sessions: AdminChild[] }>(`${BASE}/${batchId}`)
}

export function getAdminChildMessages(batchId: string, sessionId: string) {
  return get<{ messages: AdminMessage[]; truncated: boolean; total: number }>(
    `${BASE}/${batchId}/sessions/${sessionId}/messages`)
}

export function getAdminSubtaskMessages(batchId: string, sid: string, subtaskId: string) {
  return get<SubtaskMessagesResult>(
    `${BASE}/${batchId}/sessions/${sid}/subtasks/${subtaskId}/messages`)
}

export function retryAdminBatch(batchId: string) {
  return post<{ retried: number }>(`${BASE}/${batchId}/retry-failed`)
}

export function reexecuteAdminChild(batchId: string, sessionId: string) {
  return post<{ reexecuted: boolean }>(`${BASE}/${batchId}/sessions/${sessionId}/reexecute`)
}

export interface AdminChildFile {
  name: string
  path: string
  dir: 'uploads' | 'outputs' | 'workspace'
  size: number
  dataFileId: string | null
}

export interface AdminImportResult {
  path: string
  status?: 'imported' | 'existing'
  file?: { id: string; name: string; size: number }
  error?: string
  code?: 'NOT_RECORDED' | 'BAD_PATH' | 'FILE_MISSING' | 'TOO_LARGE' | 'IMPORT_FAILED' | 'PATHS_REQUIRED' | 'TOO_MANY'
}

export function listAdminChildFiles(batchId: string, sessionId: string) {
  return get<{ files: AdminChildFile[]; truncated: boolean }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files`)
}

export function getAdminChildFilePreview(batchId: string, sessionId: string, path: string) {
  return get<{ content: string; truncated: boolean; binary: boolean }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files/preview`, { path })
}

export function importAdminChildFiles(batchId: string, sessionId: string, paths: string[]) {
  return post<{ results: AdminImportResult[] }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files/import`, { paths })
}

// 返字符串 URL 而非 axios 请求：浏览器 <a href download> 直接下载
export function adminChildFileDownloadUrl(batchId: string, sessionId: string, path: string): string {
  const bid = encodeURIComponent(batchId)
  const sid = encodeURIComponent(sessionId)
  const p = encodeURIComponent(path)
  return `/api${BASE}/${bid}/sessions/${sid}/files/download?path=${p}${authParam('&')}`
}
