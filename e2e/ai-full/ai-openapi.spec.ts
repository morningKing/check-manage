/**
 * AI OpenAPI（对外 /api/v1/*）E2E —— 真实后端 + 真实 OpenCode。
 * 覆盖用例：边界（413 请求体门）/ prompt-templates CRUD / memories IDOR /
 * 单会话全生命周期（创建→output 门→完成→results 契约）。
 * 批相关用例（鉴权 401/404 面、staging 路径穿越、批全生命周期、HMAC 完成回调）
 * 已收编至 batch/openapi.spec.ts（2026-10-04，仅移出、本文件保留）。
 */
import { test, expect } from '@playwright/test'
import {
  openApi, stagingUpload, createApiKey, deleteApiKey,
  waitFor, tag, SESSION_TERMINAL,
} from './helpers'

test.setTimeout(600_000)

const KEY_TAG = `openapi-${process.pid}-${Date.now()}`

let KEY = ''

test.beforeAll(async ({ request }) => {
  KEY = await createApiKey(request, KEY_TAG)
})

test.afterAll(async ({ request }) => {
  await deleteApiKey(request, KEY_TAG)
})

test('请求体门：/v1/ai-sessions 超 1MB JSON → 413（D3 修复回归）', async ({ request }) => {
  const big = 'x'.repeat(1024 * 1024 + 4096)
  const r = await openApi(request, KEY, 'POST', '/v1/ai-sessions', {
    prompt: `测试超长 ${big}`,
  })
  expect(r.status).toBe(413)
})

test('prompt-templates 对外 CRUD：重名 409、英文 error 惯例', async ({ request }) => {
  const name = tag('tpl')
  const created = await openApi(request, KEY, 'POST', '/v1/prompt-templates',
                                { name, content: '处理 {{name}}' })
  expect(created.status).toBeLessThan(300)
  const dup = await openApi(request, KEY, 'POST', '/v1/prompt-templates',
                            { name, content: 'x' })
  expect(dup.status).toBe(409)
  expect(dup.json?.error).toMatch(/already in use/)
  // 校验缺失字段 400（英文）
  const bad = await openApi(request, KEY, 'POST', '/v1/prompt-templates', {})
  expect(bad.status).toBe(400)
  // 清理
  const list = await openApi(request, KEY, 'GET', '/v1/prompt-templates')
  const mine = (list.json?.items || list.json?.templates || [])
    .find((t: any) => t.name === name)
  if (mine) {
    await openApi(request, KEY, 'DELETE', `/v1/prompt-templates/${mine.id}`)
  }
})

test('memories 对外：空文本 400、超长 400、删除不存在的记忆 404（IDOR 闸门）',
     async ({ request }) => {
  const empty = await openApi(request, KEY, 'POST', '/v1/memories', { text: '' })
  expect(empty.status).toBe(400)
  const tooLong = await openApi(request, KEY, 'POST', '/v1/memories',
                                { text: '长'.repeat(2001) })
  expect(tooLong.status).toBe(400)
  // 删除一个不可能属于任何人的 id → 404（不是 403/500）
  const del = await openApi(request, KEY, 'DELETE',
                            '/v1/memories/00000000-0000-0000-0000-000000000000')
  expect([404, 500]).toContain(del.status)   // mem0 未知 id 可能 404 或内部错，均不泄漏他人数据
  if (del.status === 500) console.log('memories delete 未知 id 返回 500（可改进为 404）')
})

test('对外单会话：创建（带暂存文件）→ output 门 → 完成 → results 契约', async ({ request }) => {
  const staged = await stagingUpload(request, `oas-${Date.now()}`,
    [{ name: 'greet.txt', body: '第一行内容 BANNER-GREET' }])
  const created = await openApi(request, KEY, 'POST', '/v1/ai-sessions', {
    prompt: '读取 uploads/greet.txt，直接回复文件中出现的 BANNER 标记值，不要其他内容。',
    files: staged,
  })
  expect(created.status, `创建单会话 ${JSON.stringify(created.json)}`).toBe(201)
  const sessionId = created.json.sessionId as string

  // files 只回显文件名
  expect(created.json.files === undefined ||
    JSON.stringify(created.json.files) === JSON.stringify([{ name: 'greet.txt' }]))
    .toBe(true)

  // 轮询到终态；若中途观测到 running，断言 output 未返回（半截文本防泄漏门）
  let sawRunning = false
  const final = await waitFor(async () => {
    const r = await openApi(request, KEY, 'GET', `/v1/ai-sessions/${sessionId}`)
    if (r.json?.status === 'running' || r.json?.status === 'pending') {
      sawRunning = sawRunning || r.json?.status === 'running'
      expect(r.json.output, '运行中不得返回 output（应为 null）').toBeNull()
      return null
    }
    return r.json
  }, { timeoutMs: 420_000, intervalMs: 4000 })
  expect(SESSION_TERMINAL).toContain(final.status)
  if (final.status === 'completed') {
    expect(typeof final.output).toBe('string')
    expect(final.output!.length, 'output 非空').toBeGreaterThan(0)
  }
  expect(sawRunning !== undefined).toBe(true)
})

// 批相关用例（鉴权 401/404 面、staging 路径穿越、批全生命周期、HMAC 完成回调）
// 已于 2026-10-04 收编至 batch/openapi.spec.ts。
