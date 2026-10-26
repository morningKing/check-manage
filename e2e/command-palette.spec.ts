import { test, expect, type Page } from '@playwright/test'

// 命令面板（菜单搜索，Ctrl+K）回归：
// AI 会话页曾有页面级 Ctrl+K 监听（聚焦会话搜索框）与全局命令面板抢键——
// 弹窗开着时二次 Ctrl+K 会把焦点拽进会话搜索框，且 Esc 只绑在面板输入框上
// 导致焦点在外时弹窗关不掉。修复后 Ctrl+K 归面板所有，面板打开期间全局接管。

async function login(page: Page): Promise<void> {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).not.toHaveURL(/login/)
}

test('AI 会话页 Ctrl+K 唤起面板：二次按键不丢焦点，Esc 焦点在外也能关', async ({ page }) => {
  const paletteOverlay = page.locator('.command-palette-overlay')
  const paletteInput = page.locator('.command-palette .search-input')
  const sessionSearch = page.locator('input[placeholder*="搜索会话"]')

  await login(page)
  await page.goto('/ai-chat')
  await expect(sessionSearch).toBeVisible()

  // Ctrl+K 打开命令面板，焦点落在面板输入框。
  // 首按带重试：AppLayout 的快捷键监听在异步初始化后注册（修复前），
  // 首屏刚可见时按键可能落空——这里容忍「开面板」的冷启动延迟，
  // 后续二次按键/输入/Esc 断言保持严格。
  await expect(async () => {
    await page.keyboard.press('Control+k')
    await expect(paletteOverlay).toBeVisible({ timeout: 1_000 })
  }).toPass({ timeout: 15_000 })
  await expect(paletteInput).toBeFocused()

  // 二次 Ctrl+K：面板保持打开、焦点仍在面板输入框（不再被会话搜索框抢走）
  await page.keyboard.press('Control+k')
  await expect(paletteOverlay).toBeVisible()
  await expect(paletteInput).toBeFocused()
  await expect(sessionSearch).not.toBeFocused()

  // 输入进入面板搜索框，而非会话搜索框
  await page.keyboard.type('会话')
  await expect(paletteInput).toHaveValue('会话')
  await expect(sessionSearch).toHaveValue('')

  // 焦点在面板内时 Esc 关闭
  await page.keyboard.press('Escape')
  await expect(paletteOverlay).toBeHidden()
})

test('面板打开后焦点漂移（外部聚焦）Esc 仍能关闭，且不误触底层输入', async ({ page }) => {
  const paletteOverlay = page.locator('.command-palette-overlay')
  const paletteInput = page.locator('.command-palette .search-input')
  const sessionSearch = page.locator('input[placeholder*="搜索会话"]')

  await login(page)
  await page.goto('/ai-chat')
  await expect(sessionSearch).toBeVisible()

  // 首按带重试（同上，容忍修复前监听器注册晚于首屏可见的冷启动竞态）
  await expect(async () => {
    await page.keyboard.press('Control+k')
    await expect(paletteOverlay).toBeVisible({ timeout: 1_000 })
  }).toPass({ timeout: 15_000 })
  await expect(paletteInput).toBeFocused()

  // 模拟焦点漂移：把焦点移到会话搜索框（修复前 Esc 只绑在面板输入框上，此处关不掉）
  await sessionSearch.focus()
  await expect(sessionSearch).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(paletteOverlay).toBeHidden()
  // 面板关闭时的清空逻辑不波及外部输入框
  await expect(sessionSearch).toHaveValue('')
})
