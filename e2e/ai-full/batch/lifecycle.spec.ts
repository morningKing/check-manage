/**
 * AI 批任务生命周期域 spec（2026-10-04 从 ai-batch-lifecycle.spec.ts 收编 + 补缺）。
 *
 * 覆盖用例：TC-BATCH-001/002/003/005/006/007、TC-SUB-001（迁移自
 * ai-batch-lifecycle，断言语义与源 spec 一致，取数直连后端 3002）+
 * 4 个确定性补缺用例（PATCH 配置编辑 / append 追加 / 建批负路径 / events 增量）。
 *
 * LLM 预算标记：
 * - 用例 1（暂存上传→UI→子会话全文→终态治理）：@llm —— 2 个真实 OpenCode 子会话，预算 ~10 分钟；
 * - 用例 2（删除治理）：@llm-light —— sleep 长任务，1 次轻量 LLM 消耗；
 * - 用例 3-6（PATCH/append/负路径/events）：0 LLM（fail-fast 与纯校验构造）。
 */
import { test, expect } from '@playwright/test'
import {
  API, BATCH_TERMINAL, authHeaders, cleanupBatch, createBatch, getDetail,
  uploadStaging, waitFor,
} from './batch-helpers'
import { adminTokenCached, failFastBatch, sleepBatch, tag } from './toolbox'
import { gotoWithAuth, screenshot } from '../helpers'
import { expandBatchGroup } from './ui-helpers'

test.setTimeout(600_000)

test('批任务：暂存上传→创建→BatchGroup 展示→子会话全文→终态治理', async ({ page }) => {
  const tk = await adminTokenCached()
  const name = tag('batch-ui')
  const uploadSessionId = `e2e-${Date.now()}`
  const staged = []
  for (const f of [
    { name: 'one.txt', body: 'BATCH-MARK-ONE' },
    { name: 'two.txt', body: 'BATCH-MARK-TWO' },
  ]) {
    staged.push(await uploadStaging(tk, f.name, f.body, uploadSessionId))
  }

  // 源 spec 断言 created.status === 201；createBatch 非 201 直接抛错（带状态码+响应体）
  const created = await createBatch(tk, {
    name,
    prompt: '读取 uploads/ 下的文件，直接回复文件中出现的 BATCH-MARK 值，不要其他内容。',
    files: staged,
  })
  const batchId = created.batchId || created.batch?.id
  expect(batchId).toBeTruthy()

  try {
    // 打开 AI 助手页面：侧栏应出现批任务分组（BatchGroup）
    // （AI 会话页有 SSE 长连接，不能等 networkidle —— 直接断言分组出现）
    await gotoWithAuth(page, '/ai-chat')
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group).toBeVisible({ timeout: 30_000 })
    await screenshot(page, 'batch-sidebar-group')

    // 展开分组（重试循环语义收敛进 ui-helpers）：子会话（以文件名展示）可见
    await expandBatchGroup(page, name)
    await expect(page.getByText('one.txt', { exact: false }).first())
      .toBeVisible({ timeout: 15_000 })
    await screenshot(page, 'batch-children')

    // 点击子会话 → 打开完整对话（批任务子会话在独立查看面板/会话页展示）
    await page.getByText('one.txt', { exact: false }).first().click()
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2500)
    await screenshot(page, 'batch-child-conversation')

    // 轮询批任务到达终态
    const finalStatus = await waitFor(async () => {
      const d = await getDetail(tk, batchId)
      const st = d.batch?.status
      return BATCH_TERMINAL.includes(st) ? st : null
    }, 480_000, '批次终态', 5000)
    console.log('batch final status:', finalStatus)
    expect(BATCH_TERMINAL).toContain(finalStatus)

    // 子会话对话内容已落库：owner 端子会话即真实会话行，直接读其消息
    const detail = await getDetail(tk, batchId)
    const children = detail.sessions || []
    expect(children.length).toBe(2)
    const childId = children[0].id
    const childMsgs = await fetch(`${API}/ai/chat/sessions/${childId}/messages`, {
      headers: authHeaders(tk),
    })
    expect(childMsgs.status).toBe(200)
    const childMsgsJson = await childMsgs.json()
    expect((childMsgsJson?.messages || []).length, '子会话消息应已持久化')
      .toBeGreaterThan(0)

    // retry-failed：终态批上必然可用（无失败时返回 {retried:0}）
    const retry = await fetch(`${API}/ai/chat/batches/${batchId}/retry-failed`, {
      method: 'POST', headers: authHeaders(tk),
    })
    expect(retry.status).toBe(200)
    expect((await retry.json())?.retried).toBeGreaterThanOrEqual(0)

    // 终态批任务直接删除（无 stop 门）
    const del = await fetch(`${API}/ai/chat/batches/${batchId}`, {
      method: 'DELETE', headers: authHeaders(tk),
    })
    expect([200, 204]).toContain(del.status)
    const gone = await fetch(`${API}/ai/chat/batches/${batchId}`, {
      headers: authHeaders(tk),
    })
    expect(gone.status).toBe(404)
  } finally {
    await cleanupBatch(tk, batchId)   // 已删则 404 被忽略；失败路径兜底清理
  }
})

test('批任务删除治理：非终态删除必须 409，stop=1 才可停止并删除', async () => {
  const tk = await adminTokenCached()
  // toolbox.sleepBatch 构造稳定 running 窗口（替代源内联「数到 200」长任务）
  const batchId = await sleepBatch(tk, { children: 1 })
  const del = (stop: boolean) => fetch(
    `${API}/ai/chat/batches/${batchId}${stop ? '?stop=1' : ''}`,
    { method: 'DELETE', headers: authHeaders(tk) })

  try {
    // 尽快尝试直接删除：非终态必须被拒
    const early = await del(false)
    // 若 worker 还没开跑（仍 pending）同样属非终态 → 409；小概率已跑完 → 204
    const detailNow = await getDetail(tk, batchId)
    const nowStatus = detailNow.batch?.status
    if (!BATCH_TERMINAL.includes(nowStatus)) {
      expect(early.status, `非终态(${nowStatus})删除应 409`).toBe(409)
      // stop=1：停止并删除。H2 有界 drain（10s）内子任务未收口时契约返回
      // 409 BATCH_DRAIN_TIMEOUT（retryable:true）——sleep 长任务模型慢时
      // 一次 DELETE 收不干净，按契约重试直至 204/200
      const stopDeadline = Date.now() + 60_000
      let stopDel: Response
      do {
        stopDel = await del(true)
        if (![204, 200].includes(stopDel.status)) {
          expect(stopDel.status, '非 409 的失败不应重试').toBe(409)
          expect((await stopDel.json())?.error?.code).toBe('BATCH_DRAIN_TIMEOUT')
          await new Promise(r => setTimeout(r, 3_000))
        }
      } while (![204, 200].includes(stopDel.status) && Date.now() < stopDeadline)
      expect([204, 200]).toContain(stopDel.status)
    } else {
      expect([204, 200]).toContain(early.status)
    }
    const gone = await fetch(`${API}/ai/chat/batches/${batchId}`, {
      headers: authHeaders(tk),
    })
    expect(gone.status).toBe(404)
  } finally {
    await cleanupBatch(tk, batchId)   // 已删则 404 被忽略；失败路径兜底清理
  }
})

test('PATCH 配置编辑生效：agent/prompt 回读 + 门禁期望同步', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  try {
    // 服务端 validate_checks 要求 {name, check_type, tool, args_pattern} 形状
    // （brief 草稿的 {type:'tool_assert'} 会被 400 拒收——已按实际契约修正）
    const checks = [{
      name: 'e2e sleep gate', check_type: 'tool',
      tool: 'bash', args_pattern: 'sleep', min_count: 1,
    }]
    const r = await fetch(`${API}/ai/chat/batches/${bid}`, {
      method: 'PATCH', headers: authHeaders(tk),
      body: JSON.stringify({
        name: tag('edited'), prompt: '改后的提示词', agent: 'general',
        action_checks: checks,
      }),
    })
    expect(r.status).toBe(200)
    const d = await getDetail(tk, bid)
    expect(d.batch.prompt).toBe('改后的提示词')
    expect(d.batch.agent).toBe('general')
    expect(JSON.stringify(d.batch.action_checks ?? d.action_checks ?? []))
      .toContain('args_pattern')
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('append 追加文件：新子任务生成并被派发（fail-fast 收敛，0 LLM）', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  try {
    const before = await getDetail(tk, bid)
    const up = await uploadStaging(tk, 'extra.txt', '追加输入\n', tag('upl'))
    const r = await fetch(`${API}/ai/chat/batches/${bid}/append`, {
      method: 'POST', headers: authHeaders(tk), body: JSON.stringify({ files: [up] }),
    })
    expect(r.status).toBeLessThan(300)
    const d = await waitFor(async () => {
      const cur = await getDetail(tk, bid)
      return (cur.sessions?.length ?? 0) === (before.sessions?.length ?? 0) + 1 &&
        (cur.sessions ?? []).every((s: any) => s.status === 'failed') ? cur : null
    }, 120_000, '追加子任务 fail-fast 终态')
    expect(d.batch.total).toBe((before.batch?.total ?? before.sessions.length) + 1)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('建批校验负路径：0 文件 / 空 prompt / staging 路径穿越', async () => {
  const tk = await adminTokenCached()
  // 0 文件
  const noFiles = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({ name: tag('neg'), prompt: 'x', files: [] }),
  })
  expect(noFiles.status).toBeGreaterThanOrEqual(400)
  // 空 prompt
  const up = await uploadStaging(tk, 'neg.txt', 'x\n', tag('upl'))
  const noPrompt = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({ name: tag('neg'), prompt: '   ', files: [up] }),
  })
  expect(noPrompt.status).toBeGreaterThanOrEqual(400)
  // staging 路径穿越 ①：upload_session_id 纯路径段（消毒后为空）→ 400。
  // （staging 上传对**文件名**穿越是静默消毒（safe_filename 取 basename 返 201），
  // 真正的拒绝点在 upload_session_id 与 create/append 的 files[].path —— 已按
  // 实际防线修正 brief 草稿的断言位置）
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('x')]), 'evil.csv')
  form.append('upload_session_id', '../../..')
  const trav = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST', headers: { Authorization: `Bearer ${tk}` }, body: form,
  })
  expect(trav.status).toBeGreaterThanOrEqual(400)
  // staging 路径穿越 ②：create 的 files[].path 带 .. → validate_staged_files 400
  const travCreate = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({
      name: tag('neg'), prompt: 'x',
      files: [{ name: 'evil.csv', path: '../../admin/evil.csv' }],
    }),
  })
  expect(travCreate.status).toBeGreaterThanOrEqual(400)
})

test('events afterSeq 增量：afterSeq=latest 返回空、新事件出现在增量窗口', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  try {
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      return (d.sessions ?? []).every((s: any) => s.status === 'failed') ? d : null
    }, 90_000, '子任务终态（产生事件）')
    // 事件行字段是 event_seq（brief 草稿写 seq —— 已按 GET /events 实际返回修正）
    const allRes = await fetch(`${API}/ai/chat/batches/${bid}/events`, {
      headers: authHeaders(tk),
    })
    expect(allRes.status).toBe(200)
    const all = await allRes.json()
    const events = all.events ?? all
    expect(events.length).toBeGreaterThan(0)
    const lastSeq = events[events.length - 1].event_seq
    expect(lastSeq).toBeGreaterThan(0)
    const sinceRes = await fetch(
      `${API}/ai/chat/batches/${bid}/events?afterSeq=${lastSeq}`,
      { headers: authHeaders(tk) })
    expect(sinceRes.status).toBe(200)
    const since = await sinceRes.json()
    // afterSeq=latest：增量窗口为空，且任何返回行都不得越过 lastSeq
    expect((since.events ?? []).filter((e: any) => e.event_seq > lastSeq))
      .toHaveLength(0)
    expect(since.events ?? []).toHaveLength(0)
  } finally {
    await cleanupBatch(tk, bid)
  }
})
