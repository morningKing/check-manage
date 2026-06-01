import { get, post, del } from '@/utils/request'
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

export function stagingUpload(file: File, uploadSessionId: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('upload_session_id', uploadSessionId)
  return post<StagedFile>('/ai/chat/batches/staging/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
