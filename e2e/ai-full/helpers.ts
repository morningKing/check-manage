/**
 * 全量 AI 测试套件共享助手（docs/ai-testing 交付物，全量回归直接复用）。
 *
 * 约定：
 * - 所有测试数据带 `AITEST-` 前缀 + 时间戳，避免重跑撞名（同名分组/模板 409）。
 * - 登录走真实 /api/auth/login；API Key 用后即删，不残留密钥。
 * - 截图证据目录 e2e/screenshots/ai-full/。
 */
import fs from 'node:fs'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'

export const AUTH_FILE = 'e2e/.auth/admin.json'
export const SHOT_DIR = 'e2e/screenshots/ai-full'

let cachedToken: string | null = null

/** admin JWT（缓存复用；登录态失效自动重登一次） */
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

/** 批任务暂存上传（内部通道，UI 同款） */
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

/** 轮询直到谓词成立；返回最后一次值。intervalMs/poll 次数可配。 */
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
  await page.waitForLoadState('networkidle')
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
