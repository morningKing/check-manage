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
}

/** 试算预览（preview_steps 口径，不落库） */
export interface FitPreview {
  per_step: FitPerStep[]
  steps_total: number
  steps_hit: number
  score: number
  status: string
}

export function listSkillFits(params?: { sessionId?: string; limit?: number }) {
  return get<{ fits: SkillFitRow[] }>(`${ADMIN}/skill-fit`, params)
}

export function getSkillFitDetail(attemptId: string) {
  return get<{ fits: SkillFitDetail[] }>(`${ADMIN}/skill-fit/${encodeURIComponent(attemptId)}`)
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

export function generateSkillDefSteps(body: { kind?: string; path: string }) {
  return post<{ steps: FitStep[] }>(`${ADMIN}/skill-def-steps/generate`, body)
}

export function applySkillDefSteps(body: { path: string; steps: FitStep[] }) {
  return post<{ path: string }>(`${ADMIN}/skill-def-steps/apply`, body)
}

export function previewSkillDefSteps(body: { steps: FitStep[]; attemptId: string }) {
  return post<{ preview: FitPreview }>(`${ADMIN}/skill-def-steps/preview`, body)
}

export function diagnoseSkillFit(resultId: string) {
  return post<{ diagnosis: SkillFitDiagnosis }>(
    `${ADMIN}/skill-fit/${encodeURIComponent(resultId)}/diagnose`)
}
