/**
 * 子代理会话复用 E2E（2026-09-24 特性，真实 OpenCode 链路）。
 *
 * 场景：批任务配置 subagent_reuse=["general"] → 派发一条要求对 general 连续
 * 委派两次任务（第二次基于第一次结果）的 prompt → 断言：
 *  1. 模型的两次委派实际续跑同一子会话（两个气泡 subtaskId 相同——插件强制
 *     注入 task_id + 派发指令双保险生效）；
 *  2. 复用子会话的任务段 turn_segments ≥ 2（每段以一次委派 prompt 为边界）；
 *  3. UI：子代理气泡显示 task_id 标识与「已复用·N 段」徽标，展开有分段头。
 *
 * 模型偶发空回合/不委派（OC 刚重启后首回合异常）→ 自动清批重试一次。
 */
import { test, expect, type Page } from '@playwright/test'
import { adminToken } from './helpers'

test.setTimeout(900_000)

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.getByRole('button', { name: /登\s*录/ })
      .waitFor({ state: 'hidden', timeout: 15_000 })
  } catch { /* 已是登录态 */ }
  await page.goto(`/ai-chat?session=`)
  await page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .waitFor({ state: 'visible', timeout: 60_000 })
}

test('子代理会话复用：两次委派同会话 + 任务段边界 + 气泡标识', async ({ request, page }) => {
  const token = await adminToken(request)
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  let subtaskIds: string[] = []
  let childId = ''
  let batchId = ''
  let segments: Array<{ ord: number; turn: number; label: string; firstMsgId: string }> = []

  for (let attempt = 0; attempt < 3 && subtaskIds.length < 2; attempt++) {
    // 上一批没有委派就清掉再试
    if (batchId) {
      await request.fetch(`/api/ai/chat/batches/${batchId}?stop=1`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
    }
    // 1) 内部 API 创建（配置链路的 UI 覆盖见 CreateBatchDialog 手动验证）
    const up = await request.fetch('/api/ai/chat/batches/staging/upload', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'input.txt', mimeType: 'text/plain',
                buffer: Buffer.from('两次委派任务的材料', 'utf-8') },
        upload_session_id: `sru-${Date.now()}-${attempt}`,
      },
    })
    expect(up.status()).toBe(201)
    const staged = (await up.json()) as { name: string; path: string }
    const createRes = await request.fetch('/api/ai/chat/batches', {
      method: 'POST', headers: HDRS,
      data: JSON.stringify({
        name: `AITEST-sru-${Date.now()}-${attempt}`,
        prompt: '请分两次用 task 工具委派给 general 子代理执行（两次是连续的两轮任务）：'
              + '第一轮让 general 计算 37 乘 43 等于多少；'
              + '第二轮让 general 基于第一轮的得数再除以 7 等于多少，并给出最终整数结果。'
              + '两次委派完成后，汇报最终答案。',
        files: [staged],
        subagent_reuse: ['general'],
      }),
    })
    expect(createRes.status()).toBe(201)
    const detail = await createRes.json()
    expect(detail.batch.subagent_reuse).toEqual(['general'])
    batchId = detail.batch.id as string
    childId = detail.sessions[0].id as string

    // 2) 等批任务收敛
    const deadline = Date.now() + 420_000
    let status = ''
    while (Date.now() < deadline) {
      const r = await request.fetch(`/api/ai/chat/batches/${batchId}`, { headers: HDRS })
      status = (await r.json()).batch.status
      if (['completed', 'partial', 'failed'].includes(status)) break
      await new Promise(rr => setTimeout(rr, 4000))
    }
    console.log(`[reuse] attempt ${attempt}: batch status=${status}`)

    // 3) 从子会话消息提取 subtask_use 气泡
    const msgsRes = await request.fetch(`/api/ai/chat/sessions/${childId}/messages`,
                                        { headers: HDRS })
    const msgs = (await msgsRes.json()).messages ?? []
    subtaskIds = []
    for (const m of msgs) {
      for (const p of (m.content ?? [])) {
        if (p.type === 'subtask_use' && p.subtaskId) subtaskIds.push(p.subtaskId)
      }
    }
    console.log(`[reuse] attempt ${attempt}: bubbles=${JSON.stringify([...new Set(subtaskIds)])}`)

    if (subtaskIds.length >= 2) {
      const reusedId = subtaskIds[0]
      const subRes = await request.fetch(
        `/api/ai/chat/sessions/${childId}/subtasks/${reusedId}/messages`, { headers: HDRS })
      expect(subRes.status()).toBe(200)
      segments = (await subRes.json()).subtask.segments ?? []
    }
  }

  // 4) 核心断言：两次委派复用同一子会话 + 任务段 ≥2
  expect(subtaskIds.length, '模型应产生两次委派气泡').toBeGreaterThanOrEqual(2)
  expect(new Set(subtaskIds).size, '两次委派必须复用同一子会话').toBe(1)
  expect(segments.length, '复用会话应有 ≥2 个任务段').toBeGreaterThanOrEqual(2)
  expect(segments[0].label).toBeTruthy()

  // 5) UI：打开子会话 → 气泡头 task_id 标识 + 复用徽标 → 展开看分段头
  await login(page)
  await page.goto(`/ai-chat?session=${childId}`)
  const bubble = page.locator('.subtask-bubble').first()
  await bubble.waitFor({ state: 'visible', timeout: 30_000 })
  await expect(bubble.locator('.subtask-bubble__taskid')).toBeVisible()
  await expect(bubble.locator('.subtask-bubble__reuse')).toContainText('已复用·2 段')
  await bubble.click()   // 展开
  await expect(bubble.locator('.subtask-bubble__segment').first())
    .toBeVisible({ timeout: 15_000 })
  await expect(bubble.locator('.subtask-bubble__segment').nth(1)).toBeVisible()
  await page.screenshot({
    path: 'e2e/screenshots/ai-full/subagent-reuse-bubble.png', fullPage: true,
  })

  // 6) 清理
  await request.fetch(`/api/ai/chat/batches/${batchId}?stop=1`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
  })
})

test('创建对话框：子代理会话复用配置可保存（UI 链路）', async ({ page, request }) => {
  test.setTimeout(180_000)
  await login(page)
  await page.goto('/ai-chat')

  const createBatchBtn = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .getByRole('button', { name: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 15_000 })

  const name = `AITEST-sru-ui-${Date.now()}`
  await dialog.locator('input[data-test="name"]').fill(name)
  await dialog.locator('textarea[data-test="prompt"]').fill('自验证：复用配置保存')

  // 多选下拉：展开 → 勾选 general
  const select = dialog.locator('[data-test="reuse-select"]')
  await select.click()
  const option = page.locator('.el-select-dropdown__item', { hasText: 'general' }).first()
  await option.waitFor({ state: 'visible', timeout: 10_000 })
  await option.click()
  await page.keyboard.press('Escape')

  // 需要至少一个文件（内部创建约束）
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })

  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  // API 断言：批次 subagent_reuse 已保存
  const token = await adminToken(request)
  const deadline = Date.now() + 15_000
  let saved: string[] | null = null
  while (Date.now() < deadline) {
    const listRes = await request.fetch('/api/ai/chat/batches?page=1&pageSize=5', {
      headers: { Authorization: `Bearer ${token}` },
    })
    const hit = ((await listRes.json()).items ?? []).find((b: any) => b.name === name)
    if (hit) {
      const d = await request.fetch(`/api/ai/chat/batches/${hit.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      saved = (await d.json()).batch.subagent_reuse ?? null
      // 清理
      await request.fetch(`/api/ai/chat/batches/${hit.id}?stop=1`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
      break
    }
    await new Promise(rr => setTimeout(rr, 1000))
  }
  expect(saved, '复用配置应随创建保存').toEqual(['general'])
})
