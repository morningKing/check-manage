import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 两个修复的端到端验证：
 *
 * 1. 批任务「停止全部 → 继续运行」：停止后子任务变 cancelled、出现「继续运行」
 *    入口；继续后 cancelled 子任务恢复执行并到达终态。停止前若观察到子任务
 *    已开跑，还断言续跑保留了原有消息历史（没有被清掉重跑）——即
 *    「在原来的工作上继续」。
 *
 * 2. AI 会话回合失败提示：会话历史里持久化的 error part（session.error /
 *    info.error 经 chat_persist 落库后的形态）在对话流里渲染成错误条。
 *    真实触发上游超时无法确定性复现，持久化侧逻辑由 server 端单测覆盖，
 *    这里验证可见性这一环。
 */

const BATCH_NAME = `e2e-stop-resume-${Date.now()}`
const INJECT = path.join(path.dirname(fileURLToPath(import.meta.url)),
                         'helpers', 'inject_error.py')

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })
}

/** 带 Bearer 的后端 API GET（走 vite 代理，/api 前缀会被剥掉）。 */
async function apiGet(page: import('@playwright/test').Page, url: string) {
  const token = await page.evaluate(() => localStorage.getItem('check-manage:token'))
  const res = await page.request.get(url, {
    headers: { Authorization: `Bearer ${JSON.parse(token!)}` },
  })
  expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy()
  return res.json()
}

/** token 存的是 JSON.stringify 后的字符串，读出来要 parse。 */
async function bearer(page: import('@playwright/test').Page) {
  const raw = await page.evaluate(() => localStorage.getItem('check-manage:token'))
  return `Bearer ${JSON.parse(raw!)}`
}

async function findBatchByName(page: import('@playwright/test').Page, name: string) {
  const list = await apiGet(page, '/api/ai/chat/batches?page=1&pageSize=50')
  const found = (list.items as Array<{ id: string; name: string }>).find(b => b.name === name)
  expect(found, `batch ${name} should exist`).toBeTruthy()
  return found!
}

async function expandBatchGroup(page: import('@playwright/test').Page, name: string,
                                childCount = 2) {
  const group = page.locator('.batch-group', { hasText: name }).first()
  await group.waitFor({ state: 'visible', timeout: 15_000 })
  // 详情 5s 轮询会重渲染列表，可能吞掉展开点击 —— 重试直到 body 出现。
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.batch-group__body').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
  await expect(group.locator('.bg-child')).toHaveCount(childCount, { timeout: 10_000 })
  return group
}

test('批任务：停止全部后可继续运行，且续跑保留原有进度', async ({ page }) => {
  test.setTimeout(420_000)
  await login(page)
  await page.goto('/ai-chat')

  // --- 创建 2 个文件的批任务（prompt 足够慢，留出停止窗口） ---
  const createBatchBtn = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .getByRole('button', { name: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  await dialog.locator('input[data-test="name"]').fill(BATCH_NAME)
  await dialog.locator('textarea[data-test="prompt"]')
    .fill('请围绕「春天的田野」写一段不少于300字的中文散文，只输出散文正文。')
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })
  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  const group = await expandBatchGroup(page, BATCH_NAME)

  // --- 等一个子任务真正开跑（running 黄点）再停 —— 保证至少一个子任务
  //     停止前已有工作进度，继续时走「原 OpenCode 会话续跑」路径。
  let sawRunning = false
  try {
    await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 60_000 })
    sawRunning = true
  } catch { /* 子任务极快完成时停止流程照常验证 */ }

  // --- 中断全部（原「停止」） ---
  await group.locator('[title^="中断全部"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  // worker 需要一个 tick 把 pending/running 翻成 cancelled，UI 5s 轮询带回
  await expect(group.locator('[title^="继续运行"]')).toBeVisible({ timeout: 60_000 })
  await expect(group.locator('.dot--running')).toHaveCount(0, { timeout: 60_000 })
  await expect(group.locator('.dot--cancelled').first()).toBeVisible({ timeout: 30_000 })

  // --- 后端视角：详情带出 cancelled 计数（继续按钮的判据）---
  const batch = await findBatchByName(page, BATCH_NAME)
  const detail = await apiGet(page, `/api/ai/chat/batches/${batch.id}`)
  expect(detail.batch.cancelled).toBeGreaterThan(0)
  const childIds: string[] = detail.sessions.map((s: { id: string }) => s.id)

  // 续跑前记录已有消息 id —— 续跑后这些行必须原样保留（历史未清）
  const preIds: Record<string, string[]> = {}
  for (const cid of childIds) {
    const msgs = await apiGet(page, `/api/ai/chat/sessions/${cid}/messages`)
    preIds[cid] = (msgs.messages as Array<{ id: string }>).map(m => m.id)
  }

  // --- 继续运行 ---
  await group.locator('[title^="继续运行"]').click()
  await page.waitForFunction((name) => {
    const groups = Array.from(document.querySelectorAll('.batch-group'))
    const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(name))
    const badge = g?.querySelector('.badge')
    return !!badge && ['badge--completed', 'badge--partial'].some(
      c => badge.classList.contains(c))
  }, BATCH_NAME, { timeout: 360_000 })

  await expect(group.locator('.dot--completed')).toHaveCount(2, { timeout: 60_000 })

  // 停止前已开跑的子任务：续跑后旧消息仍在、且新增了内容 —— 证明是在
  // 原工作上继续，而不是清空重跑（重新执行会先删光消息）。
  if (sawRunning) {
    let verified = false
    for (const cid of childIds) {
      if (!preIds[cid].length) continue
      const msgs = await apiGet(page, `/api/ai/chat/sessions/${cid}/messages`)
      const nowIds = (msgs.messages as Array<{ id: string }>).map(m => m.id)
      const preserved = preIds[cid].every(id => nowIds.includes(id))
      if (preserved && nowIds.length > preIds[cid].length) {
        verified = true
        break
      }
    }
    expect(verified,
      '续跑应在保留停止前消息历史的基础上新增内容（原工作继续）').toBeTruthy()
  }

  // --- 清理：删除批次 ---
  await group.locator('[title="删除批次"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.batch-group', { hasText: BATCH_NAME }))
    .toHaveCount(0, { timeout: 10_000 })
})

test('批任务：暂停后可继续运行（paused 不占失败计数）', async ({ page }) => {
  test.setTimeout(420_000)
  await login(page)
  await page.goto('/ai-chat')

  // --- 创建 2 文件批任务（慢 prompt，留出暂停窗口） ---
  const createBatchBtn = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .getByRole('button', { name: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  await dialog.locator('input[data-test="name"]').fill(BATCH_NAME)
  await dialog.locator('textarea[data-test="prompt"]')
    .fill('请围绕「秋天的山谷」写一段不少于300字的中文散文，只输出散文正文。')
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })
  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  const group = await expandBatchGroup(page, BATCH_NAME)

  // --- 等子任务真正开跑再暂停（走"运行中协作式暂停"路径） ---
  await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 60_000 })

  await group.locator('[title^="暂停全部"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  // worker 把运行中的回合 abort 并落成 paused（蓝点），批次徽标变「已暂停」
  await expect(group.locator('.dot--running')).toHaveCount(0, { timeout: 60_000 })
  await expect(group.locator('.dot--paused').first()).toBeVisible({ timeout: 60_000 })
  await expect(group.locator('.badge--paused')).toBeVisible({ timeout: 30_000 })

  // 后端视角：paused 计数 > 0，且暂停不占 failed 计数
  const batch = await findBatchByName(page, BATCH_NAME)
  const detail = await apiGet(page, `/api/ai/chat/batches/${batch.id}`)
  expect(detail.batch.paused).toBeGreaterThan(0)
  expect(detail.batch.failed).toBe(0)
  const childIds: string[] = detail.sessions.map((s: { id: string }) => s.id)

  // 暂停期间已有部分消息历史（被中断的回合），记录下来用于续跑对比
  const preIds: Record<string, string[]> = {}
  for (const cid of childIds) {
    const msgs = await apiGet(page, `/api/ai/chat/sessions/${cid}/messages`)
    preIds[cid] = (msgs.messages as Array<{ id: string }>).map(m => m.id)
  }

  // --- 继续运行 ---
  await group.locator('[title^="继续运行"]').click()
  await page.waitForFunction((name) => {
    const groups = Array.from(document.querySelectorAll('.batch-group'))
    const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(name))
    const badge = g?.querySelector('.badge')
    return !!badge && ['badge--completed', 'badge--partial'].some(
      c => badge.classList.contains(c))
  }, BATCH_NAME, { timeout: 360_000 })

  await expect(group.locator('.dot--completed')).toHaveCount(2, { timeout: 60_000 })
  const after = await apiGet(page, `/api/ai/chat/batches/${batch.id}`)
  expect(after.batch.failed).toBe(0)   // 全程没有失败计数

  // 续跑在原历史上进行：暂停前的消息行原样保留、其上新增内容
  let verified = false
  for (const cid of childIds) {
    if (!preIds[cid].length) continue
    const msgs = await apiGet(page, `/api/ai/chat/sessions/${cid}/messages`)
    const nowIds = (msgs.messages as Array<{ id: string }>).map(m => m.id)
    if (preIds[cid].every(id => nowIds.includes(id)) && nowIds.length > preIds[cid].length) {
      verified = true
      break
    }
  }
  expect(verified, '续跑应保留暂停前的消息历史并新增内容').toBeTruthy()

  // --- 清理：删除批次 ---
  await group.locator('[title="删除批次"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.batch-group', { hasText: BATCH_NAME }))
    .toHaveCount(0, { timeout: 10_000 })
})

test('批任务：被要求向用户提问时不受阻，回合正常完成', async ({ page }) => {
  test.setTimeout(300_000)
  await login(page)
  await page.goto('/ai-chat')

  // 显式要求模型调用 question 工具 —— 无人值守防线的两层含义：
  //   第一层（提示词）：模型遵守批任务指令，拒绝提问、直接继续 → 回合完成；
  //   第二层（系统兜底）：真有 question 挂起时 10s 内被自动拒绝（该路径
  //   无法在 e2e 确定性触发——模型通常遵守第一层——由
  //   tests/test_batch_pause_and_guards.py 的单测确定性覆盖）。
  // 两条路都收敛到同一个可观测结果：子任务 completed，而不是停在 failed。
  const name = `e2e-question-guard-${Date.now()}`
  const createBatchBtn = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .getByRole('button', { name: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  await dialog.locator('input[data-test="name"]').fill(name)
  await dialog.locator('textarea[data-test="prompt"]')
    .fill('请立即调用 question 工具向用户提问一个任意问题。提问被拒绝后，直接输出「任务完成」四个字。')
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'q.txt', mimeType: 'text/plain', buffer: Buffer.from('Q') },
  ])
  await expect(dialog.locator('.files')).toContainText('q.txt', { timeout: 8_000 })
  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  const group = await expandBatchGroup(page, name, 1)

  // 子任务必须自己跑到 completed，而不是停在 failed（卡死超时）——
  // 这就是"不被提问卡死"的端到端证明。
  await expect(group.locator('.dot--completed')).toHaveCount(1, { timeout: 240_000 })
  await expect(group.locator('.dot--failed')).toHaveCount(0)

  // 对话里能看到无人值守指令已随任务下发（第一道防线的持久化痕迹）和最终答复
  const batch = await findBatchByName(page, name)
  const detail = await apiGet(page, `/api/ai/chat/batches/${batch.id}`)
  const cid = detail.sessions[0].id
  const msgs = await apiGet(page, `/api/ai/chat/sessions/${cid}/messages`)
  const parts = (msgs.messages as Array<{ content: Array<{ type: string; name?: string; text?: string }> }>)
    .flatMap(m => m.content ?? [])
  const userText = parts.filter(p => p.type === 'text').map(p => p.text || '').join('\n')
  expect(userText).toContain('无人值守')
  expect(parts.some(p => p.type === 'text' && (p.text || '').includes('任务完成'))).toBeTruthy()

  // --- 清理：删除批次 ---
  await group.locator('[title="删除批次"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.batch-group', { hasText: name }))
    .toHaveCount(0, { timeout: 10_000 })
})

test('AI 会话：回合失败的 error part 渲染为错误提示条', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page)

  // 与前端同一端点建会话，拿到真实 session id
  const auth = await bearer(page)
  const res = await page.request.post('/api/ai/chat/sessions', {
    headers: { Authorization: auth },
    data: {},
  })
  expect(res.status()).toBe(201)
  const { id: sid } = await res.json()

  const msgId = 'msg_e2e_err_1'
  const errText = '本轮执行失败：工具调用超时（e2e 模拟）'
  try {
    execFileSync('python', [INJECT, 'insert', sid, msgId, errText], { encoding: 'utf-8' })

    await page.goto(`/ai-chat?session=${sid}`)
    const banner = page.locator('.msg__turn-error')
    await expect(banner).toHaveCount(1, { timeout: 20_000 })
    await expect(banner).toContainText(errText)
  } finally {
    // 清理：注入的消息行 + 会话本身（软删 + 工作区清理 + 杀 OpenCode 会话）
    try {
      execFileSync('python', [INJECT, 'cleanup', sid, msgId], { encoding: 'utf-8' })
    } catch { /* best-effort */ }
    try {
      await page.request.delete(`/api/ai/chat/sessions/${sid}`, {
        headers: { Authorization: auth },
      })
    } catch { /* best-effort */ }
  }
})
