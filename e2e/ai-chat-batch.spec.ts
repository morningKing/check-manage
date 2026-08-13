import { test, expect } from '@playwright/test'

test('batch task: create, run, retry, delete', async ({ page }) => {
  test.setTimeout(180_000)
  // Navigate to home
  await page.goto('/')

  // Log in as admin (same pattern as ai-chat-smoke.spec.ts)
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()

  // Wait for login to actually complete before navigating — otherwise the
  // /ai-chat route guard can redirect back to the login page (auth race).
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })

  // Navigate to /ai-chat. NOTE: no networkidle wait — the page keeps an SSE
  // stream open, so the network never settles; wait for real elements instead.
  await page.goto('/ai-chat')

  // The batch section lives directly in the sidebar (no tabs anymore)
  const createBatchBtn = page.locator('.ai-sidebar__batches-head button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()

  // Everything below is scoped to the dialog — the page has other file inputs
  // (chat composer) and stale batch children can share file names.
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })

  const nameInput = dialog.locator('input[data-test="name"]')
  const promptInput = dialog.locator('textarea[data-test="prompt"]')
  await nameInput.fill('e2e-batch')
  await promptInput.fill('echo hi')

  // Upload 2 in-memory files via the dialog's ElUpload input[type=file]
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
    { name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('B') },
  ])

  // Wait for staged files in the dialog's own file list
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })
  await expect(dialog.locator('.files')).toContainText('b.txt', { timeout: 8_000 })

  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  // The batch shows up as a .batch-group in the sidebar
  const group = page.locator('.batch-group', { hasText: 'e2e-batch' }).first()
  await group.waitFor({ state: 'visible', timeout: 10_000 })

  // Expand it: one child session per input file. The list polls/re-renders,
  // which can swallow the toggle click — retry until the body is really there.
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.batch-group__body').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
  await expect(group.locator('.bg-child')).toHaveCount(2, { timeout: 10_000 })

  // Wait up to 120s for the batch to reach a terminal status badge
  await page.waitForFunction(() => {
    const groups = Array.from(document.querySelectorAll('.batch-group'))
    const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes('e2e-batch'))
    const badge = g?.querySelector('.badge')
    return !!badge && ['completed', 'failed', 'partial'].some(
      s => badge.classList.contains(`badge--${s}`))
  }, undefined, { timeout: 120_000 })

  // Delete via the 删除批次 icon, confirm in the ElMessageBox
  await group.locator('[title="删除批次"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()

  // Confirm the batch is gone from the sidebar
  await expect(page.locator('.batch-group', { hasText: 'e2e-batch' }))
    .toHaveCount(0, { timeout: 5_000 })
})
