import { get, post } from '@/utils/request'

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

export function retryAdminBatch(batchId: string) {
  return post<{ retried: number }>(`${BASE}/${batchId}/retry-failed`)
}

export function reexecuteAdminChild(batchId: string, sessionId: string) {
  return post<{ reexecuted: boolean }>(`${BASE}/${batchId}/sessions/${sessionId}/reexecute`)
}
