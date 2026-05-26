import { test, expect } from '@playwright/test'

test('AI chat M1 smoke: open drawer, send message, receive streamed reply', async ({ page }) => {
  await page.goto('/')

  // Log in
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[type="password"]', 'admin123')
  await page.getByRole('button', { name: /登录|Login/ }).click()

  // Land on home, open AI drawer
  await page.getByRole('button', { name: /AI 助手/ }).click()

  // First-open auto-creates a session; wait for input to be enabled
  const input = page.getByPlaceholder(/询问 Agent/)
  await input.waitFor({ state: 'visible', timeout: 15_000 })

  // Send "hello"
  await input.fill('hello')
  await page.getByRole('button', { name: '发送' }).click()

  // User echo
  await expect(page.locator('.ai-message--user').last()).toContainText('hello')

  // Wait for an assistant reply (at least one .ai-message--assistant with non-empty content)
  await expect(page.locator('.ai-message--assistant').first()).toBeVisible({ timeout: 60_000 })
  const replyText = await page.locator('.ai-message--assistant').first().innerText()
  expect(replyText.trim().length).toBeGreaterThan(0)
})
