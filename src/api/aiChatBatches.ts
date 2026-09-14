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
  provision_repo?: string | null
  provision_ref?: string | null
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

/** 中断整个批次：排队中的直接取消，运行中的协作式中断，已暂停的落成取消。
 *  之后可 resumeBatch 在原工作上继续。 */
export function stopBatch(id: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/cancel`, {})
}

/** 暂停整批排队/运行中的子任务：落在非终态 paused（不占 failed 计数），可续跑。 */
export function pauseBatch(id: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/pause`, {})
}

/** 继续执行已暂停（paused）/已中断（cancelled）的子任务：已开跑过的在原
 *  OpenCode 会话/工作区续跑，保留历史。 */
export function resumeBatch(id: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/resume`, {})
}

export function appendBatch(id: string, files: StagedFile[]) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/append`, { files })
}

export function reexecuteChild(batchId: string, sessionId: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${batchId}/sessions/${sessionId}/reexecute`, {})
}

export function cancelChild(batchId: string, sessionId: string) {
  return post<{ id: string; status: string }>(`/ai/chat/batches/${batchId}/sessions/${sessionId}/cancel`, {})
}

export function updateBatchConfig(id: string, body: {
  agent: string | null
  model: string | null
  provision_repo?: string | null
  provision_ref?: string | null
}) {
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
