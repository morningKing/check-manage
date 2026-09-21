/**
 * 动作账本与到位门禁 E2E(设计:docs/design/AI子任务动作账本与到位门禁设计.md)。
 *
 * 覆盖:
 * 1. 对话框门禁区块 UI(启用/加行/不完整期望阻断提交——纯 UI,不烧 LLM)
 * 2. 真实批任务 + gate fail:哨兵正则必然无命中 → 子任务 failed 带
 *    action_gate 明细 + 侧栏「门禁 ×1」徽标
 * 3. 真实批任务 + gate pass:db_record 效果断言指向既有 products 数据
 *    (与模型行为无关,确定性通过)→ completed + 「门禁 ✓」徽标
 *
 * 约定:AITEST- 前缀;截图留证 e2e/screenshots/ai-full/;批任务用后即删。
 * 注意:/ai-chat 有常驻 SSE,严禁等待 networkidle(等待真实元素代替)。
 */
import { test, expect, type Page } from '@playwright/test'
import {
  api, screenshot, stagingUpload, tag, waitFor,
} from './helpers'

test.setTimeout(600_000)

/** 真实登录(表单),再进 /ai-chat;等真实元素,绝不等 networkidle(SSE 常驻) */
async function gotoChat(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    // 首访时 vite 冷编译可能让登录表单晚出现——显式等它,而不是 count() 瞬断
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.getByRole('button', { name: /登\s*录/ })
      .waitFor({ state: 'hidden', timeout: 15_000 })
  } catch {
    // 已是登录态(首页无登录表单)——直接继续
  }
  await page.goto('/ai-chat')
  const sidebar = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
  for (let i = 0; i < 3; i++) {
    try {
      await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
      return
    } catch {
      await page.goto('/ai-chat')  // vite 依赖重优化会触发整页重载,重进一次
    }
  }
  await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
}

async function openCreateDialog(page: Page) {
  const createBatchBtn = page
    .locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .locator('button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  return dialog
}

/** 等批任务终态,返回 {status, sessions} */
async function waitBatchTerminal(request: any, batchId: string) {
  return waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/batches/${batchId}`)
    const st = r.json?.batch?.status
    return ['completed', 'partial', 'failed'].includes(st)
      ? { status: st, sessions: r.json.sessions || [] } : null
  }, { timeoutMs: 480_000, intervalMs: 5000 })
}

async function expandGroup(page: Page, group: any) {
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.batch-group__body').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
}

test('对话框动作门禁区块:不完整期望阻断提交(纯 UI)', async ({ page }) => {
  await gotoChat(page)
  const dialog = await openCreateDialog(page)
  await dialog.locator('input[data-test="name"]').fill('AITEST-gate-ui')
  await dialog.locator('textarea[data-test="prompt"]').fill('只回复:OK')
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })

  // 门禁区块:启用后出现 AI 提炼按钮;加一行期望(data-test 在 label 上,
  // 原生 input 被 el-checkbox 隐藏,要点可见的样式块)
  await dialog.locator('label[data-test="gate-enabled"] .el-checkbox__inner').click()
  await expect(dialog.locator('[data-test="gate-extract"]')).toBeVisible()
  await dialog.locator('[data-test="gate-add"]').click()
  await expect(dialog.locator('.gate-row')).toHaveCount(1)

  // 不完整的期望(空名称/空正则)必须阻断提交,且不产生批任务
  await dialog.locator('button[data-test="create-btn"]').click()
  await expect(page.locator('.el-message').first())
    .toContainText('动作门禁', { timeout: 5_000 })
  await expect(dialog).toBeVisible()
  await expect(page.locator('.batch-group', { hasText: 'AITEST-gate-ui' }))
    .toHaveCount(0)
})

test('真实批任务 gate fail:哨兵动作无命中 → failed + action_gate 明细 + 门禁徽标', async ({ page, request }) => {
  const name = tag('gate-fail')
  const sentinel = `e2e-gate-sentinel-${Date.now()}`
  const staged = await stagingUpload(request, `e2e-${Date.now()}`, [
    { name: 'one.txt', body: 'BATCH-MARK-ONE' },
  ])
  const created = await api(request, 'POST', '/ai/chat/batches', {
    name,
    prompt: '直接回复:OK。不要读取任何文件,不要执行任何命令。',
    files: staged,
    action_checks: [
      { name: '读取哨兵文件', tool: 'read',
        args_pattern: `${sentinel}\\.md`, min_count: 1 },
    ],
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const batchId = created.json.batchId || created.json.batch?.id
  expect(batchId).toBeTruthy()

  const final = await waitBatchTerminal(request, batchId)
  expect(final.status).toBe('failed')  // 哨兵正则必然无命中 → 门禁不过
  const child = final.sessions[0]
  expect(child.status).toBe('failed')
  expect(child.error_message).toContain('action_gate')
  expect(child.error_message).toContain('读取哨兵文件')
  expect(child.gate_failed).toBeGreaterThanOrEqual(1)

  // 账本查询端点:该子会话树可查(至少无异常;可能无工具调用)
  const calls = await api(request, 'GET',
    `/ai/chat/batches/${batchId}/children/${child.id}/tool-calls`)
  expect(calls.status).toBe(200)
  expect(Array.isArray(calls.json?.calls)).toBe(true)

  // UI:侧栏子会话行出现「门禁 ×1」失败徽标
  await gotoChat(page)
  const group = page.locator('.batch-group', { hasText: name }).first()
  await group.waitFor({ state: 'visible', timeout: 30_000 })
  await expandGroup(page, group)
  await expect(group.locator('.gate-badge--fail')).toContainText('门禁 ×1',
    { timeout: 60_000 })
  await screenshot(page, 'action-gate-fail-badge')

  const del = await api(request, 'DELETE', `/ai/chat/batches/${batchId}`)
  expect([200, 204]).toContain(del.status)
})

test('真实多子代理会话:tree 作用域计入子代理动作 → completed + 门禁✓徽标', async ({ page, request }) => {
  const name = tag('gate-subagent')
  const agents = await api(request, 'GET', '/ai/chat/agents')
  const subs = (agents.json?.subagents || []).map((s: any) => s.name)
  test.skip(subs.length < 2, '环境需要至少两个子代理')

  const staged = await stagingUpload(request, `e2e-${Date.now()}`, [
    { name: 'one.txt', body: `GATE-SUBAGENT-MARK-${Date.now()}` },
  ])
  // 双子代理委托:让两个子代理都实际动工具(读同一个输入文件),门禁用 tree
  // 作用域在根会话上核对——子代理的动作必须被计入,这正是 tree 的设计目的
  const created = await api(request, 'POST', '/ai/chat/batches', {
    name,
    prompt: (
      '请分别委托两个子代理完成任务,不要自己动手:\n'
      + `1. @${subs[0]} 读取工作区 uploads/one.txt,原样回复文件内容;\n`
      + `2. @${subs[1]} 查看工作区 uploads/ 目录下有哪些文件,回复文件名列表。\n`
      + '两个子代理都完成后,汇总它们的结果回复。'
    ),
    files: staged,
    action_checks: [
      { name: '子代理读取输入文件', tool: 'read',
        args_pattern: 'one\\.txt', scope: 'tree', min_count: 1 },
    ],
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const batchId = created.json.batchId || created.json.batch?.id

  const final = await waitBatchTerminal(request, batchId)
  expect(final.status).toBe('completed')
  const child = final.sessions[0]
  expect(child.status).toBe('completed')
  expect(child.gate_passed).toBeGreaterThanOrEqual(1)

  // tree 作用域的实锤:账本里必须有子代理(非根会话)自己的工具调用
  const calls = await api(request, 'GET',
    `/ai/chat/batches/${batchId}/children/${child.id}/tool-calls`)
  expect(calls.status).toBe(200)
  const subCalls = (calls.json?.calls || []).filter((c: any) => c.subtaskId)
  expect(subCalls.length, '应有子代理会话的工具调用入账').toBeGreaterThan(0)

  // UI:门禁 ✓ 徽标出现在子会话行
  await gotoChat(page)
  const group = page.locator('.batch-group', { hasText: name }).first()
  await group.waitFor({ state: 'visible', timeout: 30_000 })
  await expandGroup(page, group)
  await expect(group.locator('.gate-badge--pass')).toContainText('门禁 ✓',
    { timeout: 60_000 })
  await screenshot(page, 'action-gate-multi-subagent-pass')

  const del = await api(request, 'DELETE', `/ai/chat/batches/${batchId}`)
  expect([200, 204]).toContain(del.status)
})

test('真实批任务 gate pass:db_record 效果断言(确定性) → completed + 门禁✓徽标', async ({ page, request }) => {
  const name = tag('gate-pass')
  const staged = await stagingUpload(request, `e2e-${Date.now()}`, [
    { name: 'one.txt', body: 'BATCH-MARK-ONE' },
  ])
  // db_record 断言指向既有 products 数据,与模型行为无关 → 确定性通过
  const created = await api(request, 'POST', '/ai/chat/batches', {
    name,
    prompt: '直接回复:OK。不要读取任何文件,不要执行任何命令。',
    files: staged,
    action_checks: [
      { name: '产品数据存在', check_type: 'db_record',
        effect_spec: { collection: 'products', filter: {} }, min_count: 1 },
    ],
  })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const batchId = created.json.batchId || created.json.batch?.id

  const final = await waitBatchTerminal(request, batchId)
  expect(final.status).toBe('completed')
  const child = final.sessions[0]
  expect(child.status).toBe('completed')
  expect(child.gate_passed).toBeGreaterThanOrEqual(1)

  // UI:门禁 ✓ 徽标
  await gotoChat(page)
  const group = page.locator('.batch-group', { hasText: name }).first()
  await group.waitFor({ state: 'visible', timeout: 30_000 })
  await expandGroup(page, group)
  await expect(group.locator('.gate-badge--pass')).toContainText('门禁 ✓',
    { timeout: 60_000 })
  await screenshot(page, 'action-gate-pass-badge')

  const del = await api(request, 'DELETE', `/ai/chat/batches/${batchId}`)
  expect([200, 204]).toContain(del.status)
})
