import { test, expect, type Page } from '@playwright/test'
import { writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * F-upload-drawer + 文件抽屉富预览：
 *  1) 上传文件实时出现在文件抽屉的「上传文件」分组（无需发消息/刷新）。
 *  2) Markdown 文件点「预览」走 FilePreviewDialog 渲染排版（而非显示源码）。
 *  3) 图片文件点「预览」走 FilePreviewDialog 的 <img>（而非"二进制无法预览"）。
 */

async function loginAndOpenChat(page: Page) {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /AI 助手/ }).click()
  await page.getByPlaceholder(/给 AI 助手发消息/).waitFor({ state: 'visible', timeout: 15_000 })
}

async function uploadFile(page: Page, filePath: string) {
  await page.locator('.composer-bar input[type="file"][multiple]').setInputFiles(filePath)
}

function uploadRow(page: Page, fileName: string) {
  const drawer = page.locator('.ai-outputs')
  const group = drawer.locator('.output-group', { hasText: '上传文件' })
  return group.locator('.output-file', { hasText: fileName })
}

test('uploaded file shows up in the 上传文件 drawer group in real time', async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), 'pw-upload-'))
  const fileName = `pw-upload-${Date.now()}.txt`
  const filePath = join(dir, fileName)
  writeFileSync(filePath, `playwright upload test ${Date.now()}`)

  await loginAndOpenChat(page)

  const drawer = page.locator('.ai-outputs')
  await expect(drawer.locator('.output-file__name', { hasText: fileName })).toHaveCount(0)

  await uploadFile(page, filePath)

  const group = drawer.locator('.output-group', { hasText: '上传文件' })
  await expect(group).toBeVisible({ timeout: 20_000 })
  await expect(group.locator('.output-file__name', { hasText: fileName })).toBeVisible({ timeout: 20_000 })

  const row = uploadRow(page, fileName)
  await expect(row.getByRole('button', { name: '预览' })).toBeVisible()
  await expect(row.locator('a.output-file__dl')).toBeVisible()

  rmSync(dir, { recursive: true, force: true })
})

test('markdown file previews as rendered HTML in the preview dialog', async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), 'pw-md-'))
  const stamp = Date.now()
  const fileName = `pw-note-${stamp}.md`
  const heading = `预览标题${stamp}`
  writeFileSync(join(dir, fileName), `# ${heading}\n\n这是一段正文内容。\n`)

  await loginAndOpenChat(page)
  await uploadFile(page, join(dir, fileName))

  const row = uploadRow(page, fileName)
  await expect(row).toBeVisible({ timeout: 20_000 })
  await row.getByRole('button', { name: '预览' }).click()

  const dialog = page.locator('.file-preview-dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  // Rendered heading (h1), not raw markdown source
  await expect(dialog.locator('h1', { hasText: heading })).toBeVisible({ timeout: 20_000 })
  await expect(dialog.locator('.fp-text')).toHaveCount(0) // not the plain-text fallback

  rmSync(dir, { recursive: true, force: true })
})

test('image file previews inline via <img> in the preview dialog', async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), 'pw-img-'))
  const fileName = `pw-pic-${Date.now()}.png`
  // 1x1 transparent PNG
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    'base64',
  )
  writeFileSync(join(dir, fileName), png)

  await loginAndOpenChat(page)
  await uploadFile(page, join(dir, fileName))

  const row = uploadRow(page, fileName)
  await expect(row).toBeVisible({ timeout: 20_000 })
  await row.getByRole('button', { name: '预览' }).click()

  const dialog = page.locator('.file-preview-dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  const img = dialog.locator('.fp-image img')
  await expect(img).toBeVisible({ timeout: 20_000 })
  // Confirm the image actually decoded (naturalWidth > 0), not just an <img> tag
  await expect.poll(async () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth), { timeout: 20_000 })
    .toBeGreaterThan(0)

  rmSync(dir, { recursive: true, force: true })
})
