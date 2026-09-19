/**
 * 变更文件 Markdown 预览渲染 E2E：
 * 变更文件面板里的 .md 点「预览」应打开 FilePreviewDialog 的排版渲染
 * （标题/表格/代码块成为 HTML），而不是把 md 源码当文本/diff 展示。
 *
 * 前置 seed（由脚本写入，测试内不重复创建）：
 *   会话 audit-chat-1789743325271（sess_cebf68ce157e）workspace 根下有
 *   未跟踪文件「预览验证报告.md」→ 变更文件面板以「新增」出现。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']
const SESSION_TITLE = 'audit-chat-1789743325271'
const MD_NAME = '预览验证报告.md'

test.beforeAll(async ({ browser }) => {
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
  if (!auth) throw new Error('shared login failed')
  fs.writeFileSync(AUTH_FILE, JSON.stringify(auth))
})

test.beforeEach(async ({ context }) => {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await context.addInitScript((entries) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

test('变更文件 md 预览走富渲染（标题/表格成为 HTML 而非源码）', async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto('/home')
  const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
  await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await aiBtn.click()
  await page.getByPlaceholder(/给 AI 助手发消息/)
    .waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1200)

  // ① 选中目标会话（seed md 所在）
  const item = page.locator('.session-item', { hasText: SESSION_TITLE }).first()
  await item.waitFor({ state: 'visible', timeout: 15_000 })
  await item.click()

  // ② 变更文件面板出现目标 md（必要时点一次重新扫描）
  const row = page.locator('.change-file__row', { hasText: MD_NAME }).first()
  try {
    await row.waitFor({ state: 'visible', timeout: 8_000 })
  } catch {
    await page.locator('.ai-changes__refresh').click()
    await row.waitFor({ state: 'visible', timeout: 15_000 })
  }

  // ③ 点「预览」→ FilePreviewDialog 打开且 Markdown 排版渲染
  await row.getByRole('button', { name: '预览' }).click()
  const dialog = page.locator('.file-preview-dialog')
  await dialog.waitFor({ state: 'visible', timeout: 15_000 })
  // 渲染断言：md 标题成为 h1、表格成为 <table>（源码态不会有这两个元素）
  await expect(dialog.locator('h1', { hasText: '变更文件渲染验证' }))
    .toBeVisible({ timeout: 15_000 })
  await expect(dialog.locator('table')).toBeVisible()
  // 兜底断言：不是纯文本 <pre>（源码态走 fp-text/代码块）
  await expect(dialog.locator('pre.fp-text')).toHaveCount(0)
})
