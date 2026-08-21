export type BatchStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed'
// 'cancelled' 只会出现在对外 API 触发的取消（POST .../cancel，见
// docs/user-guide/integration/ai-batch-api.md）——UI 目前没有取消入口，但外部
// 调用方取消后，这条子会话状态会经同一个 batch worker 落回这张表，UI 侧的批
// 任务详情/管理页也会看到它，所以类型和展示分支都要能处理它。
export type BatchSessionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

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
