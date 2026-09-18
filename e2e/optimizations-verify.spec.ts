/**
 * 端到端自验证：任务一（zip 中文乱码修复已由后端单测覆盖）+ 运行时检索、
 * 批任务排序/单任务继续/停止并删除、AI Chat 发送失败重试、工具调用摘要、
 * 记忆与模板管理的错误态（P0/P2 spec 验收）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'

// python 解析（Playwright 进程 PATH 可能缺 python，按候选探测）
const PYTHON = [
  process.env.PYTHON,
  'F:/llvm/anaconda3/python.exe',
  'C:/Python312/python.exe',
  'python',
].find((c) => c && (c === 'python' || fs.existsSync(c))) || 'python'

const BATCH_NAME = `e2e-opt-batch-${Date.now()}`

// ── 共享登录态：beforeAll 用 UI 登录一次并保存 storageState，所有用例复用，
// 规避登录页/初始化的偶发竞态（应用既有问题，与本仓库 e2e 其余 spec 的
// "auth race" 注释同源）。 ──
const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

test.beforeAll(async ({ browser }) => {
  // UI 登录一次，把 localStorage 的登录态持久化到 .auth/admin.json，
  // 供本文件所有用例通过 addInitScript 复用（绕开登录页/初始化竞态）。
  fs.mkdirSync(AUTH_DIR, { recursive: true })
  let auth: Record<string, string> | null = null
  for (let attempt = 0; attempt < 3 && !auth; attempt++) {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    try {
      await page.goto('/')
      await page.fill('input[placeholder*="用户名"]', 'admin')
      await page.fill('input[placeholder*="密码"]', 'admin123')
      await page.getByRole('button', { name: /登\s*录/ }).click()
      await page.waitForFunction(
        () => !!localStorage.getItem('check-manage:token')
           && !!localStorage.getItem('check-manage:userInfo'),
        undefined, { timeout: 30_000 })
      await page.waitForTimeout(1500)
      auth = await page.evaluate((keys) => {
        const out: Record<string, string> = {}
        for (const k of keys) {
          const v = localStorage.getItem(k)
          if (v) out[k] = v
        }
        return out
      }, AUTH_KEYS)
    } catch { /* retry */ }
    await ctx.close()
  }
  if (!auth || Object.keys(auth).length < AUTH_KEYS.length) {
    throw new Error('shared login failed')
  }
  fs.writeFileSync(AUTH_FILE, JSON.stringify(auth))
})

test.beforeEach(async ({ context }) => {
  // 在任何页面脚本之前注入登录态
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8')) as Record<string, string>
  await context.addInitScript((entries) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

async function gotoAiChat(page: import('@playwright/test').Page) {
  // storageState 已带登录态：整页加载 /home → 顶栏「AI 助手」SPA 内导航进入
  // /ai-chat（比整页直达 /ai-chat 更稳，规避初始化竞态导致的首页兜底）。
  let lastErr: unknown = null
  for (let i = 0; i < 3; i++) {
    try {
      await page.goto('/home')
      const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
      await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
      await aiBtn.click()
      const input = page.getByPlaceholder(/给 AI 助手发消息/)
      await input.waitFor({ state: 'visible', timeout: 30_000 })
      // 等会话自动创建的首个重渲染结束，避免瞬时的重挂载影响后续断言
      await page.waitForTimeout(1500)
      await input.waitFor({ state: 'visible', timeout: 10_000 })
      return input
    } catch (e) {
      lastErr = e
      await page.waitForTimeout(2000)
    }
  }
  throw lastErr ?? new Error('composer never appeared')
}

test('OpenCode 运行时页：技能/Agent 检索过滤', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/admin/ai-opencode')
  const skillSearch = page.locator('input[data-test="skill-search"]')
  await skillSearch.waitFor({ state: 'visible', timeout: 30_000 })

  // 搜一个必然无命中的关键词 → 空态文案变为「没有匹配的技能」
  await skillSearch.fill('zzz-no-match-zzz')
  await expect(page.locator('.el-table__empty-text')).toHaveText('没有匹配的技能', { timeout: 15_000 })
  // 清空后恢复：表格出现数据行，或空态回到「暂无技能」
  await skillSearch.fill('')
  await page.waitForTimeout(500)
  const emptyCount = await page.locator('.el-table__empty-text').count()
  if (emptyCount > 0) {
    await expect(page.locator('.el-table__empty-text')).toHaveText('暂无技能')
  }

  // Agent 页签同样具备检索
  await page.getByRole('tab', { name: /智能体/ }).click()
  const agentSearch = page.locator('input[data-test="agent-search"]')
  await agentSearch.waitFor({ state: 'visible', timeout: 10_000 })
  await agentSearch.fill('zzz-no-match-zzz')
  await expect(page.locator('.el-table__empty-text')).toHaveText('没有匹配的 Agent')
})

test('AI Chat：发送失败出现错误卡，重试后成功收到回复', async ({ page }) => {
  test.setTimeout(180_000)
  const input = await gotoAiChat(page)

  // 模拟发送接口 502（结构化错误，P0 §8/§11）
  await page.route('**/api/ai/chat/sessions/*/messages', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'OPENCODE_UNAVAILABLE',
            message: 'AI 运行时暂时不可用（e2e 模拟）',
            retryable: true,
            operation: 'send_message',
            messageId: 'msg_e2e_fail',
          },
        }),
      })
      return
    }
    await route.continue()
  })

  await input.fill('e2e 失败重试验证')
  await page.getByRole('button', { name: '发送' }).click()

  // 错误卡出现：错误原因 + 重试入口；输入区不再处于"正在思考"
  const card = page.locator('[data-test="turn-failure"]')
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card).toContainText('本轮执行失败')
  await expect(card).toContainText('AI 运行时暂时不可用（e2e 模拟）')
  await expect(card).toContainText('e2e 失败重试验证')
  await expect(page.locator('.ai-chat__pending')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试本轮' })).toBeEnabled()

  // 解除拦截后一键重试 → 正常流式回复
  await page.unroute('**/api/ai/chat/sessions/*/messages')
  await page.getByRole('button', { name: '重试本轮' }).click()
  await expect(page.locator('.msg--assistant').first()).toBeVisible({ timeout: 90_000 })
  const reply = await page.locator('.msg--assistant').first().innerText()
  expect(reply.trim().length).toBeGreaterThan(0)
  await expect(card).toHaveCount(0)
})

test('AI Chat：工具调用气泡显示可读摘要与状态文字（注入，确定性）', async ({ page }) => {
  test.setTimeout(120_000)
  const title = `tool-bubble-${Date.now()}`
  const sid = execFileSync(PYTHON,
    [path.join(process.cwd(), 'e2e', 'helpers', 'inject_toolmsg.py'), 'seed', title],
    { encoding: 'utf-8' }).trim()
  try {
    // 打开注入会话：气泡来自持久化 normalized content，渲染确定性
    await page.goto(`/ai-chat?session=${sid}`)
    const head = page.locator('.tool-call__head').first()
    await head.waitFor({ state: 'visible', timeout: 30_000 })

    const headText = await head.innerText()
    expect(headText).toMatch(/写入文件/)
    expect(headText).toContain('e2e-tool.txt')
    expect(headText).toContain('Wrote file e2e-tool.txt')
    expect(headText).toContain('已完成')
    // 折叠态是自然语言摘要，不允许是裸 JSON 输入
    const summaryText = await head.locator('.tool-call__summary').innerText()
    expect(summaryText.trim()).not.toMatch(/^[{\[]/)
    // a11y：aria-expanded 切换 + 展开后可见原始输入
    await expect(head).toHaveAttribute('aria-expanded', 'false')
    await head.click()
    await expect(head).toHaveAttribute('aria-expanded', 'true')
    await expect(page.locator('.tool-call__body').first()).toBeVisible()
    await expect(page.locator('.tool-call__body').first()).toContainText('file_path')
  } finally {
    execFileSync('python',
      [path.join(process.cwd(), 'e2e', 'helpers', 'inject_toolmsg.py'), 'cleanup', title],
      { encoding: 'utf-8' })
  }
})

test('批任务：最新会话在上、单任务独立继续、停止并删除', async ({ page }) => {
  test.setTimeout(300_000)
  await page.goto('/ai-chat')

  const createBatchBtn = page
    .locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .locator('button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()

  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  await dialog.locator('input[data-test="name"]').fill(BATCH_NAME)
  await dialog.locator('textarea[data-test="prompt"]').fill('回复收到即可，无需其他操作')
  // 串行上传：ElUpload 并发完成顺序不定，staged 顺序决定 batch_seq，
  // 必须保证 a 先 b 后，"最新会话在最上"的断言才确定。
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])
  await expect(dialog.locator('.files')).toContainText('b.txt', { timeout: 8_000 })
  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  const group = page.locator('.batch-group', { hasText: BATCH_NAME }).first()
  await group.waitFor({ state: 'visible', timeout: 10_000 })
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.batch-group__body').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
  await expect(group.locator('.bg-child')).toHaveCount(2, { timeout: 10_000 })

  // ① 排序：后创建的 b.txt 排在最上面（最新在上）
  await expect(group.locator('.bg-child').first()).toContainText('b.txt')
  await expect(group.locator('.bg-child').last()).toContainText('a.txt')

  // ② 暂停全部 → 两个子任务都落成 paused
  for (let i = 0; i < 3; i++) {
    try {
      await group.locator('[title^="暂停全部"]').click({ timeout: 3000 })
      break
    } catch { await page.waitForTimeout(800) }
  }
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(group.locator('.dot--paused')).toHaveCount(2, { timeout: 60_000 })

  // ③ 单任务继续：只恢复 b.txt，a.txt 保持 paused
  const bRow = group.locator('.bg-child', { hasText: 'b.txt' }).first()
  await bRow.hover()
  await bRow.locator('[title^="继续此任务"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  // b.txt 续跑直至完成；a.txt 不被拉起
  await expect(bRow.locator('.dot--completed')).toBeVisible({ timeout: 180_000 })
  const aRow = group.locator('.bg-child', { hasText: 'a.txt' }).first()
  await expect(aRow.locator('.dot--paused')).toBeVisible()

  // ④ 运行中（含 paused）批次不能直接删除 → 「停止并删除」
  await group.locator('[title="删除批次"]').click()
  const box = page.locator('.el-message-box')
  await expect(box).toContainText('运行中的批任务不能直接删除')
  await expect(box.locator('.el-button--primary')).toContainText('停止并删除')
  await box.locator('.el-button--primary').click()
  await expect(page.locator('.batch-group', { hasText: BATCH_NAME })).toHaveCount(0, { timeout: 30_000 })
})

test('长期记忆：加载失败显示错误态与重试（不显示为空列表）', async ({ page }) => {
  const input = await gotoAiChat(page)

  await page.route('**/api/ai/memories*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.abort('failed')
      return
    }
    await route.continue()
  })

  // 打开「我的记忆」抽屉（+ 菜单 → 我的记忆）
  await page.locator('button[aria-label="添加附件或技能"]').click()
  await page.locator('.el-dropdown-menu__item', { hasText: '我的记忆' }).click()
  const drawer = page.locator('.el-drawer', { hasText: '我的长期记忆' })
  await drawer.waitFor({ state: 'visible', timeout: 10_000 })

  const errBox = drawer.locator('[data-test="mem-error"]')
  await expect(errBox).toBeVisible({ timeout: 10_000 })
  await expect(errBox).toContainText('记忆加载失败')
  await expect(drawer).not.toContainText('暂无长期记忆')

  // 解除拦截 → 重试加载成功
  await page.unroute('**/api/ai/memories*')
  await errBox.getByRole('button', { name: '重新加载' }).click()
  await expect(drawer.locator('[data-test="mem-error"]')).toHaveCount(0, { timeout: 10_000 })
})

test('Prompt 模板：新建、检索、收藏、插入到输入框', async ({ page }) => {
  test.setTimeout(120_000)
  const input = await gotoAiChat(page)

  // 从批任务弹窗进入「管理模板」
  const createBatchBtn = page
    .locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .locator('button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })
  await dialog.getByRole('button', { name: '管理模板' }).click()

  const drawer = page.locator('.el-drawer', { hasText: '管理模板' })
  await drawer.waitFor({ state: 'visible', timeout: 10_000 })

  // 新建模板
  await drawer.getByRole('button', { name: '+ 新模板' }).click()
  const editBoxes = drawer.locator('.tpl.active input')
  await editBoxes.first().fill('e2e-opt-template')
  await drawer.locator('.tpl.active textarea').fill('E2E 模板内容：请总结以下内容')
  await drawer.locator('.tpl.active').getByRole('button', { name: '保存' }).click()
  await expect(drawer.locator('.tpl', { hasText: 'e2e-opt-template' })).toBeVisible({ timeout: 10_000 })

  // 检索：无命中 → 空态；有命中 → 只剩目标
  await drawer.locator('input[data-test="tpl-search"]').fill('no-such-tpl')
  await expect(drawer).toContainText('没有匹配的模板')
  await drawer.locator('input[data-test="tpl-search"]').fill('e2e-opt-template')
  const rows = drawer.locator('.tpl__row')
  await expect(rows.filter({ hasText: 'e2e-opt-template' })).toHaveCount(1)

  // 收藏
  const tplRow = drawer.locator('.tpl', { hasText: 'e2e-opt-template' }).first()
  await tplRow.locator('.tpl__star').click()
  await expect(tplRow.locator('.tpl__star.on')).toBeVisible()

  // 插入 → 写入主输入框（不直接发送），抽屉关闭
  await tplRow.getByRole('button', { name: '插入' }).click()
  await expect(drawer).not.toBeVisible()
  await expect(input).toHaveValue('E2E 模板内容：请总结以下内容')

  // 清理：重新打开删除模板
  await dialog.getByRole('button', { name: '管理模板' }).click()
  await drawer.waitFor({ state: 'visible', timeout: 10_000 })
  const row2 = drawer.locator('.tpl', { hasText: 'e2e-opt-template' }).first()
  await row2.getByRole('button', { name: '删除' }).click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.el-drawer', { hasText: '管理模板' }).locator('.tpl', { hasText: 'e2e-opt-template' })).toHaveCount(0, { timeout: 10_000 })
  await page.keyboard.press('Escape')
  await dialog.getByRole('button', { name: '取消' }).click()
})
