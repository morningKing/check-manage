/**
 * 智能写作 · 布局回归：
 *  1) 长文档下，编辑器头部（标题栏 + 格式工具栏）必须始终可见——
 *     滚动只发生在正文区，外层容器不得出现滚动把头部挤走；
 *  2) 选中文字不再有浮动工具栏遮挡正文（格式入口统一在头部）。
 * 前置同 suggest-editor.spec.ts（mock 栈）；本用例不依赖建议引擎。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

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
      await page.waitForTimeout(1200)
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

async function gotoSuggestList(page: Page) {
  await page.goto('/home')
  const menuItem = page.locator('.el-menu-item', { hasText: '智能写作' }).first()
  await menuItem.waitFor({ state: 'visible', timeout: 30_000 })
  await menuItem.click()
  await expect(page.getByRole('button', { name: '新建文档' })).toBeVisible({ timeout: 15_000 })
}

test('长文档：头部常驻、正文区内滚动；选中无浮动工具栏遮挡', async ({ page }) => {
  test.setTimeout(120_000)
  const title = `E2E-布局-${Date.now()}`

  await gotoSuggestList(page)
  await page.getByRole('button', { name: '新建文档' }).click()
  const dialog = page.locator('.el-dialog', { hasText: '新建文档' })
  await dialog.waitFor({ state: 'visible' })
  await dialog.getByPlaceholder('未命名文档').fill(title)
  await dialog.getByRole('button', { name: '创建' }).click()
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  // 开调试钩子需要带 ?debug=1——先重载带上参数，再注入长正文
  await page.goto(`${page.url().split('?')[0]}?debug=1`)
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => Boolean((window as any).__editor), undefined, { timeout: 20_000 })

  const paras = Array.from({ length: 60 }, (_, i) => `<p>第 ${i + 1} 段：长文档滚动与头部常驻的布局验证文本。</p>`).join('')
  await page.evaluate((html) => {
    (window as any).__editor.commands.setContent(html)
  }, paras)
  await page.waitForTimeout(800)

  const topbar = page.locator('.se-topbar')
  const toolbar = page.locator('.se-toolbar')
  await expect(topbar).toBeVisible()
  await expect(toolbar).toBeVisible()
  const toolbarBefore = await toolbar.boundingBox()

  // 工具栏必须与纸张「中心对齐」——占位镜像正文区宽度（此前按整页居中错位半栏）
  const align = await page.evaluate(() => {
    const inner = document.querySelector('.se-toolbar-inner') as HTMLElement | null
    const paper = document.querySelector('.doc-surface') as HTMLElement | null
    if (!inner || !paper) return null
    const a = inner.getBoundingClientRect()
    const b = paper.getBoundingClientRect()
    return {
      toolbarCenter: a.left + a.width / 2,
      paperCenter: b.left + b.width / 2,
      toolbarWidth: a.width,
      paperWidth: b.width,
    }
  })
  expect(align).not.toBeNull()
  expect(Math.abs((align as { toolbarCenter: number }).toolbarCenter
    - (align as { paperCenter: number }).paperCenter)).toBeLessThanOrEqual(2)
  expect((align as { toolbarWidth: number }).toolbarWidth)
    .toBeCloseTo((align as { paperWidth: number }).paperWidth, -1)

  // 外层容器不得出现可滚动溢出（否则滚轮会把头部挤走）
  const outerOverflow = await page.evaluate(() => {
    const main = document.querySelector('.content-main') as HTMLElement | null
    if (!main) return { ok: false, reason: 'no .content-main' }
    return { ok: true, overflow: main.scrollHeight - main.clientHeight }
  })
  expect(outerOverflow.ok).toBe(true)
  expect(outerOverflow.overflow).toBeLessThanOrEqual(1)

  // 正文区滚动到底，头部位置不变
  await page.locator('.se-editor-host').evaluate((el) => {
    el.scrollTop = el.scrollHeight
  })
  await page.waitForTimeout(400)
  const toolbarAfter = await toolbar.boundingBox()
  expect(Math.abs((toolbarAfter?.y ?? -1) - (toolbarBefore?.y ?? -2))).toBeLessThanOrEqual(1)
  await expect(toolbar).toBeVisible()

  // 选中文字：不得出现浮动格式工具栏
  await page.locator('.doc-surface p').first().click()
  await page.keyboard.press('Shift+Home')
  await page.waitForTimeout(600)
  await expect(page.locator('.bubble-toolbar, .se-bubble-toolbar')).toHaveCount(0)

  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-long-doc.png' })

  // 清理
  await gotoSuggestList(page)
  const row = page.locator('.el-table__row', { hasText: title })
  await row.getByRole('button', { name: '删除' }).click()
  const confirmBox = page.locator('.el-message-box')
  await confirmBox.waitFor({ state: 'visible' })
  await confirmBox.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-table__row', { hasText: title })).toHaveCount(0, { timeout: 15_000 })
})
