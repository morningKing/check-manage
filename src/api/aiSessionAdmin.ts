import { get, post, authParam } from '@/utils/request'

const BASE = '/ai/chat/admin/sessions/v2'

export interface AdminSession {
  id: string
  /** chat | trace_analysis（轨迹分析会话，默认列表隐藏） */
  kind?: string
  userId: string
  username: string
  title: string | null
  status: string
  sourceType: 'regular' | 'batch' | 'api_batch' | 'scan' | 'kefu'
  batchId: string | null
  batchName: string | null
  batchSeq: number | null
  inputFile: string | null
  scanTaskId: string | null
  lastMessagePreview: string | null
  errorMessage: string | null
  createdAt: string | null
  lastActiveAt: string | null
  opencodeSessionId: string | null
}

export interface AdminSessionDetail extends AdminSession {
  workspacePath: string | null
  batchApiKeyId: string | null
}

export interface AdminSessionMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: any[]
  createdAt: string | null
  meta: any
}

export interface AdminSessionFilters {
  status?: string
  sourceType?: string
  owner?: string
  keyword?: string
  batchId?: string
  /** all=包含轨迹分析会话；trace_analysis=只看分析会话；缺省=排除 */
  kind?: string
}

export interface AdminSessionFile {
  name: string
  path: string
  dir: string
  size: number
  dataFileId?: string | null
}

export function listSessions(
  filters: AdminSessionFilters, page: number, pageSize: number,
) {
  return get<{ items: AdminSession[]; total: number }>(
    BASE, { ...filters, page, pageSize },
  )
}

export function getSessionDetail(sessionId: string) {
  return get<AdminSessionDetail>(`${BASE}/${sessionId}`)
}

export function getSessionMessages(sessionId: string) {
  return get<{ messages: AdminSessionMessage[]; total: number; truncated: boolean }>(
    `${BASE}/${sessionId}/messages`,
  )
}

export function getSessionFiles(sessionId: string) {
  return get<{ files: AdminSessionFile[]; truncated: boolean }>(
    `${BASE}/${sessionId}/files`,
  )
}

export function archiveSession(sessionId: string) {
  return post(`/ai/chat/sessions/${sessionId}/archive`)
}

/**
 * 返回字符串 URL 而非 axios 请求：`window.open`/`<a href download>` 直接下载，
 * 拿不到 axios 拦截器加的 Authorization 头，靠 `require_permission_sse` 支持的
 * `?access_token=` 查询参数鉴权（跟 aiBatchAdmin.ts::adminChildFileDownloadUrl
 * 同一个模式）。
 */
export function sessionFileDownloadUrl(sessionId: string, path: string): string {
  const sid = encodeURIComponent(sessionId)
  const p = encodeURIComponent(path)
  return `/api${BASE}/${sid}/files/download?path=${p}${authParam('&')}`
}

/**
 * Trigger trace analysis for a session. Creates a new analysis session
 * with the trace-analyzer skill and returns its ID.
 */
export function analyzeSession(sessionId: string) {
  return post<{ analysisId: string; analysisSessionId: string; message: string }>(
    `${BASE}/${sessionId}/analyze`,
  )
}

// ── 执行合规审计（execution-audit Spec §15） ────────────────────────────

const EXEC_BASE = '/ai/chat/admin'

export interface ExecutionAttempt {
  id: string
  sessionId: string
  sourceType: string
  sourceId: string | null
  parentAttemptId: string | null
  attemptNo: number
  operation: string
  requestedAgent: string | null
  effectiveAgent: string | null
  agentResolution: string
  requestedModel: string | null
  effectiveModel: string | null
  modelResolution: string
  rawPromptHash: string | null
  effectivePromptHash: string | null
  effectivePromptLen: number | null
  status: string
  errorCode: string | null
  errorMessage: string | null
  startedAt: string | null
  finishedAt: string | null
}

export interface ExecutionManifest {
  kind: string
  name: string
  source: string
  path: string | null
  contentHash: string | null
  injected: boolean
  injectionStatus: string
  runtimeLoaded: string
  selected: string
  invoked: string
}

export interface ExecutionStep {
  step_id: string
  expected: boolean
  declared_by_agent: boolean
  observed: boolean
  status: string
  evidence_level: string
  evidence_refs: string[]
  reason?: string
  duration_ms?: number | null
}

export interface ToolFailure {
  tool: string
  failure_type: string
  input_preview?: string
  result_preview?: string
  duration_ms?: number
  evidence_refs: string[]
  recovery?: {
    attempted: boolean
    same_input_retry: boolean
    strategy_changed: boolean
    recovered: boolean
  }
}

export interface ExecutionReport {
  schema_version?: string
  status?: string
  diagnosis_id?: string
  execution?: {
    attempt_id?: string
    agent?: { requested?: string; effective?: string; resolution?: string }
    model?: { requested?: string; effective?: string; resolution?: string }
    prompt?: { raw_hash?: string; effective_hash?: string; effective_len?: number }
    skills?: ExecutionManifest[]
  }
  contract?: {
    status: string
    reason?: string
    skill?: string
    steps?: ExecutionStep[]
    violations?: Array<{ type: string; step_id: string; severity: string; reason?: string }>
  }
  declared_plan?: {
    declared_steps?: Array<{ id?: string; content: string; status: string; status_changes?: number }>
    snapshot_count?: number
    completed_count?: number
    total_count?: number
  }
  tool_failures?: ToolFailure[]
  step_completion?: { required_total: number; required_completed_confirmed: number; rate: number | null }
  data_completeness?: { score: number; limitations: string[] }
}

export interface ExecutionAuditPayload {
  attempts: ExecutionAttempt[]
  currentAttempt: ExecutionAttempt | null
  manifests: ExecutionManifest[]
  events: Array<{ event_seq: number; event_type: string; occurred_at: string | null; status: string | null }>
  report: ExecutionReport | null
}

export function getExecutionAudit(sessionId: string) {
  return get<ExecutionAuditPayload>(`${EXEC_BASE}/sessions/${sessionId}/execution-audit`)
}

export function getExecutionPrompt(sessionId: string, attemptId?: string) {
  return get<{
    attemptId: string
    rawUserContent: string | null
    effectivePrompt: string | null
    rawHash: string | null
    effectiveHash: string | null
    effectiveLen: number | null
    augmentations: Record<string, unknown>
    plaintextAvailable: boolean
  }>(`${EXEC_BASE}/sessions/${sessionId}/execution-prompt`,
     attemptId ? { attemptId } : undefined)
}

export interface SessionAnalysis {
  id: string
  analysis_session_id: string
  status: string
  data_completeness: number | null
  error_message: string | null
  created_at: string | null
  completed_at: string | null
}

/** 某会话的轨迹分析历史（execution-audit：分析与原会话的关联入口） */
export function getSessionAnalyses(sessionId: string) {
  return get<{ analyses: SessionAnalysis[] }>(
    `${BASE}/${sessionId}/analyses`)
}

export function getAnalysisStatus(analysisId: string) {
  return get<{
    analysisId: string
    targetSessionId: string
    analysisSessionId: string
    status: string
    dataCompleteness: number | null
    error: string | null
    createdAt: string | null
    completedAt: string | null
  }>(`${EXEC_BASE}/analyses/${analysisId}`)
}
