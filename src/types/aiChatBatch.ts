export type BatchStatus = 'pending' | 'running' | 'paused' | 'completed' | 'partial' | 'failed' | 'cancelled'
// 'cancelled' 只会出现在对外 API 触发的取消（POST .../cancel，见
// docs/user-guide/integration/ai-batch-api.md）——UI 目前没有取消入口，但外部
// 调用方取消后，这条子会话状态会经同一个 batch worker 落回这张表，UI 侧的批
// 任务详情/管理页也会看到它，所以类型和展示分支都要能处理它。
// 'paused' 是批任务「暂停」落的非终态（不占 failed 计数），可被 resume 续跑。
export type BatchSessionStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'
import type { AiTraceMetadata } from './aiChat'

export interface AiChatBatch {
  id: string
  user_id: string
  name: string
  prompt: string
  template_id: string | null
  agent: string | null
  model: string | null
  provision_repo?: string | null
  provision_ref?: string | null
  /** Set when this batch was created by an AI 定时任务 scan run
   *  (ai_scan_engine.run_task), not a user via 新建批任务/对外 API.
   *  Drives the "AI定时任务" sidebar section in AiChatView.vue. */
  scan_task_id?: string | null
  status: BatchStatus
  total: number
  done: number
  failed: number
  /** status='cancelled' 的子任务数（计入 failed 聚合计数内）。中断产生的
   *  子任务处于该状态，可通过「继续运行」在原工作上恢复 —— 与 failed
   *  （重试失败，从头重跑）是两个互不重叠的恢复入口。 */
  cancelled?: number
  /** status='paused' 的子任务数（非终态，不占 failed 计数）。暂停产生的
   *  子任务处于该状态，同样可通过「继续运行」续跑。 */
  paused?: number
  created_at: string
  completed_at: string | null
}

export interface AiChatBatchSession {
  id: string
  status: BatchSessionStatus
  batch_seq: number
  batch_input_file: string
  opencode_session_id: string | null
  error_message: string | null
  last_message_preview: string | null
  traceUrl?: AiTraceMetadata['traceUrl']
  traceId?: AiTraceMetadata['traceId']
}

export interface AiChatBatchDetail {
  batch: AiChatBatch
  sessions: AiChatBatchSession[]
}

export interface AiChatPromptTemplate {
  id: string
  user_id: string
  name: string
  content: string
  created_at: string
  updated_at: string
}

export interface StagedFile {
  name: string
  path: string
}
