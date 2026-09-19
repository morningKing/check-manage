/**
 * AI 批任务生命周期 E2E（真实 OpenCode 子会话）。
 * 覆盖用例：TC-BATCH-001/002/003/005/006/007、TC-SUB-001（子会话查看）。
 */
import { test, expect } from '@playwright/test'
import {
  api, gotoWithAuth, screenshot, stagingUpload, tag, waitFor,
} from './helpers'

test.setTimeout(600_000)

test('批任务：暂存上传→创建→BatchGroup 展示→子会话全文→终态治理', async ({ page, request }) => {
  const name = tag('batch-ui')
  const uploadSessionId = `e2e-${Date.now()}`
  const staged = await stagingUpload(request, uploadSessionId, [
    { name: 'one.txt', body: 'BATCH-MARK-ONE' },
    { name: 'two.txt', body: 'BATCH-MARK-TWO' },
  ])

  const created = await api(request, 'POST', '/ai/chat/batches', {
    name,
    prompt: '读取 uploads/ 下的文件，直接回复文件中出现的 BATCH-MARK 值，不要其他内容。',
    files: staged,
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const batchId = created.json.batchId || created.json.batch?.id
  expect(batchId).toBeTruthy()

  // 打开 AI 助手页面：侧栏应出现批任务分组（BatchGroup）
  // （AI 会话页有 SSE 长连接，不能等 networkidle —— 直接断言分组出现）
  await gotoWithAuth(page, '/ai-chat')
  const group = page.getByText(name, { exact: false }).first()
  await expect(group).toBeVisible({ timeout: 30_000 })
  await screenshot(page, 'batch-sidebar-group')

  // 展开分组：子会话（以文件名展示）可见
  await group.click()
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
    const r = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
    const st = r.json?.batch?.status
    return ['completed', 'partial', 'failed'].includes(st) ? st : null
  }, { timeoutMs: 480_000, intervalMs: 5000 })
  console.log('batch final status:', finalStatus)
  expect(['completed', 'partial', 'failed']).toContain(finalStatus)

  // 子会话对话内容已落库：owner 端子会话即真实会话行，直接读其消息
  const detail = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
  const children = detail.json?.sessions || []
  expect(children.length).toBe(2)
  const childId = children[0].id
  const childMsgs = await api(request, 'GET',
    `/ai/chat/sessions/${childId}/messages`)
  expect(childMsgs.status).toBe(200)
  expect((childMsgs.json?.messages || []).length, '子会话消息应已持久化').toBeGreaterThan(0)

  // retry-failed：终态批上必然可用（无失败时返回 {retried:0}）
  const retry = await api(request, 'POST', `/ai/chat/batches/${batchId}/retry-failed`)
  expect(retry.status).toBe(200)
  expect(retry.json?.retried).toBeGreaterThanOrEqual(0)

  // 终态批任务直接删除（无 stop 门）
  const del = await api(request, 'DELETE', `/ai/chat/batches/${batchId}`)
  expect([200, 204]).toContain(del.status)
  const gone = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
  expect(gone.status).toBe(404)
})

test('批任务删除治理：非终态删除必须 409，stop=1 才可停止并删除', async ({ request }) => {
  const name = tag('batch-stopdel')
  const uploadSessionId = `e2e-stop-${Date.now()}`
  const staged = await stagingUpload(request, uploadSessionId, [
    { name: 'long.txt', body: '内容' },
  ])
  const created = await api(request, 'POST', '/ai/chat/batches', {
    name,
    prompt: '这是一个长任务：请从 1 数到 200，每行一个数字，数完后总结。不要使用工具。',
    files: staged,
  })
  expect(created.status).toBe(201)
  const batchId = created.json.batchId || created.json.batch?.id

  // 尽快尝试直接删除：非终态必须被拒
  const early = await api(request, 'DELETE', `/ai/chat/batches/${batchId}`)
  // 若 worker 还没开跑（仍 pending）同样属非终态 → 409；小概率已跑完 → 204
  const detailNow = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
  const nowStatus = detailNow.json?.batch?.status
  if (!['completed', 'partial', 'failed'].includes(nowStatus)) {
    expect(early.status, `非终态(${nowStatus})删除应 409`).toBe(409)
    // stop=1：停止并删除
    const stopDel = await api(request, 'DELETE', `/ai/chat/batches/${batchId}?stop=1`)
    expect([204, 200]).toContain(stopDel.status)
  } else {
    expect([204, 200]).toContain(early.status)
  }
  const gone = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
  expect(gone.status).toBe(404)
})
