/**
 * 智能写作一期 E2E：表格规则、图片图注、排版、Word 导入/导出。
 * 前置：npm run dev:all（Flask 3002 + vite 5173 + se mock 1234）。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  Document, Packer, Paragraph, HeadingLevel, Table, TableRow, TableCell, TextRun, ImageRun,
} from 'docx'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']
const API_BASE = 'http://127.0.0.1:3002'
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg=='
const PNG_BUFFER = Buffer.from(PNG_B64, 'base64')

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

async function tokenOf(page: Page): Promise<string> {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem('check-manage:token') || '""')) as Promise<string>
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

async function gotoSuggestList(page: Page): Promise<void> {
  await page.goto('/home')
  const menuItem = page.locator('.el-menu-item', { hasText: '智能写作' }).first()
  await menuItem.waitFor({ state: 'visible', timeout: 30_000 })
  await menuItem.click()
  await expect(page.getByRole('button', { name: '新建文档' })).toBeVisible({ timeout: 15_000 })
}

async function createBlankDoc(page: Page, title: string): Promise<void> {
  await page.getByRole('button', { name: '新建文档' }).click()
  const dialog = page.locator('.el-dialog', { hasText: '新建文档' })
  await dialog.waitFor({ state: 'visible' })
  await dialog.getByPlaceholder('未命名文档').fill(title)
  await dialog.getByRole('button', { name: '创建' }).click()
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 20_000 })
  await page.waitForTimeout(1200)
}

async function fetchGlobalRules(api: APIRequestContext, token: string): Promise<unknown[]> {
  const res = await api.get(`${API_BASE}/suggest-editor/rules/global`, {
    headers: authHeaders(token),
  })
  if (!res.ok()) return []
  return ((await res.json()) as { rules?: unknown[] }).rules ?? []
}

async function putGlobalRules(
  api: APIRequestContext, token: string, rules: unknown[],
): Promise<void> {
  const res = await api.put(`${API_BASE}/suggest-editor/rules/global`, {
    headers: authHeaders(token), data: { rules },
  })
  expect(res.ok(), `PUT global rules failed: ${res.status()} ${await res.text()}`).toBeTruthy()
}

async function deleteDocsByTitle(
  api: APIRequestContext, token: string, title: string,
): Promise<void> {
  const res = await api.get(`${API_BASE}/suggest-editor/documents`, {
    headers: authHeaders(token),
  })
  const body = await res.json() as { documents?: { id: string; title: string }[] }
  for (const doc of (body.documents ?? []).filter((d) => d.title === title)) {
    const deleted = await api.delete(`${API_BASE}/suggest-editor/documents/${doc.id}`, {
      headers: authHeaders(token),
    })
    expect(deleted.ok(), `DELETE document ${doc.id} failed: ${deleted.status()}`).toBeTruthy()
  }
}

async function makeFixtureDocx(filePath: string): Promise<void> {
  const doc = new Document({
    sections: [{
      children: [
        new Paragraph({ text: '导入标题', heading: HeadingLevel.HEADING_1 }),
        new Paragraph({ children: [new TextRun('正文第一段')] }),
        new Paragraph({ text: '事项一', bullet: { level: 0 } }),
        new Table({
          rows: [
            new TableRow({
              children: [
                new TableCell({ children: [new Paragraph('设备')] }),
                new TableCell({ children: [new Paragraph('数量')] }),
              ],
            }),
            new TableRow({
              children: [
                new TableCell({ children: [new Paragraph('9800M12')] }),
                new TableCell({ children: [new Paragraph('3')] }),
              ],
            }),
          ],
        }),
        new Paragraph({
          children: [new ImageRun({
            type: 'png', data: PNG_BUFFER, transformation: { width: 60, height: 60 },
          })],
        }),
      ],
    }],
  })
  fs.writeFileSync(filePath, await Packer.toBuffer(doc))
}

test('表格单元格内的本地规则实时命中', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-表格规则-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let original: unknown[] = []
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await tokenOf(page)
    expect(token).toBeTruthy()
    original = await fetchGlobalRules(api, token)
    await putGlobalRules(api, token, [{
      id: 'e2e-wl-table',
      name: 'E2E设备类型白名单',
      enabled: true,
      severity: 'warn',
      whitelists: [{
        anchors: ['设备类型：', '设备类型:'],
        values: ['9800M12', '9600M12'],
        message: '设备类型只能是 9800M12 或 9600M12',
      }],
    }])

    await gotoSuggestList(page)
    await createBlankDoc(page, title)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('/')
    await page.keyboard.type('biaoge')
    const menu = page.locator('.se-slash-menu')
    await expect(menu).toBeVisible({ timeout: 10_000 })
    await menu.locator('.slash-item', { hasText: '表格' }).click()
    await expect(page.locator('.doc-surface table')).toBeVisible({ timeout: 10_000 })

    await page.locator('.doc-surface th').first().click()
    await page.keyboard.type('设备类型：9800M12；5000M12', { delay: 20 })
    const card = page.locator('.se-suggest-note', { hasText: '设备类型只能是 9800M12 或 9600M12' })
    await expect(card).toBeVisible({ timeout: 10_000 })
    await expect(card.locator('.se-suggest-note-quote')).toContainText('5000M12')

    await page.reload()
    await expect(page.locator('.doc-surface table')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.doc-surface th').first()).toContainText('9800M12')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putGlobalRules(api, token, original)
    } catch (error) {
      cleanupErrors.push(`恢复全局规则失败：${String(error)}`)
    }
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})

test('图片图注与排版持久化', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-图片排版-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await tokenOf(page)
    await gotoSuggestList(page)
    await createBlankDoc(page, title)

    await page.locator('.doc-surface').click()
    await page.keyboard.type('排版测试')
    await page.keyboard.press('Home')
    await page.keyboard.press('Shift+End')
    await page.locator('[title="字号"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '16pt' }).click()
    await page.locator('[title="文字颜色"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '#c0392b' }).click()
    await page.locator('[title="高亮"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '#fff3a3' }).click()
    await page.locator('[title="居中"]').click()
    // 回到段落末尾再插图：确保选区已折叠，图片不会替换掉刚排好版的文字
    const formattedParagraph = page.locator('.doc-surface p', { hasText: '排版测试' }).first()
    await formattedParagraph.click()
    await page.keyboard.press('End')

    const chooserPromise = page.waitForEvent('filechooser')
    await page.locator('[title="插入图片"]').click()
    const chooser = await chooserPromise
    await chooser.setFiles({ name: 'dot.png', mimeType: 'image/png', buffer: PNG_BUFFER })
    const img = page.locator('.doc-surface figure img').first()
    await expect(img).toBeVisible({ timeout: 15_000 })
    await expect.poll(
      () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0)

    const caption = page.locator('.doc-surface figcaption').first()
    await caption.click()
    await page.keyboard.type('图注：9800M12')
    await expect(caption).toContainText('图注：9800M12')

    const html = await page.locator('.doc-surface').innerHTML()
    expect(html).toContain('font-size: 16pt')
    expect(html).toContain('text-align: center')
    expect(html).toContain('#fff3a3')

    await page.reload()
    await expect(page.locator('.doc-surface figure img').first()).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.doc-surface figcaption').first()).toContainText('图注：9800M12')
    const restored = await page.locator('.doc-surface').innerHTML()
    expect(restored).toContain('font-size: 16pt')
    expect(restored).toContain('text-align: center')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})

test('Word 导入、首轮基线与导出', async ({ page, playwright }) => {
  test.setTimeout(240_000)
  const title = `E2E-Word导入-${Date.now()}`
  const fixture = path.join(os.tmpdir(), `${title}.docx`)
  const exported = path.join(os.tmpdir(), `${title}-out.docx`)
  const api = await playwright.request.newContext()
  let token = ''
  let testFailure: unknown = null

  try {
    await makeFixtureDocx(fixture)
    await page.goto('/home')
    token = await tokenOf(page)
    await gotoSuggestList(page)

    await page.getByRole('button', { name: '导入 Word' }).click()
    const dialog = page.locator('.el-dialog').filter({
      has: page.locator('.el-dialog__title', { hasText: '导入 Word' }),
    })
    await dialog.waitFor({ state: 'visible' })
    await dialog.locator('input[type="file"]').setInputFiles(fixture)
    await dialog.getByRole('button', { name: '开始导入' }).click()

    await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })
    await expect(page.locator('.doc-surface h1')).toHaveText('导入标题', { timeout: 60_000 })
    await expect(page.locator('.doc-surface table')).toContainText('9800M12')
    const img = page.locator('.doc-surface figure img').first()
    await expect(img).toBeVisible({ timeout: 20_000 })
    await expect.poll(
      () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0)

    await expect(page.locator('.se-status')).toContainText('已建立导入基线', { timeout: 60_000 })
    // 点进正文段落再编辑，确保插入点确实在文本块里（而不是图片/图注）
    const paragraph = page.locator('.doc-surface p', { hasText: '正文第一段' }).first()
    await paragraph.click()
    await page.keyboard.press('End')
    await page.keyboard.type('X')
    await expect(paragraph).toContainText('正文第一段X')
    await expect(page.locator('.se-status')).toContainText(/分析 1 个变动段落/, { timeout: 60_000 })

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出 Word' }).click()
    const download = await downloadPromise
    await download.saveAs(exported)

    const mammothModule = await import('mammoth') as unknown as {
      convertToHtml?: (input: { buffer: Buffer }) => Promise<{ value: string }>
      default?: { convertToHtml: (input: { buffer: Buffer }) => Promise<{ value: string }> }
    }
    const convert = mammothModule.convertToHtml ?? mammothModule.default?.convertToHtml
    if (!convert) throw new Error('mammoth convertToHtml unavailable')
    const { value } = await convert({ buffer: fs.readFileSync(exported) })
    expect(value).toContain('导入标题')
    expect(value).toContain('9800M12')
    expect(value).toContain('正文第一段')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    fs.rmSync(fixture, { force: true })
    fs.rmSync(exported, { force: true })
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})
