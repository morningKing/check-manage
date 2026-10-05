/**
 * ai-full 批任务 spec 共享骨架（2026-09-30 从四个 spec 的复制粘贴中收敛）。
 *
 * 断言直连后端 3002（绕开 vite 代理——e2e 运行期经代理的 GET 偶发返回
 * 空对象 {}，根因在代理层；批任务行为验证的目标是后端本身）。
 * 与 ../helpers.ts（UI 向、走 /api 代理、APIRequestContext）刻意分开：
 * 本模块纯 fetch、无 playwright 依赖，供确定性批任务行为断言使用。
 * 所有轮询显式 deadline + 间隔，超时抛带上下文的错误。
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const API = 'http://127.0.0.1:3002'
export const BATCH_TERMINAL = ['completed', 'partial', 'failed']

export async function adminToken(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  return (await r.json()).token
}

export function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

/** staging 上传一个内存文件，返回可直接放进 createBatch.files 的对象。 */
export async function uploadStaging(
  token: string, name: string, content: string, uploadSessionId: string,
): Promise<any> {
  const form = new FormData()
  form.append('file', new Blob([Buffer.from(content)], { type: 'text/plain' }), name)
  form.append('upload_session_id', uploadSessionId)
  const up = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form,
  })
  if (up.status !== 201) throw new Error(`staging upload failed: ${up.status} ${await up.text()}`)
  return await up.json()
}

export interface CreateBatchBody {
  name: string
  prompt: string
  agent?: string
  model?: string
  provision_repo?: string
  subagent_reuse?: string[]
  gate_retry?: boolean
  action_checks?: any[]
  files: any[]
}

export async function createBatch(token: string, body: CreateBatchBody): Promise<any> {
  const r = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(token), body: JSON.stringify(body),
  })
  if (r.status !== 201) throw new Error(`create batch failed: ${r.status} ${await r.text()}`)
  return await r.json()
}

export async function getDetail(token: string, bid: string): Promise<any> {
  return await (await fetch(`${API}/ai/chat/batches/${bid}`, {
    headers: authHeaders(token),
  })).json()
}

/** 轮询直到批次收敛终态，返回终态 detail；超时抛带末次状态的错误。 */
export async function waitBatchTerminal(
  token: string, bid: string, timeoutMs = 420_000, intervalMs = 3000,
): Promise<any> {
  const deadline = Date.now() + timeoutMs
  let last: any = null
  while (Date.now() < deadline) {
    last = await getDetail(token, bid)
    if (BATCH_TERMINAL.includes(last.batch?.status)) return last
    await new Promise(rr => setTimeout(rr, intervalMs))
  }
  throw new Error(`批次 ${bid} 未在 ${timeoutMs}ms 内收敛终态；末次状态 ` +
    `${JSON.stringify(last?.batch?.status)} / ` +
    `${JSON.stringify((last?.sessions || []).map((s: any) => s.status))}`)
}

/** 轮询直到谓词成立（返回非 null/undefined/false 即命中），返回命中值。 */
export async function waitFor<T>(
  fn: () => Promise<T | null>, timeoutMs: number, label: string, intervalMs = 2000,
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const v = await fn()
    if (v !== null && v !== undefined && v !== false) return v
    await new Promise(rr => setTimeout(rr, intervalMs))
  }
  throw new Error(`等待超时（${timeoutMs}ms）: ${label}`)
}

/** 终态后清理测试批（stop=1 允许非终态兜底删除，级联子会话）。 */
export async function cleanupBatch(token: string, bid: string): Promise<void> {
  await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
  })
}

export interface AgentDef { mode: 'primary' | 'subagent'; body: string }

/** 本地 git 预置仓库：自定义 agent 定义（OpenCode agent md）与项目技能
 * （skill/<name>/SKILL.md，frontmatter name/description），返回仓库路径
 * （调用方用完 fs.rmSync(recursive, force) 清理）。
 *
 * skills 形参（2026-10-04 为 ui-journeys「技能注入」用例加）：键为技能名，
 * 值为 SKILL.md 正文。服务端把预置仓库整仓 clone 成子会话工作区的
 * .opencode/（batch_engine._provision_workspace：「The repo root is treated
 * as the .opencode config dir (it should contain agent/, skill/, …)」），
 * 因此仓库里 skill/<name>/SKILL.md 即 OpenCode 的**项目级技能** ——
 * 与全局技能（AI 全局技能管理页、中心存储 global-skills/）是两条注入路径，
 * 项目技能注入成功是静默的（无「已注入全局技能」通知）。 */
export function makeProvisionRepo(
  agents: Record<string, AgentDef>,
  skills: Record<string, string> = {},
): string {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-prov-'))
  fs.mkdirSync(path.join(repo, 'agent'), { recursive: true })
  for (const [name, def] of Object.entries(agents)) {
    fs.writeFileSync(path.join(repo, 'agent', `${name}.md`),
      `---\ndescription: E2E agent ${name}\nmode: ${def.mode}\n---\n${def.body}\n`, 'utf-8')
  }
  for (const [name, body] of Object.entries(skills)) {
    fs.mkdirSync(path.join(repo, 'skill', name), { recursive: true })
    fs.writeFileSync(path.join(repo, 'skill', name, 'SKILL.md'),
      `---\nname: ${name}\ndescription: E2E skill ${name}\n---\n${body}\n`, 'utf-8')
  }
  const g = (args: string[]) =>
    execFileSync('git', ['-c', 'core.autocrlf=false', ...args], { cwd: repo })
  g(['init', '-q'])
  g(['add', '.'])
  g(['-c', 'user.name=e2e', '-c', 'user.email=e2e@local', 'commit', '-qm', 'agents'])
  return repo
}

/** 统计某状态子任务数：countByStatus(detail)['running']。 */
export function countByStatus(detail: any): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const s of detail.sessions) counts[s.status] = (counts[s.status] || 0) + 1
  return counts
}

/** 子会话消息条数（内部会话消息端点）。 */
export async function messageCount(token: string, sid: string): Promise<number> {
  const r = await (await fetch(`${API}/ai/chat/sessions/${sid}/messages`, {
    headers: authHeaders(token),
  })).json()
  return (r.messages ?? []).length
}
