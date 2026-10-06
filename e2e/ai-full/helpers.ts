/**
 * 全量 AI 测试套件共享助手（docs/ai-testing 交付物，全量回归直接复用）。
 *
 * 约定：
 * - 所有测试数据带 `AITEST-` 前缀 + 时间戳，避免重跑撞名（同名分组/模板 409）。
 * - 登录走真实 /api/auth/login；API Key 用后即删，不残留密钥。
 * - 截图证据目录 e2e/screenshots/ai-full/。
 */
import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { APIRequestContext, Page } from '@playwright/test'
import { API } from './batch/batch-helpers'

export const AUTH_FILE = 'e2e/.auth/admin.json'
export const SHOT_DIR = 'e2e/screenshots/ai-full'

let cachedToken: string | null = null

/**
 * admin JWT（缓存复用；登录态失效自动重登一次）
 *
 * @deprecated 批任务域用例请改用 e2e/ai-full/batch/batch-helpers.ts 的
 * `adminToken`（直连 3002 权威版）。本函数保留给非批用例；当最后一批
 * 消费方迁移后删除。
 */
export async function adminToken(request: APIRequestContext): Promise<string> {
  if (cachedToken) return cachedToken
  const res = await request.post('/api/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  if (res.status() !== 200) throw new Error(`login failed: ${res.status()}`)
  cachedToken = (await res.json()).token
  return cachedToken!
}

/** 内部（JWT）API 调用 */
export async function api(request: APIRequestContext, method: string,
                         path: string, data?: unknown,
                         extraHeaders: Record<string, string> = {}):
                         Promise<{ status: number; json: any; headers: any }> {
  const token = await adminToken(request)
  const res = await request.fetch(`/api${path}`, {
    method,
    data: data === undefined ? undefined : JSON.stringify(data),
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...extraHeaders,
    },
  })
  let json: any = null
  try { json = await res.json() } catch { /* 204/二进制 */ }
  return { status: res.status(), json, headers: res.headers() }
}

/** 创建一把绑定 admin 的 API Key（对外 API 测试入口），返回明文密钥 */
export async function createApiKey(request: APIRequestContext,
                                   tag: string): Promise<string> {
  const r = await api(request, 'POST', '/apiKeys', { name: `AITEST-${tag}` })
  if (r.status >= 300 || !r.json?.key) {
    throw new Error(`create api key failed: ${r.status} ${JSON.stringify(r.json)}`)
  }
  return r.json.key as string
}

export async function deleteApiKey(request: APIRequestContext,
                                   tag: string): Promise<void> {
  const list = await api(request, 'GET', '/apiKeys')
  for (const k of list.json || []) {
    if (k.name === `AITEST-${tag}`) {
      await api(request, 'DELETE', `/apiKeys/${k.id}`)
    }
  }
}

/** 对外 API（X-API-Key）调用 */
export async function openApi(request: APIRequestContext, key: string,
                              method: string, path: string, data?: unknown):
                              Promise<{ status: number; json: any }> {
  const res = await request.fetch(`/api${path}`, {
    method,
    data: data === undefined ? undefined : JSON.stringify(data),
    headers: {
      'X-API-Key': key,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
  })
  let json: any = null
  try { json = await res.json() } catch { /* no body */ }
  return { status: res.status(), json }
}

/**
 * 批任务暂存上传（内部通道，UI 同款）
 *
 * @deprecated 批任务域用例请改用 e2e/ai-full/batch/batch-helpers.ts 的
 * `uploadStaging`（直连 3002 权威版）。本函数保留给非批用例（ai-openapi、
 * batch/openapi 迁移过渡期）；当最后一批消费方迁移后删除。
 */
export async function stagingUpload(request: APIRequestContext,
                                    uploadSessionId: string,
                                    files: { name: string; body: string }[]):
                                    Promise<{ name: string; path: string }[]> {
  const out: { name: string; path: string }[] = []
  for (const f of files) {
    const token = await adminToken(request)
    const res = await request.fetch('/api/ai/chat/batches/staging/upload', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: f.name, mimeType: 'text/plain', buffer: Buffer.from(f.body, 'utf-8') },
        upload_session_id: uploadSessionId,
      },
    })
    if (res.status() !== 201) {
      throw new Error(`staging upload failed: ${res.status()} ${await res.text()}`)
    }
    out.push(await res.json())
  }
  return out
}

/**
 * 轮询直到谓词成立；返回最后一次值。intervalMs/poll 次数可配。
 *
 * @deprecated 批任务域用例请改用 e2e/ai-full/batch/batch-helpers.ts 的
 * `waitFor`（直连 3002 权威版，deadline+label 形参）。本函数保留给非批
 * 用例；当最后一批消费方迁移后删除。
 */
export async function waitFor<T>(fn: () => Promise<T | null>,
                                 opts: { timeoutMs?: number; intervalMs?: number } = {}):
                                 Promise<T> {
  const timeout = opts.timeoutMs ?? 300_000
  const interval = opts.intervalMs ?? 3000
  const deadline = Date.now() + timeout
  let last: T | null = null
  while (Date.now() < deadline) {
    last = await fn()
    if (last !== null) return last
    await new Promise(r => setTimeout(r, interval))
  }
  throw new Error(`waitFor timeout after ${timeout}ms; last=${JSON.stringify(last)}`)
}

/**
 * @deprecated 批任务域用例请改用 e2e/ai-full/batch/batch-helpers.ts 的
 * `BATCH_TERMINAL`（直连 3002 权威版）。本常量保留给非批用例；当最后一批
 * 消费方迁移后删除。
 */
export const BATCH_TERMINAL = ['completed', 'partial', 'failed']
export const SESSION_TERMINAL = ['completed', 'failed', 'cancelled']

/** 打开 AI 会话页并定位到指定会话（先注入共享登录态）。
 * 注意：AI 会话页有 SSE 长连接，networkidle 永不触发 —— 调用方请断言具体
 * 消息/气泡元素，不要等待网络空闲。 */
export async function openChatSession(page: Page, sessionId: string): Promise<void> {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await page.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
  await page.goto(`/ai-chat?session=${sessionId}`)
  await page.waitForSelector('.ai-chat__main, .ai-chat', { timeout: 20_000 })
}

export async function screenshot(page: Page, name: string): Promise<string> {
  fs.mkdirSync(SHOT_DIR, { recursive: true })
  const file = path.join(SHOT_DIR, `${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  return file
}

/** 带登录态打开任意路径（admin 页面等） */
export async function gotoWithAuth(page: Page, path: string): Promise<void> {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await page.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
  await page.goto(path)
  // /ai-chat 有常驻 SSE 连接,networkidle 永不收敛(见 memory:e2e flakiness)。
  // 只做 8s 尽力等待:普通页面足以稳定,SSE 页面不再把整个用例拖到超时。
  try {
    await page.waitForLoadState('networkidle', { timeout: 8_000 })
  } catch { /* SSE 页面视为已就绪 */ }
}

export function tag(prefix: string): string {
  return `AITEST-${prefix}-${Date.now()}`
}

/** 从会话列表查指定会话（无单独详情端点；列表含 status/group 等字段） */
export async function findSession(request: APIRequestContext, sid: string):
        Promise<Record<string, any> | null> {
  const r = await api(request, 'GET', '/ai/chat/sessions')
  const sessions = r.json?.sessions || []
  return sessions.find((s: any) => s.id === sid) || null
}

// package.json 带 "type": "module"，本仓库 e2e 规约以 import.meta.url 求模块目录
// （同 batch/toolbox.ts、e2e/ai-chat-stop-resume.spec.ts），不直接用 __dirname。
const DIRNAME = path.dirname(fileURLToPath(import.meta.url))

/** 重启后端（Windows 环境）：kill 3002 → 带 env 重启 → 探活。不传 env 即恢复默认。
 * 原 batch/toolbox.ts 实现（2026-10-06 共享化迁入，函数体逐字保留；仅 server 目录
 * 相对层级随本文件位置少一级 `..`），toolbox 侧 re-export 转发、batch 用例不受影响。 */
export async function restartBackend(env: Record<string, string> = {}): Promise<void> {
  // 找到监听 3002 的 PID 并 kill（netstat 行形如 `TCP  127.0.0.1:3002 ... LISTENING  1234`）。
  // /:3002\s/ 锚定端口列，避免 includes(':3002') 子串误匹配 :30021 等监听行；
  // netstat 失败/无监听（首启）可容忍，但 taskkill 失败必须显式抛出——吞掉会让
  // 旧进程继续占用 3002，重启退化为数分钟后的看门狗超时，难以定位。
  let pid: string | undefined
  try {
    const out = execFileSync('netstat', ['-ano'], { encoding: 'utf-8', shell: true })
    pid = out.split('\n').map(l => l.trim())
      .filter(l => /:3002\s/.test(l) && l.includes('LISTENING'))
      .pop()?.split(/\s+/).pop()
  } catch { /* netstat 失败视同无监听（首启容忍） */ }
  if (pid) {
    try {
      execFileSync('taskkill', ['/F', '/PID', pid], { stdio: 'ignore' })
    } catch (e) {
      throw new Error(`restartBackend: taskkill PID=${pid} 失败，3002 仍被旧进程占用：${e}`)
    }
  }
  const child = spawn('python', ['app.py'], {
    cwd: path.join(DIRNAME, '..', '..', 'server'),
    env: { ...process.env, ...env },
    // detached:true（Windows=新进程组+独立控制台）：后端必须活过本 runner 退出
    // ——detached:false 时子进程随父控制台关闭被杀（Task 12 实测 run 结束即失联）
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
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
