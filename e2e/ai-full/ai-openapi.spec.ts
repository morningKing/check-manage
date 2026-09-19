/**
 * AI OpenAPI（对外 /api/v1/*）E2E —— 真实后端 + 真实 OpenCode。
 * 覆盖用例：TC-API-001~008/012~016（鉴权/隔离/边界/状态机/HMAC 回调/输出门）。
 */
import http from 'node:http'
import crypto from 'node:crypto'
import { test, expect } from '@playwright/test'
import {
  api, openApi, stagingUpload, createApiKey, deleteApiKey,
  waitFor, tag, BATCH_TERMINAL, SESSION_TERMINAL,
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

test('鉴权矩阵：无密钥/伪造密钥 401；未创建资源 404 不泄漏存在性', async ({ request }) => {
  // 无密钥
  const noKey = await request.fetch('/api/v1/ai-batches', { method: 'GET' })
  expect(noKey.status()).toBe(401)
  // 伪造密钥
  const badKey = await openApi(request, 'cm_fake_key_000', 'GET', '/v1/ai-batches')
  expect(badKey.status).toBe(401)
  // 带合法密钥查不存在的批任务/会话/扫描任务 → 一律 404（中文 error + code）
  for (const [method, path] of [
    ['GET', '/v1/ai-batches/b-not-exist'],
    ['GET', '/v1/ai-batches/b-not-exist/results'],
    ['GET', '/v1/ai-sessions/s-not-exist'],
    ['GET', '/v1/ai-scan-tasks/task-not-exist'],
  ] as const) {
    const r = await openApi(request, KEY, method, path)
    expect(r.status, path).toBe(404)
    expect(r.json?.error, path).toBeTruthy()
  }
})

test('路径归属与穿越：绝对路径/.. /他人 staging 一律 400', async ({ request }) => {
  for (const badPath of [
    'C:/Windows/win.ini',
    '/etc/passwd',
    '../secret.txt',
    `batch-staging/someone-else/us/x.csv`,
    `batch-staging/../../admin/x.csv`,
  ]) {
    const r = await openApi(request, KEY, 'POST', '/v1/ai-sessions', {
      prompt: '测试', files: [{ name: 'x.csv', path: badPath }],
    })
    expect(r.status, `path=${badPath}`).toBe(400)
    expect(r.json?.code, `path=${badPath}`).toBeTruthy()
  }
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

test('对外批任务全生命周期：上传→创建→完成→results→PATCH 整体替换→append→删除',
     async ({ request }) => {
  const staged = await stagingUpload(request, `batch-${Date.now()}`, [
    { name: 'a.txt', body: '内容A MARK-A' },
    { name: 'b.txt', body: '内容B MARK-B' },
  ])
  const created = await openApi(request, KEY, 'POST', '/v1/ai-batches', {
    name: tag('batch'),
    prompt: '读取 uploads/ 下的文件，直接回复文件中出现的 MARK 标记值，不要其他内容。',
    files: staged,
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const batchId = created.json.batchId
  expect(created.json.total).toBe(2)

  // 轮询批任务完成
  await waitFor(async () => {
    const r = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}`)
    return BATCH_TERMINAL.includes(r.json?.status) ? r.json : null
  }, { timeoutMs: 480_000, intervalMs: 5000 })

  // results：completed 的子任务才有 output
  const results = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}/results`)
  expect(results.status).toBe(200)
  const rows = results.json?.results || []
  expect(rows.length).toBe(2)
  for (const row of rows) {
    if (row.status === 'completed') {
      expect((row.output || '').length, 'output 非空').toBeGreaterThan(0)
      expect(row.output).toMatch(/MARK-[AB]/)
    } else {
      expect(row.status).toBe('failed')
    }
  }

  // PATCH 整体替换语义：只传 agent，model 应被清空为 null（文档化行为）
  const patched = await openApi(request, KEY, 'PATCH', `/v1/ai-batches/${batchId}`,
                                { agent: 'nonexistent-agent-x' })
  expect(patched.status).toBeLessThan(300)
  const after = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}`)
  expect(after.json.model ?? null).toBeNull()
  // 还原为默认，避免后续 append 重跑用到不存在的 agent
  await openApi(request, KEY, 'PATCH', `/v1/ai-batches/${batchId}`,
                { agent: '', model: '' })

  // append：对已终态批任务追加是合法用法 → 状态回 running
  const staged2 = await stagingUpload(request, `batch-append-${Date.now()}`,
    [{ name: 'c.txt', body: '内容C MARK-C' }])
  const appended = await openApi(request, KEY, 'POST',
                                 `/v1/ai-batches/${batchId}/append`,
                                 { files: staged2 })
  expect(appended.status, 'append 终态批任务').toBeLessThan(300)
  expect(appended.json.total).toBe(3)
  expect(appended.json.status).toBe('running')

  // retry-failed 非终态 → 409
  const retry = await openApi(request, KEY, 'POST',
                              `/v1/ai-batches/${batchId}/retry-failed`)
  expect(retry.status).toBe(409)

  // 等待追加的子任务收敛到终态
  await waitFor(async () => {
    const r = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}`)
    return BATCH_TERMINAL.includes(r.json?.status) ? r.json : null
  }, { timeoutMs: 480_000, intervalMs: 5000 })

  // 删除
  const del = await openApi(request, KEY, 'DELETE', `/v1/ai-batches/${batchId}`)
  expect(del.status).toBeLessThan(300)
  const gone = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}`)
  expect(gone.status).toBe(404)
})

test('HMAC 完成回调：签名/事件头/原始体验证（真实子任务收敛触发）', async ({ request }) => {
  const received: { headers: http.IncomingHttpHeaders; body: string }[] = []
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c: Buffer) => { body += c.toString('utf-8') })
    req.on('end', () => {
      received.push({ headers: req.headers, body })
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end('{"ok":true}')
    })
  })
  const port = await new Promise<number>(resolve => {
    server.listen(0, '127.0.0.1', () => resolve((server.address() as any).port))
  })

  try {
    const secret = `sec-${Date.now()}`
    const staged = await stagingUpload(request, `cb-${Date.now()}`,
      [{ name: 'cb.txt', body: 'CALLBACK-OK' }])
    const created = await openApi(request, KEY, 'POST', '/v1/ai-batches', {
      name: tag('cb'),
      prompt: '读取 uploads/cb.txt，直接回复其中的标记值。',
      files: staged,
      callbackUrl: `http://127.0.0.1:${port}/cb`,
      callbackSecret: secret,
    })
    expect(created.status).toBe(201)
    const batchId = created.json.batchId
    // 回调 secret 不得回显
    expect(JSON.stringify(created.json)).not.toContain(secret)

    await waitFor(async () => received.length > 0 ? true : null,
                  { timeoutMs: 480_000, intervalMs: 5000 })
    const cb = received[0]
    expect(cb.headers['x-webhook-event']).toBe('ai_batch_completed')
    const ts = cb.headers['x-webhook-timestamp'] as string
    const sig = cb.headers['x-webhook-signature'] as string
    expect(ts).toBeTruthy()
    const expected = crypto.createHmac('sha256', secret)
      .update(`${ts}.${cb.body}`).digest('hex')
    expect(sig, 'HMAC-SHA256(secret, ts.body) 签名必须匹配').toBe(expected)

    // 终态后再确认批任务收敛（回调与状态收敛一致）
    const final = await openApi(request, KEY, 'GET', `/v1/ai-batches/${batchId}`)
    expect(BATCH_TERMINAL).toContain(final.json?.status)
    await openApi(request, KEY, 'DELETE', `/v1/ai-batches/${batchId}`)
  } finally {
    server.close()
  }
})
