/**
 * AI 编排管理 API 封装（P3-A7）
 *
 * 后端路由：server/routes/ai_orchestrations.py，url_prefix='/ai/orchestrations'。
 * 出参键与后端对齐（不是任务书里的 items 草案）：
 * - 定义列表 / 运行列表的集合键分别是 `definitions` / `runs`
 * - run 详情（get_run）把 steps 内嵌在 run 对象里，字段保持 snake_case；
 *   仅列表项的 created_at 被后端改名为 createdAt
 * - 发布定义需要 `admin.ai_orchestration_admin` 能力（后端 403 由全局拦截器提示）
 */
import { get, post } from '@/utils/request'

const BASE = '/ai/orchestrations'

/** 编排定义列表项（list_definitions：每个 id 取最新已发布版本） */
export interface OrchDefinition {
  id: string
  version: number
  name: string
  description: string | null
  publishedAt: string | null
}

/** 编排定义详情（get_definition：含 nodes/edges 拓扑，供 DAG 编辑器加载） */
export interface OrchDefinitionDetail extends OrchDefinition {
  nodes: any[]
  edges: any[]
  approval_policy?: Record<string, unknown> | null
  timeout_policy?: Record<string, unknown> | null
}

/** 发布编排定义的返回（publish_definition：发布即新版本） */
export interface OrchPublishResult {
  id: string
  version: number
}

/** 发布编排定义入参；retry/timeout/budget 等策略字段原样透传给后端 */
export interface OrchPublishBody {
  name: string
  description?: string | null
  nodes: unknown[]
  edges: unknown[]
  [key: string]: unknown
}

/** 运行列表项（list_runs 出参） */
export interface OrchRun {
  id: string
  definition_id: string
  definition_version: number
  status: string
  requested_by: string
  error_code: string | null
  createdAt: string | null
}

/** run 的一个 step（get_run 出参，snake_case） */
export interface OrchStep {
  id: string
  node_id: string
  kind: string
  name: string
  status: string
  depends_on: string[] | null
  session_id: string | null
  attempt_count: number
  output: unknown
  error_message: string | null
  started_at: string | null
  finished_at: string | null
}

/** run 详情（get_run 出参：steps 内嵌） */
export interface OrchRunDetail {
  id: string
  definition_id: string
  definition_version: number
  status: string
  run_input_snapshot: Record<string, unknown> | null
  requested_by: string
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  steps: OrchStep[]
}

/** 列出编排定义（每个 id 的最新版本） */
export function listDefinitions() {
  return get<{ definitions: OrchDefinition[] }>(`${BASE}/definitions`)
}

/** 取单个编排定义（缺省最新已发布版本，含 nodes/edges） */
export function getDefinition(defId: string) {
  return get<OrchDefinitionDetail>(`${BASE}/definitions/${encodeURIComponent(defId)}`)
}

/** 发布（或升版）编排定义；校验失败后端回 400 */
export function publishDefinition(body: OrchPublishBody) {
  return post<OrchPublishResult>(`${BASE}/definitions`, body)
}

/** 列出最近的编排运行 */
export function listRuns() {
  return get<{ runs: OrchRun[] }>(`${BASE}/runs`)
}

/** 取单个 run（含内嵌 steps） */
export function getRun(runId: string) {
  return get<OrchRunDetail>(`${BASE}/runs/${encodeURIComponent(runId)}`)
}
