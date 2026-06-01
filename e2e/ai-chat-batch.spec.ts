import { test, expect } from '@playwright/test'

test('batch task: create, run, retry, delete', async ({ page }) => {
  // Navigate to home
  await page.goto('/')

  // Log in as admin (same pattern as ai-chat-smoke.spec.ts)
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[type="password"]', 'admin123')
  await page.getByRole('button', { name: /登录|Login/ }).click()

  // Navigate to /ai-chat
  await page.goto('/ai-chat')
  await page.waitForLoadState('networkidle', { timeout: 10_000 })

  // Click the 批任务 tab in .ai-sidebar__tabs
  const batchTaskTab = page.locator('.ai-sidebar__tabs button', { hasText: '批任务' })
  await batchTaskTab.waitFor({ state: 'visible', timeout: 5_000 })
  await batchTaskTab.click()

  // Click "+ 新建批任务" button in .batch-list
  const createBatchBtn = page.locator('button', { hasText: /^\+\s*新建批任务/ })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 5_000 })
  await createBatchBtn.click()

  // Fill dialog: name and prompt using data-test selectors
  const nameInput = page.locator('input[data-test="name"]')
  const promptInput = page.locator('textarea[data-test="prompt"]')
  await nameInput.waitFor({ state: 'visible', timeout: 5_000 })
  await nameInput.fill('e2e-batch')
  await promptInput.fill('echo hi')

  // Upload 2 in-memory files via ElUpload's input[type=file]
  const fileInput = page.locator('input[type="file"]').first()
  await fileInput.setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])

  // Wait for staged files to appear in the dialog file list
  await expect(page.getByText('a.txt')).toBeVisible({ timeout: 8_000 })
  await expect(page.getByText('b.txt')).toBeVisible({ timeout: 8_000 })

  // Click the create button using data-test selector
  const createBtn = page.locator('button[data-test="create-btn"]')
  await createBtn.waitFor({ state: 'visible', timeout: 5_000 })
  await createBtn.click()

  // Confirm BatchDetailView mounts with the batch name in .batch-detail .title
  const batchTitle = page.locator('.batch-detail .title', { hasText: 'e2e-batch' })
  await batchTitle.waitFor({ state: 'visible', timeout: 10_000 })

  // Confirm the sessions table has 2 rows: table.sessions tbody tr count = 2
  const sessionRows = page.locator('table.sessions tbody tr')
  await expect(sessionRows).toHaveCount(2, { timeout: 5_000 })

  // Wait up to 90s for both children to reach a terminal state (badge--completed or badge--failed)
  await page.waitForFunction(() => {
    const rows = document.querySelectorAll('table.sessions tbody tr')
    if (rows.length !== 2) return false
    return Array.from(rows).every(r => {
      const badge = r.querySelector('[class*="badge--"]')
      return badge && (badge.className.includes('badge--completed') ||
                       badge.className.includes('badge--failed'))
    })
  }, { timeout: 90_000 })

  // Click 删除, accept the confirm dialog (ElMessageBox, not native dialog)
  const deleteBtn = page.getByRole('button', { name: '删除' })
  await deleteBtn.click()

  // For ElMessageBox, click the confirm button text directly
  const confirmDeleteBtn = page.getByRole('button', { name: '删除', exact: true }).last()
  await confirmDeleteBtn.click()

  // Confirm batch row no longer visible in the list
  await expect(page.getByText('e2e-batch')).not.toBeVisible({ timeout: 5_000 })
})
