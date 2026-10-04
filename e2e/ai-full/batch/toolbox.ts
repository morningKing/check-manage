/**
 * 批任务确定性构造工具箱。
 * 原则（spec §3）：系统栈真实（后端/OpenCode/DB 真进程），但不烧 LLM——
 * fail-fast / sleep 长任务 / fs 直写 / DB 种子构造目标状态。
 */
import { execFileSync, spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  adminToken, authHeaders, cleanupBatch, createBatch, getDetail,
  uploadStaging, type CreateBatchBody,
  API,
} from './batch-helpers'

export { API }

// package.json 带 "type": "module"，本仓库 e2e 规约以 import.meta.url 求模块目录
// （同 e2e/ai-chat-stop-resume.spec.ts），不直接用 __dirname。
const DIRNAME = path.dirname(fileURLToPath(import.meta.url))

export function tag(prefix: string): string {
  return `${prefix}-AITEST-${Date.now()}-${crypto.randomBytes(2).toString('hex')}`
}

let _cachedToken: string | null = null
export async function adminTokenCached(): Promise<string> {
  if (!_cachedToken) _cachedToken = await adminToken()
  return _cachedToken
}

const DB_EXEC = path.join(DIRNAME, 'db_exec.py')

/** 经 db_exec.py 执行 SQL；SELECT 返回行，其余返回 []。 */
export function dbSeed(sql: string): any[][] {
  const out = execFileSync('python', [DB_EXEC], { input: sql, encoding: 'utf-8' })
  return JSON.parse(out.trim() || '[]')
}

/** 未知 agent 建批 → worker 认领时 _check_agent fail-fast，子任务秒级 failed（不烧 LLM）。 */
export async function failFastBatch(
  tk: string, o?: { files?: number; prompt?: string; name?: string },
): Promise<string> {
  const uploadSession = tag('upl')
  const files = []
  for (let i = 0; i < (o?.files ?? 1); i++) {
    files.push(await uploadStaging(tk, `in-${i}.txt`, `e2e 输入 ${i}\n`, uploadSession))
  }
  const body: CreateBatchBody = {
    name: o?.name ?? tag('failfast'),
    prompt: o?.prompt ?? '对每个输入文件输出一行摘要。',
    agent: 'e2e-no-such-agent',   // 不存在的 primary agent → 认领即 fail-fast
    files,
  }
  const batch = await createBatch(tk, body)
  return batch.batch?.id ?? batch.id
}

/** bash sleep 长任务：模型跑一次 bash sleep 后输出 done，构造稳定 running 窗口。
 * 用默认 agent（不传 agent 字段）——本部署 OpenCode 的 'general' 是 subagent，
 * 指定它会被 _check_agent 以「subagent 不能作为主 Agent」拒绝（子任务秒级
 * failed，永远到不了 running）；同款构造见 ai-batch-control.spec.ts 的长任务。 */
export async function sleepBatch(
  tk: string, o: { children: number; sleepSec?: number; name?: string },
): Promise<string> {
  const uploadSession = tag('upl')
  const files = []
  for (let i = 0; i < o.children; i++) {
    files.push(await uploadStaging(tk, `sleep-${i}.txt`, `sleep ${i}\n`, uploadSession))
  }
  const sec = o.sleepSec ?? 90
  const batch = await createBatch(tk, {
    name: o.name ?? tag('sleep'),
    prompt: `用 bash 工具执行 \`sleep ${sec}\`，结束后输出一行 done 即可，不要做别的。`,
    files,
  } as CreateBatchBody)
  return batch.batch?.id ?? batch.id
}

export function childWorkspace(detail: any, idx = 0): string {
  const ws = detail.sessions[idx]?.workspace_path
  if (!ws) throw new Error(`sessions[${idx}] 无 workspace_path: ${JSON.stringify(detail.sessions?.[idx])}`)
  return ws
}

export function writeWorkspaceFile(ws: string, rel: string, content: string): void {
  const p = path.join(ws, rel)
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, content, 'utf-8')
}

export function readWorkspaceFile(ws: string, rel: string): string {
  return fs.readFileSync(path.join(ws, rel), 'utf-8')
}

/** 管理员建一个普通用户（role=guest）并登录，cleanup 删除该用户。 */
export async function secondUser(prefix = 'e2eu'): Promise<{
  id: string; username: string; password: string; token: string; cleanup(): Promise<void>
}> {
  const tk = await adminTokenCached()
  const username = tag(prefix).toLowerCase().replace(/[^a-z0-9-]/g, '')
  const password = 'e2e-pass-123'
  const r = await fetch(`${API}/users`, {
    method: 'POST',
    headers: authHeaders(tk),
    body: JSON.stringify({ username, password, displayName: `E2E ${prefix}`, role: 'guest' }),
  })
  if (r.status !== 201) throw new Error(`create user failed: ${r.status} ${await r.text()}`)
  const user = await r.json()
  const lr = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!lr.ok) throw new Error(`second user login failed: ${lr.status}`)
  const token = (await lr.json()).token
  return {
    id: user.id, username, password, token,
    async cleanup() {
      await fetch(`${API}/users/${user.id}`, { method: 'DELETE', headers: authHeaders(tk) })
    },
  }
}

export interface SeedOpts {
  ocId?: string | null
  lease?: 'past' | 'future' | null
  retryCount?: number
  effectUnknown?: boolean
  checkpoint?: boolean
  messages?: number
  agent?: string                  // 默认 'e2e-no-such-agent'：重排后被认领即 fail-fast，绝不真跑
}

/** 直插一对批+running 子会话行（绕开 API，规避 worker 认领竞态——
 * claim 只认 pending 行，见 batch_engine.py:1116）。返回 ids 供断言/清理。
 * 清理约定：种子行**不能**走 API DELETE ?stop=1——cancel_batch 只置
 * cancel_requested（batch_repo.py:408），worker 从未认领过该子任务，
 * drain 10s 超时 → 409 保留。必须直接
 * dbSeed(`DELETE FROM ai_chat_batches WHERE id='${bid}'`)：
 * sessions 及 effects/checkpoints/messages 经 FK ON DELETE CASCADE 级联删除。 */
export async function seedRunningChild(o: SeedOpts): Promise<{ bid: string; sid: string }> {
  const adminId = dbSeed(`SELECT id FROM users WHERE username='admin'`)[0][0]
  const bid = `b-${crypto.randomUUID()}`
  const sid = `ses-${crypto.randomUUID()}`
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-seed-ws-'))
  const lease = o.lease === 'future'
    ? "NOW() + interval '300 seconds'"
    : o.lease === 'past' ? "NOW() - interval '300 seconds'" : 'NULL'
  dbSeed(`
    INSERT INTO ai_chat_batches (id, user_id, name, prompt, agent, status, total)
    VALUES ('${bid}', '${adminId}', '${tag('seed')}', 'seed prompt',
            '${o.agent ?? 'e2e-no-such-agent'}', 'running', 1);
    INSERT INTO ai_chat_sessions
      (id, user_id, title, workspace_path, session_token, token_expires_at, status,
       batch_id, batch_seq, batch_input_file, lease_until, retry_count, created_at)
    VALUES ('${sid}', '${adminId}', 'seed child', '${ws}',
            '${crypto.randomUUID().replace(/-/g, '')}', NOW() + interval '1 hour', 'running',
            '${bid}', 1, 'seed-in.txt', ${lease}, ${o.retryCount ?? 0}, NOW());
    ${o.ocId ? `UPDATE ai_chat_sessions SET opencode_session_id='${o.ocId}' WHERE id='${sid}';` : ''}
    ${o.effectUnknown ? `INSERT INTO ai_execution_effects (id, session_id, batch_id, effect_type, idempotency_key, status)
      VALUES ('fx-${crypto.randomUUID()}', '${sid}', '${bid}', 'file_import', 'seed-${crypto.randomUUID()}', 'unknown');` : ''}
    ${o.checkpoint ? `INSERT INTO ai_execution_checkpoints (id, session_id, execution_generation, checkpoint_type)
      VALUES ('ck-${crypto.randomUUID()}', '${sid}', 1, 'progress');` : ''}
    ${Array.from({ length: o.messages ?? 0 }, (_, i) =>
      `INSERT INTO ai_chat_messages (id, session_id, role, content)
       VALUES ('m-${crypto.randomUUID()}', '${sid}', 'user',
               '[{"type":"text","text":"seed msg ${i}"}]');`).join('\n')}
  `)
  return { bid, sid }
}

/** 重启后端（Windows 环境）：kill 3002 → 带 env 重启 → 探活。不传 env 即恢复默认。 */
export async function restartBackend(env: Record<string, string> = {}): Promise<void> {
  // 找到监听 3002 的 PID 并 kill（netstat 行形如 `TCP  127.0.0.1:3002 ... LISTENING  1234`）
  try {
    const out = execFileSync('netstat', ['-ano'], { encoding: 'utf-8', shell: true })
    const pid = out.split('\n').map(l => l.trim())
      .filter(l => l.includes(':3002') && l.includes('LISTENING'))
      .pop()?.split(/\s+/).pop()
    if (pid) execFileSync('taskkill', ['/F', '/PID', pid], { stdio: 'ignore' })
  } catch { /* 3002 无进程时忽略 */ }
  const child = spawn('python', ['app.py'], {
    cwd: path.join(DIRNAME, '..', '..', '..', 'server'),
    env: { ...process.env, ...env },
    detached: false, stdio: 'ignore',
  })
  child.unref()
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      if (r.status < 500) return   // 400/401/422 都证明 Flask 已起
    } catch { /* 未起，重试 */ }
    await new Promise(rr => setTimeout(rr, 1000))
  }
  throw new Error('backend restart: 60s 内未探活')
}
