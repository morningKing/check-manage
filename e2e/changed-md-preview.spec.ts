/**
 * 变更文件 Markdown 预览 E2E：
 * 变更文件面板里的 .md 点「预览」打开 diff 抽屉，默认「渲染预览」模式
 * （标题/表格/代码块成为 HTML），抽屉头可切「diff 对照」看源码 diff，
 * 再切回渲染。双模式切换由 [data-test="diff-mode-toggle"] 承载。
 *
 * 自建种子（缺口补齐）：不再依赖共享会话 audit-chat-*（会被其他用例改名/
 * 清理）——测试内通过 API 自建会话 + 直接写入 workspace 的未跟踪 md 文件，
 * 变更文件面板以「新增」出现；用后即删。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']
const MD_NAME = '预览验证报告.md'
const H1_TEXT = '变更文件渲染验证'

const MD_CONTENT = [
  `# ${H1_TEXT}`,
  '',
  '| 列A | 列B |',
  '| --- | --- |',
  '| 1 | 2 |',
  '',
  '- 项目一',
  '- 项目二',
  '',
].join('\n')

test.beforeEach(async ({ context }) => {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await context.addInitScript((entries) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

test('变更文件 md 预览走富渲染（标题/表格成为 HTML 而非源码）', async ({ page, request }) => {
  test.setTimeout(180_000)
  const tokenRes = await request.post('/api/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  const token = (await tokenRes.json()).token as string
  const authHdr = { Authorization: `Bearer ${token}` }

  // 自建种子会话 + workspace 未跟踪 md（自建，不依赖共享会话）
  const created = await request.post('/api/ai/chat/sessions', {
    headers: authHdr,
    data: { title: `changed-md-seed-${Date.now()}` },
  })
  const sid = (await created.json()).id as string
  const wsRoot = path.join(os.homedir(), '.check-manage', 'ai-workspaces',
    'user-admin', sid)
  fs.mkdirSync(wsRoot, { recursive: true })
  fs.writeFileSync(path.join(wsRoot, MD_NAME), MD_CONTENT, 'utf-8')

  await page.goto('/home')
  const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
  await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await aiBtn.click()
  await page.getByPlaceholder(/给 AI 助手发消息/)
    .waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1200)

  // ① 打开变更文件面板（新会话含未跟踪 md → 扫描后以「新增」出现）
  const row = page.locator('.change-file__row', { hasText: MD_NAME }).first()
  try {
    await row.waitFor({ state: 'visible', timeout: 8_000 })
  } catch {
    await page.locator('.ai-changes__refresh').click()
    await row.waitFor({ state: 'visible', timeout: 15_000 })
  }

  // ② 点「预览」→ diff 抽屉打开，默认「渲染预览」模式且 Markdown 排版渲染
  await row.getByRole('button', { name: '预览' }).click()
  const drawer = page.locator('.el-drawer').filter({ hasText: MD_NAME })
  await drawer.waitFor({ state: 'visible', timeout: 15_000 })
  const body = drawer.locator('.preview-body')
  // 渲染断言：md 标题成为 h1、表格成为 <table>（源码态不会有这两个元素）
  await expect(body.locator('h1', { hasText: H1_TEXT }))
    .toBeVisible({ timeout: 15_000 })
  await expect(body.locator('table')).toBeVisible()

  // ③ 切到「diff 对照」→ FileDiffView 源码态（渲染的 h1 消失）
  await drawer.locator('[data-test="diff-mode-toggle"]').getByText('diff 对照').click()
  await expect(body.locator('.file-diff')).toBeVisible({ timeout: 10_000 })
  await expect(body.locator('h1', { hasText: H1_TEXT })).toHaveCount(0)

  // ④ 切回「渲染预览」→ 排版恢复
  await drawer.locator('[data-test="diff-mode-toggle"]').getByText('渲染预览').click()
  await expect(body.locator('h1', { hasText: H1_TEXT }))
    .toBeVisible({ timeout: 10_000 })
  await expect(body.locator('table')).toBeVisible()

  // 清理：删除自建会话（软删 + workspace 残留可接受）
  await request.delete(`/api/ai/chat/sessions/${sid}`, { headers: authHdr })
})
