import { test, expect } from '@playwright/test'

const BASE = 'http://127.0.0.1:5173'

test.describe('AI Session Admin - 轨迹分析按钮', () => {

  test.beforeEach(async ({ page }) => {
    // Login as admin. Element Plus 渲染的登录按钮文案是「登 录」（含空格）
    // 且 type="button"，用 /登\s*录/ 正则匹配（与其它 spec 的登录方式一致）。
    await page.goto(`${BASE}/login`)
    await page.fill('input[placeholder*="用户名"], input[type="text"]', 'admin')
    await page.fill('input[placeholder*="密码"], input[type="password"]', 'admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    // 登录完成 = 登录按钮消失（SPA 跳转无 networkidle 可等）
    await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 15_000 })
  })

  /** 打开表格第一行的「操作」下拉（操作列已重构为 el-dropdown）。无行时返回 null。 */
  async function openFirstRowDropdown(page: import('@playwright/test').Page) {
    await page.goto(`${BASE}/admin/ai-sessions`)
    // SSE/轮询使 networkidle 永不出现，等真实元素
    await page.waitForSelector('.el-table', { timeout: 20_000 })
    const row = page.locator('.el-table__row').first()
    if ((await row.count()) === 0) return null
    await row.locator('button:has-text("操作")').click()
    // dropdown popper 挂在 body 下
    const menu = page.locator('.el-dropdown-menu:visible').first()
    await menu.waitFor({ state: 'visible', timeout: 5_000 })
    return menu
  }

  test('会话管理页面显示分析按钮', async ({ page }) => {
    const menu = await openFirstRowDropdown(page)
    if (!menu) {
      console.log('No sessions found, skipping visibility check')
      test.skip()
      return
    }
    // 操作下拉里必须有「轨迹分析」入口
    await expect(menu.locator('.el-dropdown-menu__item:has-text("轨迹分析")')).toBeVisible()
    await page.screenshot({ path: 'e2e/screenshots/session-admin-list.png', fullPage: true })
  })

  test('点击分析按钮触发轨迹分析', async ({ page }) => {
    const menu = await openFirstRowDropdown(page)
    if (!menu) {
      console.log('No sessions found, skipping click test')
      test.skip()
      return
    }

    // Listen for the new tab that will open (window.open(..., '_blank'))
    const newTabPromise = page.waitForEvent('popup', { timeout: 15_000 })

    await menu.locator('.el-dropdown-menu__item:has-text("轨迹分析")').click()

    // Wait for the success message
    const successMsg = page.locator('.el-message--success')
    await expect(successMsg).toBeVisible({ timeout: 15_000 })

    await page.screenshot({ path: 'e2e/screenshots/analyze-triggered.png' })

    // Verify new tab was opened (指向 AI 助手并带上分析会话 id)
    const newTab = await newTabPromise
    console.log(`New tab opened: ${newTab.url()}`)
    await newTab.close()
  })

  test('详情抽屉显示轨迹分析按钮', async ({ page }) => {
    const menu = await openFirstRowDropdown(page)
    if (!menu) {
      console.log('No sessions found, skipping detail test')
      test.skip()
      return
    }

    await menu.locator('.el-dropdown-menu__item:has-text("详情")').click()

    // Wait for the drawer to appear
    await page.waitForSelector('.el-drawer', { timeout: 10_000 })

    // Check that the "轨迹分析" button exists in the drawer
    const drawerAnalyzeBtn = page.locator('.el-drawer button:has-text("轨迹分析")')
    await expect(drawerAnalyzeBtn).toBeVisible({ timeout: 10_000 })

    await page.screenshot({ path: 'e2e/screenshots/session-detail-drawer.png' })
    console.log('Analyze button is visible in the detail drawer')
  })
})
