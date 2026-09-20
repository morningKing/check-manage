import { get, post, del, patch } from '@/utils/request'
import type {
  AiChatBatch, AiChatBatchDetail, StagedFile,
} from '@/types/aiChatBatch'

export function listBatches(page = 1, pageSize = 20) {
  return get<{ items: AiChatBatch[]; total: number; maxSessions?: number }>(
    '/ai/chat/batches', { page, pageSize })
}

export function getBatch(id: string) {
  return get<AiChatBatchDetail>(`/ai/chat/batches/${id}`)
}

export interface ActionCheck {
  name: string
  tool: string
  args_pattern: string
  min_count?: number
  scope?: 'session' | 'tree'
  check_type?: 'tool' | 'file' | 'db_record'
  effect_spec?: Record<string, unknown>
}

export function createBatch(body: {
  name: string
  prompt: string
  template_id?: string | null
  agent?: string | null
  model?: string | null
  provision_repo?: string | null
  provision_ref?: string | null
  /** 动作门禁期望(设计 §5.2 入口 A):子任务终态逐条核对账本,不过门落 failed */
  action_checks?: ActionCheck[] | null
  files: StagedFile[]
}) {
  return post<AiChatBatchDetail>('/ai/chat/batches', body)
}

/** P0 §10.4：非终态（pending/running/paused）批次不允许直接删除——stop=true
 *  走「停止并删除」：先取消全部子任务并等待运行中的落地，再清理删除。 */
export function deleteBatch(id: string, stop = false) {
  return del<void>(`/ai/chat/batches/${id}${stop ? '?stop=1' : ''}`)
}

export function retryFailedSessions(id: string) {
  return post<{ retried: number }>(`/ai/chat/batches/${id}/retry-failed`)
}

/** M1.5(设计 §5.2):AI 提炼动作门禁期望建议——只产出建议,登记仍走人工确认 */
export function extractActionChecks(body: {
  task_text: string
  agent?: string | null
  skills?: string[]
}) {
  return post<{ checks: ActionCheck[] }>(
    '/ai/chat/batches/action-checks/extract', body)
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

/** 单独继续一个已暂停（paused）的子任务：已开跑过的在原 OpenCode 会话/工作区
 *  从中断处续跑；其余已暂停/已中断的子任务保持不动。 */
export function resumeChild(batchId: string, sessionId: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${batchId}/sessions/${sessionId}/resume`, {})
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
