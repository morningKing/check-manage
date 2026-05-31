export type BatchStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed'
export type BatchSessionStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface AiChatBatch {
  id: string
  user_id: string
  name: string
  prompt: string
  template_id: string | null
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
