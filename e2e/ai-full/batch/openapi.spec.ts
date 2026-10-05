/**
 * 批域 · 对外 OpenAPI（/api/v1/ai-batches，X-API-Key）E2E —— 真实后端 + 真实 OpenCode。
 *
 * 2026-10-04 收编（仅移出、源文件保留）：
 *  - 自 ../ai-openapi.spec.ts 迁入：鉴权 401/404 面、staging 路径穿越、
 *    X-API-Key 建批→终态→results 全生命周期、HMAC 完成回调
 *    （旧用例并入下方 webhook 用例：事件头/timestamp/精确签名/secret 不回显全保留）；
 *  - 自 ../ai-harness-safety.spec.ts P0-3 迁入：对外删除治理
 *    （非终态 409 BATCH_NOT_TERMINAL / stop=true bounded drain）。
 *  - 自 ../ai-harness-safety.spec.ts P1「对外 events」并入用例 3（2026-10-04
 *    Task 14，原文件同日只剩 P2 编排用例）：X-API-Key 事件流契约
 *    （200 / 非空 / camelCase eventId+eventSeq）。
 * 新增：跨 API Key 隔离（list/detail 按 api_key_id 再圈一层，
 * 见 utils/batch_repo.py::list_batches）、file-records/results 对外契约。
 *
 * LLM 预算：
 *  - 用例 1/2：0 LLM（鉴权与路径校验在进入执行前拒绝）；
 *  - 用例 3（全生命周期）：@llm —— 2+1 个真实子会话，预算 ~10 分钟；
 *  - 用例 4（删除治理）：@llm-light —— sleep 长任务，1 次轻量消耗；
 *  - 用例 5（Key 隔离）：@llm-light —— 1 次轻量轮（随即 stop 删除）；
 *  - 用例 6（webhook）：@llm —— 1 个真实子会话触发回调；
 *  - 用例 7（file-records/results）：@llm —— 1 个真实子会话。
 */
import crypto from 'node:crypto'
import http from 'node:http'
import { test, expect } from '@playwright/test'
import {
  openApi, stagingUpload, createApiKey, deleteApiKey, waitFor, tag,
  BATCH_TERMINAL,
} from '../helpers'
import { adminTokenCached } from './toolbox'
import { cleanupBatch } from './batch-helpers'

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

test('对外批任务全生命周期：上传→创建→完成→results→PATCH 整体替换→append→删除',
     async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
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

  // 对外事件流（← ai-harness-safety.spec.ts P1「对外 events」收编，Task 14）：
  // X-API-Key 可读批事件时间线，对外契约是 camelCase（eventId/eventSeq），
  // 与内部 events 的 snake_case（event_seq）刻意区分——断言与源一致
  const evExt = await openApi(request, KEY, 'GET',
                              `/v1/ai-batches/${batchId}/events?limit=50`)
  expect(evExt.status).toBe(200)
  expect((evExt.json?.events ?? []).length).toBeGreaterThan(0)
  expect(evExt.json.events[0]).toHaveProperty('eventId')
  expect(evExt.json.events[0]).toHaveProperty('eventSeq')

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

test('对外删除治理：非终态 DELETE 409 BATCH_NOT_TERMINAL；stop=true drain 后删除',
     async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })
  const staged = await stagingUpload(request, `delg-${Date.now()}`, [
    { name: 'd.txt', body: '长任务材料：sleep 探针。\n' },
  ])
  // sleep 长任务构造稳定 running 窗口（同 toolbox.sleepBatch 配方；默认主 agent，
  // 不传 agent——'general' 是 subagent 不能作主 Agent）
  const created = await openApi(request, KEY, 'POST', '/v1/ai-batches', {
    name: tag('delg'),
    prompt: '用 bash 工具执行 `sleep 60`，结束后输出一行 done 即可，不要做别的。',
    files: staged,
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const bid = created.json.batchId as string
  try {
    // 运行中直接删 → 409 BATCH_NOT_TERMINAL（无副作用）
    await waitFor(async () => {
      const d = await openApi(request, KEY, 'GET', `/v1/ai-batches/${bid}`)
      return d.json?.status === 'running' ? d.json : null
    }, { timeoutMs: 120_000, intervalMs: 2000 })
    const del = await openApi(request, KEY, 'DELETE', `/v1/ai-batches/${bid}`)
    expect(del.status, JSON.stringify(del.json)).toBe(409)
    expect(del.json?.error?.code).toBe('BATCH_NOT_TERMINAL')

    // stop=true → cancel + bounded drain（10s）→ 删除成功；drain 超时保留任务（409）
    const delStop = await openApi(request, KEY, 'DELETE', `/v1/ai-batches/${bid}?stop=true`)
    expect([200, 409]).toContain(delStop.status)
    if (delStop.status === 200) {
      expect(delStop.json).toEqual({ deleted: true })
    }
    const after = await openApi(request, KEY, 'GET', `/v1/ai-batches/${bid}`)
    expect([200, 404]).toContain(after.status)
  } finally {
    await cleanupBatch(await adminTokenCached(), bid)   // 兜底（已删则为 no-op）
  }
})

test('跨 API Key 隔离：Key B 不可见 Key A 的批', async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })
  const keyATag = tag('keyA')
  const keyBTag = tag('keyB')
  const keyA = await createApiKey(request, keyATag)
  const keyB = await createApiKey(request, keyBTag)
  let bid = ''
  try {
    const staged = await stagingUpload(request, `iso-${Date.now()}`,
      [{ name: 'iso.txt', body: '隔离探针\n' }])
    // 不传 agent：默认主 Agent（'general' 是 subagent，不能作主 Agent）
    const created = await openApi(request, keyA, 'POST', '/v1/ai-batches', {
      name: tag('iso'), prompt: '输出一行 ok。', files: staged,
    })
    expect(created.status, JSON.stringify(created.json)).toBe(201)
    bid = created.json.batchId as string

    // Key B GET Key A 的批 → 404（不泄漏存在性；403 在容忍面内）
    const getB = await openApi(request, keyB, 'GET', `/v1/ai-batches/${bid}`)
    expect([403, 404]).toContain(getB.status)
    // Key B 的子资源同样不可见
    const resB = await openApi(request, keyB, 'GET', `/v1/ai-batches/${bid}/results`)
    expect([403, 404]).toContain(resB.status)
    // Key B 列表不含 Key A 的批（api_key_id 圈层，batch_repo.list_batches）
    const listB = await openApi(request, keyB, 'GET', '/v1/ai-batches')
    expect(JSON.stringify(listB.json ?? {})).not.toContain(bid)
    // sanity：Key A 自己可见
    const getA = await openApi(request, keyA, 'GET', `/v1/ai-batches/${bid}`)
    expect(getA.status).toBe(200)
  } finally {
    if (bid) {
      await openApi(request, keyA, 'DELETE', `/v1/ai-batches/${bid}?stop=1`)
      await cleanupBatch(await adminTokenCached(), bid)   // 兜底（已删则为 no-op）
    }
    await deleteApiKey(request, keyATag)
    await deleteApiKey(request, keyBTag)
  }
})

test('终态 webhook：HMAC 签名回调真实送达本地 receiver', async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
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
  const keyTag = tag('hook')
  const key = await createApiKey(request, keyTag)
  try {
    const secret = `sec-${Date.now()}`
    const staged = await stagingUpload(request, `hook-${Date.now()}`,
      [{ name: 'w.txt', body: 'WEBHOOK-OK' }])
    const created = await openApi(request, key, 'POST', '/v1/ai-batches', {
      name: tag('hook'),
      prompt: '读取 uploads/w.txt，直接回复其中的标记值。',
      files: staged,
      callbackUrl: `http://127.0.0.1:${port}/cb`,
      callbackSecret: secret,
    })
    expect(created.status).toBe(201)
    const bid = created.json.batchId as string
    // 回调 secret 不得回显
    expect(JSON.stringify(created.json)).not.toContain(secret)

    // 送达：终态迁移触发（outbox 投递周期内；outbox 关闭时为直发线程）
    await waitFor(async () => received.length > 0 ? true : null,
                  { timeoutMs: 480_000, intervalMs: 5000 })
    const cb = received[0]
    expect(cb.headers['x-webhook-event']).toBe('ai_batch_completed')
    const ts = cb.headers['x-webhook-timestamp'] as string
    const sig = cb.headers['x-webhook-signature'] as string
    expect(ts).toBeTruthy()
    // 签名格式以 webhook_engine.py::_compute_signature 为准：
    // HMAC-SHA256(secret, `${ts}.${原始body}`) 的 hex，无前缀
    // （outbox 路径 signature 列存原始 secret，投递时同式重算——等价）
    const expected = crypto.createHmac('sha256', secret)
      .update(`${ts}.${cb.body}`).digest('hex')
    expect(sig, 'HMAC-SHA256(secret, ts.body) 签名必须匹配').toBe(expected)

    // 载荷可关联到本批（batch_engine._notify_callback 固定带 batchId）
    const payload = JSON.parse(cb.body)
    expect(payload.batchId ?? payload.batch?.id).toBe(bid)

    // 回调与状态收敛一致：终态后再确认
    const final = await openApi(request, key, 'GET', `/v1/ai-batches/${bid}`)
    expect(BATCH_TERMINAL).toContain(final.json?.status)
    await openApi(request, key, 'DELETE', `/v1/ai-batches/${bid}`)
  } finally {
    server.close()
    await deleteApiKey(request, keyTag)
  }
}, 720_000)

test('file-records / results 契约：终态批可读子任务文件记录', async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const keyTag = tag('files')
  const key = await createApiKey(request, keyTag)
  let bid = ''
  try {
    const staged = await stagingUpload(request, `fr-${Date.now()}`,
      [{ name: 'fr.txt', body: 'file-records 探针 FR-MARK\n' }])
    const created = await openApi(request, key, 'POST', '/v1/ai-batches', {
      name: tag('files'),
      prompt: '读取 uploads/fr.txt 内容并原样输出。',
      files: staged,
    })
    expect(created.status, JSON.stringify(created.json)).toBe(201)
    bid = created.json.batchId as string
    await waitFor(async () => {
      const d = await openApi(request, key, 'GET', `/v1/ai-batches/${bid}`)
      return BATCH_TERMINAL.includes(d.json?.status) ? d.json : null
    }, { timeoutMs: 480_000, intervalMs: 5000 })

    // file-records：每个子会话一行（name/seq/status/files），batchId 回显
    const rec = await openApi(request, key, 'GET', `/v1/ai-batches/${bid}/file-records`)
    expect(rec.status, JSON.stringify(rec.json)).toBeLessThan(300)
    expect(rec.json?.batchId).toBe(bid)
    expect(Array.isArray(rec.json?.results)).toBe(true)
    expect(rec.json?.results?.length).toBe(1)

    // results：batchId 回显 + 单子任务行
    const res = await openApi(request, key, 'GET', `/v1/ai-batches/${bid}/results`)
    expect(res.status, JSON.stringify(res.json)).toBeLessThan(300)
    expect(res.json?.batchId).toBe(bid)
    expect((res.json?.results ?? []).length).toBe(1)
  } finally {
    if (bid) {
      await openApi(request, key, 'DELETE', `/v1/ai-batches/${bid}`)
      await cleanupBatch(await adminTokenCached(), bid)   // 兜底（已删则为 no-op）
    }
    await deleteApiKey(request, keyTag)
  }
}, 720_000)
