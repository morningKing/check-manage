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

  // User echo
  await expect(page.locator('.msg--user').last()).toContainText('hello')

  // Wait for an assistant reply (at least one .msg--assistant with non-empty content)
  await expect(page.locator('.msg--assistant').first()).toBeVisible({ timeout: 60_000 })
  const replyText = await page.locator('.msg--assistant').first().innerText()
  expect(replyText.trim().length).toBeGreaterThan(0)
})
