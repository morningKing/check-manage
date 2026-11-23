import { get, put, post, del } from '@/utils/request'

/** 记忆管理（mem0 长期记忆）管理面 API —— /ai/chat/admin/memory/* */

export interface MemoryStatus {
  /** mem0 单例是否初始化成功（未启用/初始化失败为 false） */
  ready: boolean
  storePath: string
  storeExists: boolean
}

export interface MemoryConfig {
  mem0Enabled: boolean
  embeddingProvider: 'api' | 'ollama'
  embeddingModel: string
  embeddingOllamaUrl: string
  embeddingDims: number
  /** 摘要 LLM 复用主 API 配置（只读展示；apiKey 掩码） */
  endpoint: string
  model: string
  apiKey: string
  status: MemoryStatus
}

export interface MemoryUpdatePayload {
  mem0Enabled?: boolean
  embeddingProvider?: 'api' | 'ollama'
  embeddingModel?: string
  embeddingOllamaUrl?: string
  embeddingDims?: number
}

export function getMemoryConfig() {
  return get<MemoryConfig>('/ai/chat/admin/memory')
}

/** read-merge 语义：只传需要覆盖的记忆字段，模型页字段不受影响 */
export function updateMemoryConfig(payload: MemoryUpdatePayload) {
  return put<MemoryConfig>('/ai/chat/admin/memory', payload)
}

export interface MemoryUser {
  id: string
  username: string
}

export function searchMemoryUsers(q: string) {
  return get<{ users: MemoryUser[] }>('/ai/chat/admin/memory/users', { q })
}

/** mem0 返回的记忆条目形状不归本仓库控制，原样透传 */
export interface MemoryEntry {
  id: string
  memory: string
  createdAt?: string | null
  updatedAt?: string | null
  [key: string]: unknown
}

export interface MemoryEntriesResult {
  ownerUserId: string
  ownerUsername: string
  memories: MemoryEntry[]
}

export function listMemoryEntries(owner: string, query?: string, limit = 50) {
  return get<MemoryEntriesResult>('/ai/chat/admin/memory/entries',
    { owner, query: query || undefined, limit })
}

export function deleteMemoryEntry(memoryId: string, owner: string) {
  return del<{ ok: boolean }>(
    `/ai/chat/admin/memory/entries/${encodeURIComponent(memoryId)}`,
    { params: { owner } })
}

export interface MemoryTestSearchResult {
  ownerUserId: string
  ownerUsername: string
  results: MemoryEntry[]
  /** render_memory_block 的注入提示词原文——页面所见 = 模型所见 */
  rendered: string
}

export function testMemorySearch(owner: string, query: string, limit = 5) {
  return post<MemoryTestSearchResult>('/ai/chat/admin/memory/test/search',
    { owner, query, limit })
}

export function testMemoryAdd(owner: string, text: string, verbatim = false) {
  return post<{ ok: boolean; memories: MemoryEntry[] }>(
    '/ai/chat/admin/memory/test/add', { owner, text, verbatim })
}
