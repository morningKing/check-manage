import { test, expect } from '@playwright/test'

test('AI chat M1 smoke: open drawer, send message, receive streamed reply', async ({ page }) => {
  await page.goto('/')

  // Log in
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()

  // Land on home, open AI drawer
  await page.getByRole('button', { name: /AI 助手/ }).click()

  // First-open auto-creates a session; wait for input to be enabled
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 15_000 })

  // Send "hello"
  await input.fill('hello')
  await page.getByRole('button', { name: '发送' }).click()

  // User echo。2026-11-07：首开自动建会话 POST 在当前环境实测 4-11.6s
  // （双侧对照：merge 前后同签名失败、API 直发 202/0.3s——浏览器侧首开
  // 请求风暴所致，与后端无关），默认 5s 窗口过敏。放宽到 20s，保留
  // 功能性断言（回显必须出现，只是容忍首开时延）。
  await expect(page.locator('.msg--user').last()).toContainText('hello', { timeout: 20_000 })

  // Wait for an assistant reply (at least one .msg--assistant with non-empty content)
  // 夜间模型延迟实测 80s+（2026-09-28：hello 回复 12:59:21 发出、13:00:43 落库）
  await expect(page.locator('.msg--assistant').first()).toBeVisible({ timeout: 180_000 })
  const replyText = await page.locator('.msg--assistant').first().innerText()
  expect(replyText.trim().length).toBeGreaterThan(0)
})
