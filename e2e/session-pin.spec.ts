/**
 * 会话置顶 E2E：
 *  1. 新会话重命名 → 置顶 → 出现在侧栏置顶区（.pinned-item），从未分组消失；
 *  2. 刷新页面置顶保持；
 *  3. 取消置顶 → 置顶区消失，会话回未分组；
 *  4. 分组内会话置顶 → 进置顶区、分组计数归零；取消置顶 → 回到分组。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from '@playwright/test'

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

async function gotoAiChat(page: import('@playwright/test').Page) {
  await page.goto('/home')
  const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
  await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await aiBtn.click()
  await page.getByPlaceholder(/给 AI 助手发消息/)
    .waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1200)
}


/** hover 显出的操作图标点击：行节点会因列表重排被替换、hover 丢失导致
 *  单击永远等不到可见，这里 hover+短超时点击重试。 */
async function hoverClick(
  page: import('@playwright/test').Page,
  row: import('@playwright/test').Locator,
  icon: import('@playwright/test').Locator,
) {
  for (let i = 0; i < 6; i++) {
    await row.hover()
    try {
      await icon.click({ timeout: 2500 })
      return
    } catch { /* rerender race: re-hover and retry */ }
    await page.waitForTimeout(800)
  }
  await icon.click({ timeout: 5_000 })
}

/** 新建会话并重命名为唯一标题，返回标题。 */
async function newNamedSession(page: import('@playwright/test').Page, name: string) {
  await page.locator('button', { hasText: '新建会话' }).first().click()
  await page.waitForTimeout(2000)
  const item = page.locator('.session-item:not(.pinned-item)').first()
  await item.waitFor({ state: 'visible', timeout: 15_000 })
  await hoverClick(page, item, item.locator('[data-test="session-more-btn"]'))
  await page.locator('[data-test="session-more-menu"] .el-dropdown-menu__item:visible',
    { hasText: '重命名' }).click()
  await page.locator('.el-message-box__input input').fill(name)
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.session-item', { hasText: name }).first())
    .toBeVisible({ timeout: 10_000 })
  return name
}

test('置顶：未分组置顶 → 刷新保持 → 取消回收', async ({ page }) => {
  test.setTimeout(240_000)
  await gotoAiChat(page)
  const name = await newNamedSession(page, `置顶测试${Date.now() % 100000}`)

  // ① 置顶：行内「置顶」→ 出现在置顶区，从未分组消失
  const ungroupedRow = page.locator('.session-item:not(.pinned-item)', { hasText: name }).first()
  await hoverClick(page, ungroupedRow, ungroupedRow.locator('[title="置顶"]'))
  await expect(page.locator('[data-test="pinned-group-head"]')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.session-item.pinned-item', { hasText: name })).toBeVisible()
  await expect(page.locator('.session-item:not(.pinned-item)', { hasText: name }))
    .toHaveCount(0, { timeout: 10_000 })

  // ② 刷新后置顶保持
  await page.reload()
  await page.getByPlaceholder(/给 AI 助手发消息/)
    .waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1500)
  await expect(page.locator('.session-item.pinned-item', { hasText: name }))
    .toBeVisible({ timeout: 15_000 })

  // ③ 取消置顶：置顶区消失，会话回未分组
  const pinnedRow = page.locator('.session-item.pinned-item', { hasText: name }).first()
  await hoverClick(page, pinnedRow, pinnedRow.locator('[title="取消置顶"]'))
  await expect(page.locator('[data-test="pinned-group-head"]')).toHaveCount(0, { timeout: 10_000 })
  await expect(page.locator('.session-item:not(.pinned-item)', { hasText: name }).first())
    .toBeVisible({ timeout: 10_000 })
})

test('置顶与分组：分组会话置顶进置顶区，取消后回分组', async ({ page }) => {
  test.setTimeout(240_000)
  await gotoAiChat(page)
  const name = await newNamedSession(page, `置顶分组${Date.now() % 100000}`)

  // ① 新建分组并移入
  await page.locator('[data-test="new-group-btn"]').click()
  const dialog = page.locator('[data-test="group-dialog"]')
  await dialog.waitFor({ state: 'visible', timeout: 10_000 })
  const groupName = `置顶组${Date.now() % 100000}`
  await page.locator('[data-test="group-name-input"]').fill(groupName)
  await page.locator('[data-test="group-submit"]').click()
  const groupHead = page.locator('[data-test="custom-group-head"]', { hasText: groupName })
  await expect(groupHead).toBeVisible({ timeout: 10_000 })
  const row = page.locator('.session-item:not(.pinned-item)', { hasText: name }).first()
  await hoverClick(page, row, row.locator('[data-test="session-more-btn"]'))
  await page.locator('[data-test="session-more-menu"] .el-dropdown-menu__item:visible',
    { hasText: groupName }).click()
  await expect(groupHead.locator('..').locator('.cgroup__item').first())
    .toBeVisible({ timeout: 10_000 })

  // ② 分组内会话置顶 → 进置顶区，分组计数归零（组内行消失）
  const inGroup = page.locator('.cgroup__item', { hasText: name }).first()
  await hoverClick(page, inGroup, inGroup.locator('[title="置顶"]'))
  await expect(page.locator('.session-item.pinned-item', { hasText: name }))
    .toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.cgroup__item', { hasText: name })).toHaveCount(0)

  // ③ 取消置顶 → 回到原分组
  const pinnedRow = page.locator('.session-item.pinned-item', { hasText: name }).first()
  await hoverClick(page, pinnedRow, pinnedRow.locator('[title="取消置顶"]'))
  await expect(page.locator('.cgroup__item', { hasText: name }).first())
    .toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.session-item.pinned-item', { hasText: name })).toHaveCount(0)

  // 清理：删组（会话回未分组，不删会话）
  await hoverClick(page, groupHead, groupHead.locator('[title^="删除分组"]'))
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(groupHead).toHaveCount(0, { timeout: 10_000 })
})
