import { get, post, put, patch, del } from '@/utils/request'

const BASE = '/ai/skills'

export interface GlobalSkill {
  id: string
  name: string
  description: string
  enabled: boolean
  uploadedBy: string | null
  fileSize: number
  createdAt: string | null
  updatedAt: string | null
}

export interface GlobalSkillFile {
  name: string
  path: string
  size: number
}

export function listGlobalSkills() {
  return get<{ skills: GlobalSkill[] }>(BASE)
}

export function getGlobalSkill(id: string) {
  return get<GlobalSkill>(`${BASE}/${id}`)
}

export function updateGlobalSkill(id: string, data: { description?: string; enabled?: boolean }) {
  return put<GlobalSkill>(`${BASE}/${id}`, data)
}

export function deleteGlobalSkill(id: string) {
  return del<{ deleted: boolean }>(`${BASE}/${id}`)
}

export function listSkillFiles(id: string) {
  return get<{ files: GlobalSkillFile[] }>(`${BASE}/${id}/files`)
}

export function readSkillFile(id: string, path: string) {
  return get<{ content: string; truncated: boolean; binary: boolean }>(
    `${BASE}/${id}/files/${encodeURIComponent(path)}`,
  )
}

export function uploadGlobalSkill(file: File, description: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('description', description)
  return post<GlobalSkill>(BASE, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// ── SkillOpt 任务拟合（task-fit）────────────────────────────────────────────

const ADMIN = '/ai/chat/admin'

export type FitStepStatus = 'hit' | 'miss' | 'skipped'

/** fit.steps 步骤（定义 frontmatter / 生成器草案 / 修订草案同构） */
export interface FitStep {
  id: string
  name: string
  expect: Array<{ tool: string; args_pattern?: string }>
}

/** 单步拟合结果：hit 带证据（tool/args 截断 160/occurredAt） */
export interface FitPerStep {
  id: string
  name: string
  status: FitStepStatus
  evidence: Array<{ tool: string; args: string; occurredAt: string | null }>
}

/** 偏差诊断（后端 snake_case：revised_steps） */
export interface SkillFitDiagnosis {
  cause: 'definition_stale' | 'step_redundant' | 'order_deviation'
    | 'model_noncompliance' | 'environment' | string
  suggestions: string[]
  revised_steps: FitStep[]
}

/** 拟合结果行（列表；id 为结果行主键，诊断端点按它定位） */
export interface SkillFitRow {
  id: string | null
  attemptId: string
  sessionId: string
  /** 执行该任务的 agent（ai_execution_attempts.effective_agent） */
  agent: string | null
  defKind: string
  defName: string
  defHash: string | null
  stepsTotal: number
  stepsHit: number
  score: number
  status: string
  computedAt: string | null
}

/** 拟合结果明细（额外带 perStep/diagnosis） */
export interface SkillFitDetail extends SkillFitRow {
  perStep: FitPerStep[]
  diagnosis: SkillFitDiagnosis | null
}

/** 定义版本（按 defKind/defName/contentHash 聚合拟合指标） */
export interface SkillDefVersion {
  id: string
  defKind: string
  defName: string
  contentHash: string | null
  versionLabel: string | null
  note: string | null
  firstSeenAt: string | null
  tasks: number
  avgScore: number | null
  fitRate: number | null
  /** 偏离分布：partial / diverged 结果数（spec §7 binding） */
  partialCount: number
  divergedCount: number
  /** 正文是否已归档（历史存量行为 false，内容动作置灰） */
  archived: boolean
  contentCapturedAt: string | null
}

/** 试算预览（preview_steps 口径，不落库） */
export interface FitPreview {
  per_step: FitPerStep[]
  steps_total: number
  steps_hit: number
  score: number
  status: string
}

/** 左栏定义清单 + 概览指标（/skill-fit/definition-summary） */
export interface SkillFitDefinitionSummary {
  defKind: string
  defName: string
  tasks: number
  fitRate: number | null
  avgScore: number | null
  partialCount: number
  divergedCount: number
  versions: number
  latestLabel: string | null
  latestHash: string | null
  lastActivity: string | null
}

/** 步骤级偏离聚合（/skill-def-patterns） */
export interface SkillDefPattern {
  defKind: string
  defName: string
  stepId: string
  missTasks: number
  versionsAffected: number
  versionHashes: string[]
  avgScore: number | null
  lastSeenAt: string | null
}

export function listSkillFits(params?: {
  sessionId?: string; defKind?: string; defName?: string; limit?: number
}) {
  return get<{ fits: SkillFitRow[] }>(`${ADMIN}/skill-fit`, params)
}

export function getSkillFitDetail(attemptId: string) {
  return get<{ fits: SkillFitDetail[]; subagentFits?: SkillFitDetail[] }>(
    `${ADMIN}/skill-fit/${encodeURIComponent(attemptId)}`)
}

export function recomputeSkillFit(attemptId: string) {
  return post<{ fits: SkillFitDetail[] }>(
    `${ADMIN}/skill-fit/${encodeURIComponent(attemptId)}/recompute`)
}

export function listSkillDefVersions(params?: { defKind?: string; defName?: string }) {
  return get<{ versions: SkillDefVersion[] }>(`${ADMIN}/skill-def-versions`, params)
}

export function updateSkillDefVersion(id: string, data: { versionLabel?: string | null; note?: string | null }) {
  return patch<{ ok: boolean }>(`${ADMIN}/skill-def-versions/${encodeURIComponent(id)}`, data)
}

export function generateSkillDefSteps(body: { kind?: string; path: string }
  | { kind?: string; name: string; contentHash?: string }) {
  return post<{ steps: FitStep[]; path: string; source?: string | null }>(
    `${ADMIN}/skill-def-steps/generate`, body)
}

/** 按定义名自动定位文件路径（生成器预填；404 = 无可用注入记录，回落手输） */
export function resolveSkillDefPath(body: { kind: string; name: string;
                                            contentHash?: string }) {
  return post<{ path: string; contentHash: string | null;
                source: 'manifest_hash_match' | 'manifest_latest' }>(
    `${ADMIN}/skill-def-steps/resolve`, body)
}

export function applySkillDefSteps(body: { path: string; steps: FitStep[]; versionLabel?: string }
  | { kind: string; name: string; contentHash?: string; steps: FitStep[]; versionLabel?: string }) {
  return post<{ path: string }>(`${ADMIN}/skill-def-steps/apply`, body)
}

export function previewSkillDefSteps(body: { steps: FitStep[]; attemptId: string }) {
  return post<{ preview: FitPreview }>(`${ADMIN}/skill-def-steps/preview`, body)
}

export function diagnoseSkillFit(resultId: string) {
  return post<{ diagnosis: SkillFitDiagnosis }>(
    `${ADMIN}/skill-fit/${encodeURIComponent(resultId)}/diagnose`)
}

export function getSkillFitDefinitionSummary() {
  return get<{ definitions: SkillFitDefinitionSummary[] }>(
    `${ADMIN}/skill-fit/definition-summary`)
}

export function listSkillDefPatterns(params?: {
  defKind?: string; defName?: string; limit?: number
}) {
  return get<{ patterns: SkillDefPattern[] }>(`${ADMIN}/skill-def-patterns`, params)
}

/** 版本归档正文（spec §5） */
export interface SkillDefVersionContent {
  id: string
  defKind: string
  defName: string
  contentHash: string | null
  versionLabel: string | null
  contentCapturedAt: string | null
  content: string
}

/** compare 端点的单端元信息 */
export interface SkillDefVersionDiffMeta {
  id: string
  defName: string
  contentHash: string | null
  versionLabel: string | null
  contentCapturedAt: string | null
}

export function getSkillDefVersionContent(id: string) {
  return get<SkillDefVersionContent>(
    `${ADMIN}/skill-def-versions/${encodeURIComponent(id)}/content`)
}

export function compareSkillDefVersions(params: { fromId: string; toId: string }) {
  return get<{ from: SkillDefVersionDiffMeta; to: SkillDefVersionDiffMeta; diff: string }>(
    `${ADMIN}/skill-def-versions/compare`, params)
}

export function rollbackSkillDefVersion(id: string) {
  return post<{ ok: boolean; path: string; contentHash: string }>(
    `${ADMIN}/skill-def-versions/${encodeURIComponent(id)}/rollback`)
}

// ── SkillOpt 性能分析（perf）────────────────────────────────────────────────

export interface PerfDefSummary {
  defKind: string; defName: string; tasks: number
  p50Ms: number; p95Ms: number; avgModelRatio: number | null
  lastActivity: string | null
}

export interface PerfTaskEntry {
  attemptId: string; sessionId: string; sourceType: string; status: string
  startedAt: string | null; finishedAt: string | null
  wallMs: number; modelMs: number; modelRatio: number
  subagentWaitMs: number; idleMs: number
  turns: number; tokensIn: number; tokensOut: number; subtaskCount: number
  completeness: { turnsWithoutDuration: number; runningSubtasks: number }
  defKind?: string; defName?: string
}

export interface PerfTurn {
  messageId: string; createdAt: string | null
  durationMs: number; tokensIn: number; tokensOut: number; preview: string
}

export interface PerfSubtask {
  subtaskId: string; agent: string | null; description: string | null
  status: string; startedAt: string | null; finishedAt: string | null
  wallMs: number
}

export interface PerfAttemptDetail {
  attempt: PerfTaskEntry & {
    requestedModel?: string | null; effectiveModel?: string | null
    batchReuseAgents?: string[] | null
  }
  coverage: { wallMs: number; modelMs: number; subagentWaitMs: number; idleMs: number }
  turns: PerfTurn[]
  subtasks: PerfSubtask[]
  tools: { errorCount: number; repeats: { tool: string; argsPreview: string; count: number }[] }
  completeness: { turnsWithoutDuration: number; runningSubtasks: number }
}

export interface Diagnosis {
  ruleId: string; severity: 'info' | 'warn'; text: string
  anchor: { type: 'turn' | 'subtask' | 'segment' | 'def'; ref: string }
}

export function perfOverview() {
  return get<{ defs: PerfDefSummary[] }>(`${ADMIN}/perf/overview`)
}
export function perfDefTasks(kind: string, name: string, limit = 50) {
  return get<{ tasks: PerfTaskEntry[] }>(
    `${ADMIN}/perf/defs/${encodeURIComponent(kind)}/${encodeURIComponent(name)}/tasks?limit=${limit}`)
}
export function perfAttempt(id: string) {
  return get<PerfAttemptDetail>(`${ADMIN}/perf/attempts/${encodeURIComponent(id)}`)
}
export function perfAttemptDiagnosis(id: string) {
  return get<{ diagnoses: Diagnosis[] }>(
    `${ADMIN}/perf/attempts/${encodeURIComponent(id)}/diagnosis`)
}
export function perfSlowTasks(limit = 10) {
  return get<{ tasks: PerfTaskEntry[] }>(`${ADMIN}/perf/slow-tasks?limit=${limit}`)
}
