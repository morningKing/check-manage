import { get, post, del, patch } from '@/utils/request'
import type {
  AiChatBatch, AiChatBatchDetail, StagedFile,
} from '@/types/aiChatBatch'

export function listBatches(page = 1, pageSize = 20) {
  return get<{ items: AiChatBatch[]; total: number }>('/ai/chat/batches', { page, pageSize })
}

export function getBatch(id: string) {
  return get<AiChatBatchDetail>(`/ai/chat/batches/${id}`)
}

export function createBatch(body: {
  name: string
  prompt: string
  template_id?: string | null
  agent?: string | null
  model?: string | null
  files: StagedFile[]
}) {
  return post<AiChatBatchDetail>('/ai/chat/batches', body)
}

export function deleteBatch(id: string) {
  return del<void>(`/ai/chat/batches/${id}`)
}

export function retryFailedSessions(id: string) {
  return post<{ retried: number }>(`/ai/chat/batches/${id}/retry-failed`)
}

export function appendBatch(id: string, files: StagedFile[]) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/append`, { files })
}

export function reexecuteChild(batchId: string, sessionId: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${batchId}/sessions/${sessionId}/reexecute`, {})
}

export function updateBatchConfig(id: string, body: { agent: string | null; model: string | null }) {
  return patch<AiChatBatchDetail>(`/ai/chat/batches/${id}`, body)
}

export function stagingUpload(file: File, uploadSessionId: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('upload_session_id', uploadSessionId)
  return post<StagedFile>('/ai/chat/batches/staging/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
