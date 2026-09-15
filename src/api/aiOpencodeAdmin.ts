import { get, post, put, del } from '@/utils/request'

const BASE = '/ai/opencode'

/** OpenCode 全局 serve 健康状态 */
export interface ServeHealth {
  healthy: boolean
  version: string | null
}

/** 重启会中断的运行中负载 */
export interface ActiveWorkload {
  batchChildren: number
  interactiveSessions: number
  activeBatches: number
}

export interface OpencodeOverview {
  globalDir: string
  skillsDir: string
  agentsDir: string
  skillsDirExists: boolean
  agentsDirExists: boolean
  serve: ServeHealth
  pendingChanges: number
  restartPolicy: 'manual' | 'auto'
  serveCmd: string
  activeWorkload: ActiveWorkload
}

/** runtime: loaded=已生效 pending=待重启 disabled=已禁用 serveOffline=服务不可达 */
export type RuntimeStatus = 'loaded' | 'pending' | 'disabled' | 'serveOffline'

export interface GlobalSkillItem {
  name: string
  description: string
  source: 'global' | 'builtin' | 'external'
  runtime: RuntimeStatus
  size: number | null
  mtime: number | null
  location?: string
}

export interface GlobalAgentItem {
  name: string
  fileName: string | null
  description: string
  mode: string
  model: string
  source: 'file' | 'builtin' | 'plugin'
  native: boolean
  runtime: RuntimeStatus
  size: number | null
  mtime: number | null
}

export interface SkillDetail {
  name: string
  description: string
  body: string
  content: string
  size: number
  mtime: number
}

export interface AgentDetail {
  name: string
  meta: Record<string, unknown>
  body: string
  content: string
  size: number
  mtime: number
}

export interface EffectStatus {
  skills: { name: string; runtime: RuntimeStatus }[]
  agents: { name: string; runtime: RuntimeStatus }[]
  pendingCount: number
}

export function getOpencodeOverview() {
  return get<OpencodeOverview>(`${BASE}/overview`)
}

export function listGlobalOpencodeSkills() {
  return get<{ items: GlobalSkillItem[]; pendingCount: number }>(`${BASE}/skills`)
}

export function getGlobalOpencodeSkill(name: string) {
  return get<SkillDetail>(`${BASE}/skills/${encodeURIComponent(name)}`)
}

export function createGlobalOpencodeSkill(data: {
  name: string
  description: string
  body?: string
}) {
  return post<{ name: string; changed: boolean }>(`${BASE}/skills`, data)
}

export function updateGlobalOpencodeSkill(
  name: string,
  data: { description?: string; body?: string; content?: string },
) {
  return put<{ name: string; changed: boolean }>(
    `${BASE}/skills/${encodeURIComponent(name)}`, data)
}

export function deleteGlobalOpencodeSkill(name: string) {
  return del<void>(`${BASE}/skills/${encodeURIComponent(name)}`)
}

export function uploadGlobalOpencodeSkillZip(file: File) {
  const form = new FormData()
  form.append('file', file)
  return post<{ name: string; changed: boolean }>(`${BASE}/skills`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 把平台「AI 技能管理」里的技能发布到 OpenCode 全局目录 */
export function publishPlatformSkill(skillId: string, overwrite = false) {
  return post<{ name: string; changed: boolean }>(`${BASE}/skills/publish`, {
    skillId,
    overwrite,
  })
}

// ── skill 附属文件（脚本/模板等，SKILL.md 之外的文件）──

export interface SkillFileInfo {
  name: string
  path: string
  size: number
  mtime: number
}

export interface SkillFileContent {
  content: string
  truncated: boolean
  binary: boolean
  size: number
  mtime: number
}

export function listSkillFiles(name: string) {
  return get<{ files: SkillFileInfo[] }>(
    `${BASE}/skills/${encodeURIComponent(name)}/files`)
}

export function readSkillFile(name: string, path: string) {
  return get<SkillFileContent>(
    `${BASE}/skills/${encodeURIComponent(name)}/files/${path
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`)
}

export function writeSkillFile(name: string, path: string, content: string) {
  return put<{ path: string; changed: boolean }>(
    `${BASE}/skills/${encodeURIComponent(name)}/files/${path
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`, { content })
}

export function deleteSkillFile(name: string, path: string) {
  return del<void>(
    `${BASE}/skills/${encodeURIComponent(name)}/files/${path
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`)
}

export function listGlobalOpencodeAgents() {
  return get<{ items: GlobalAgentItem[]; pendingCount: number }>(`${BASE}/agents`)
}

export function getGlobalOpencodeAgent(name: string) {
  return get<AgentDetail>(`${BASE}/agents/${encodeURIComponent(name)}`)
}

export function createGlobalOpencodeAgent(data: {
  name: string
  description: string
  mode?: string
  model?: string
  temperature?: number | null
  topP?: number | null
  body?: string
}) {
  return post<{ name: string; changed: boolean }>(`${BASE}/agents`, data)
}

export function updateGlobalOpencodeAgent(
  name: string,
  data: {
    description?: string
    mode?: string
    model?: string
    temperature?: number | null
    topP?: number | null
    body?: string
    content?: string
  },
) {
  return put<{ name: string; changed: boolean }>(
    `${BASE}/agents/${encodeURIComponent(name)}`, data)
}

export function deleteGlobalOpencodeAgent(name: string) {
  return del<void>(`${BASE}/agents/${encodeURIComponent(name)}`)
}

export function setGlobalOpencodeAgentDisabled(name: string, disabled: boolean) {
  return post<{ name: string; disable: boolean }>(
    `${BASE}/agents/${encodeURIComponent(name)}/${disabled ? 'disable' : 'enable'}`,
  )
}

export function getEffectStatus() {
  return get<EffectStatus>(`${BASE}/effect-status`)
}

/** 重启 opencode serve。有运行中会话且非 force 时后端返回 409 + activeWorkload */
export function restartOpencodeServe(force = false) {
  return post<{ ok: boolean; version: string | null; killedPids: number[] }>(
    `${BASE}/restart`, { force })
}

/** 平台「AI 技能管理」技能列表（发布下拉的数据源），复用既有接口 */
export { listGlobalSkills as listPlatformSkills } from '@/api/aiSkills'
