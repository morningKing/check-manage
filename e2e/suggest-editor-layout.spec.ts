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

test('长文档：chrome 精简、头部常驻、正文区内滚动；专注模式近满屏', async ({ page }) => {
  test.setTimeout(180_000)
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

  // tab 页签在编辑路由默认隐藏（meta.hideTabBar）
  await expect(page.locator('.tab-bar')).toBeHidden()

  // 顶栏与格式工具合并为单行：行高预算（title+tools+状态药丸+动作+专注 ≤ 52px）
  const topbar = page.locator('.se-topbar')
  const toolbar = page.locator('.se-toolbar')
  await expect(topbar).toBeVisible()
  await expect(toolbar).toBeVisible()
  const topbarBox = await topbar.boundingBox()
  expect(topbarBox?.height ?? 99, `topbar height=${topbarBox?.height}`).toBeLessThanOrEqual(52)

  // chrome 纵向预算：正文纸面顶边不得低于 170px（本次改造的直接目标）
  const paperTop = (await page.locator('.doc-surface').boundingBox())?.y ?? -1
  expect(paperTop, `doc-surface top=${paperTop}`).toBeGreaterThan(0)
  expect(paperTop).toBeLessThanOrEqual(170)

  // 外层容器不得出现可滚动溢出（否则滚轮会把头部挤走）
  const outerOverflow = await page.evaluate(() => {
    const main = document.querySelector('.content-main') as HTMLElement | null
    if (!main) return { ok: false, reason: 'no .content-main' }
    return { ok: true, overflow: main.scrollHeight - main.clientHeight }
  })
  expect(outerOverflow.ok).toBe(true)
  expect(outerOverflow.overflow).toBeLessThanOrEqual(1)

  // 滚动必须发生在 .se-editor-host 内：host 被视口约束（clientHeight ≈ 页面盒）且真的能滚。
  // 回归背景（2026-10-23）：.se-scope 缺高度 → height:100% 链断裂 → host 被内容撑开
  // （scrollHeight == clientHeight，无滚动条），长文档被外层 overflow:hidden 裁掉，
  // 而本用例旧断言在该坏布局下同样成立，测不出来——所以这里必须显式验证可滚。
  const scrollProbe = await page.evaluate(() => {
    const host = document.querySelector('.se-editor-host') as HTMLElement | null
    const pageBox = document.querySelector('.suggest-doc-page') as HTMLElement | null
    if (!host || !pageBox) return { ok: false as const }
    const bounded = host.clientHeight <= pageBox.clientHeight + 2
    const overflows = host.scrollHeight - host.clientHeight > 50
    const before = host.scrollTop
    host.scrollTop = 500
    const moved = host.scrollTop > before
    host.scrollTop = before
    return { ok: true as const, bounded, overflows, moved }
  })
  expect(scrollProbe.ok).toBe(true)
  expect(scrollProbe.bounded).toBe(true)
  expect(scrollProbe.overflows).toBe(true)
  expect(scrollProbe.moved).toBe(true)

  // 正文区滚动到底，头部位置不变
  const topbarBefore = await topbar.boundingBox()
  await page.locator('.se-editor-host').evaluate((el) => {
    el.scrollTop = el.scrollHeight
  })
  await page.waitForTimeout(400)
  const topbarAfter = await topbar.boundingBox()
  const yBefore = topbarBefore?.y ?? -2
  const yAfter = topbarAfter?.y ?? -1
  expect(Math.abs(yAfter - yBefore), `topbar y before=${yBefore} after=${yAfter}`)
    .toBeLessThanOrEqual(1)
  await expect(topbar).toBeVisible()

  // 选中文字：不得出现浮动格式工具栏
  await page.locator('.doc-surface p').first().click()
  await page.keyboard.press('Shift+Home')
  await page.waitForTimeout(600)
  await expect(page.locator('.bubble-toolbar, .se-bubble-toolbar')).toHaveCount(0)

  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-long-doc.png' })

  // 专注模式：平台头部/页内顶栏全部隐藏，正文顶到视口上沿；Esc 退出后恢复
  await page.locator('.se-focus-btn').click()
  await expect(page.locator('.app-header')).toBeHidden()
  await expect(page.locator('.se-head')).toBeHidden()
  await expect(page.locator('.se-focus-exit')).toBeVisible()
  const focusTop = (await page.locator('.doc-surface').boundingBox())?.y ?? -1
  expect(focusTop, `focus doc-surface top=${focusTop}`).toBeLessThanOrEqual(80)

  await page.keyboard.press('Escape')
  await expect(page.locator('.se-head')).toBeVisible()
  await expect(page.locator('.app-header')).toBeVisible()
  await expect(page.locator('.se-focus-exit')).toHaveCount(0)
  const restoredTop = (await page.locator('.doc-surface').boundingBox())?.y ?? -1
  expect(restoredTop).toBeLessThanOrEqual(170)

  // 清理
  await gotoSuggestList(page)
  const row = page.locator('.el-table__row', { hasText: title })
  await row.getByRole('button', { name: '删除' }).click()
  const confirmBox = page.locator('.el-message-box')
  await confirmBox.waitFor({ state: 'visible' })
  await confirmBox.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-table__row', { hasText: title })).toHaveCount(0, { timeout: 15_000 })
})
