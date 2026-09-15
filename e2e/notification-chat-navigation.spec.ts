import { test, expect } from '@playwright/test'

test('notification opens a different chat session while AI chat is already mounted', async ({ page }) => {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })

  const sessionsResponse = page.waitForResponse(response =>
    response.url().includes('/api/ai/chat/sessions') && response.request().method() === 'GET',
  )
  await page.goto('/ai-chat')
  const sessions = await (await sessionsResponse).json()
  const ids = (sessions.sessions || sessions).map((session: { id: string }) => session.id)
  expect(ids.length).toBeGreaterThanOrEqual(2)
  const targetSession = ids[1]

  await page.goto(`/ai-chat?session=${encodeURIComponent(ids[0])}`)
  await page.locator('.ai-chat__messages').waitFor({ state: 'visible', timeout: 15_000 })
  await page.route('**/api/notifications*', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      id: 'notification-test', userId: 'admin', type: 'aiChat',
      title: 'AI 会话完成', content: '打开目标会话',
      sourceCollection: 'ai-chat', sourceRecordId: targetSession,
      isRead: true, createdAt: new Date().toISOString(),
    }]),
  }))

  await page.locator('.header-right .el-badge').click()
  await page.locator('.notification-item').click()
  await expect(page).toHaveURL(new RegExp(`[?&]session=${targetSession}`))
})
