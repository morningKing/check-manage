/**
 * 智能写作 · 快捷填充模板（/ 菜单）E2E：
 *  1) 管理面「快捷填充」用 UI 新建模板 → 文档里输入 / 过滤 → 选中 → 自动填充，
 *     且第一个 ___ 占位符被选中，直接输入即替换；
 *  2) 文档已打开时，另一标签页保存模板 → BroadcastChannel 通知 → 无需刷新，
 *     / 菜单即出现新模板。
 *
 * 前置：dev:all mock 栈（Flask 3002 + vite 5173 + suggest-editor-server 1234）。
 * 模板真源：Flask `/suggest-editor/snippets/global`（全局文件），用例结束恢复原值。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

const API_BASE = 'http://127.0.0.1:3002'

interface TextSnippet {
  id: string
  name: string
  description: string
  keywords: string
  contentHtml: string
}

const TEMPLATE_HTML = '<p>检查项：___</p><p>检查方法：___</p><p>检查结论：___</p>'

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
  await page.waitForTimeout(1200) // 等 synced 后的模板/规则装载完成
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.stack ?? error.message
  return String(error)
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

async function fetchSnippets(api: APIRequestContext, token: string): Promise<TextSnippet[]> {
  const res = await api.get(`${API_BASE}/suggest-editor/snippets/global`, {
    headers: authHeaders(token),
  })
  expect(res.ok(), `GET snippets failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  const body = await res.json() as { snippets?: TextSnippet[] }
  return body.snippets ?? []
}

async function putSnippets(
  api: APIRequestContext, token: string, snippets: TextSnippet[],
): Promise<void> {
  const res = await api.put(`${API_BASE}/suggest-editor/snippets/global`, {
    headers: authHeaders(token),
    data: { snippets },
  })
  expect(res.ok(), `PUT snippets failed: ${res.status()} ${await res.text()}`).toBeTruthy()
}

async function deleteDocsByTitle(
  api: APIRequestContext, token: string, title: string,
): Promise<void> {
  const res = await api.get(`${API_BASE}/suggest-editor/documents`, {
    headers: authHeaders(token),
  })
  expect(res.ok(), `GET documents failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  const body = await res.json() as { documents?: { id: string; title: string }[] }
  for (const doc of (body.documents ?? []).filter((item) => item.title === title)) {
    const deleted = await api.delete(`${API_BASE}/suggest-editor/documents/${doc.id}`, {
      headers: authHeaders(token),
    })
    expect(deleted.ok(), `DELETE document ${doc.id} failed: ${deleted.status()}`).toBeTruthy()
  }
}

function outerSnippetDialog(page: Page) {
  return page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '快捷填充模板' }),
  })
}

/** 列表页「快捷填充」→ 新建 → 保存（保存后留在外层对话框，由调用方关闭） */
async function createSnippetViaUi(
  page: Page, name: string, keywords: string, contentHtml: string,
): Promise<void> {
  await page.getByRole('button', { name: '快捷填充' }).click()
  const outer = outerSnippetDialog(page)
  await outer.waitFor({ state: 'visible' })
  await outer.getByRole('button', { name: '新建模板' }).click()

  const inner = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '新建快捷模板' }),
  })
  await inner.waitFor({ state: 'visible' })
  await inner.getByPlaceholder('如：检查逻辑三段式').fill(name)
  await inner.getByPlaceholder('选模板时显示在名称右侧，一句话说明用途').fill('E2E 自动填充')
  await inner.getByPlaceholder('空格分隔的别名/拼音，如：jiance 检查 逻辑').fill(keywords)
  // 内层只有一个 textarea（正文 HTML）
  await inner.locator('textarea').fill(contentHtml)
  await inner.getByRole('button', { name: '保存' }).click()

  await expect(page.locator('.el-message', { hasText: '模板已保存' })).toBeVisible({ timeout: 10_000 })
  await expect(inner).toBeHidden({ timeout: 10_000 })
  await expect(outer.locator('.el-table__row', { hasText: name })).toBeVisible({ timeout: 10_000 })
}

async function closeSnippetDialog(page: Page): Promise<void> {
  const outer = outerSnippetDialog(page)
  await outer.locator('.el-dialog__headerbtn').click()
  await expect(outer).toBeHidden({ timeout: 10_000 })
}

test('管理面建模板 → 文档输入 / 选择 → 自动填充并选中占位符', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-快捷模板-${Date.now()}`
  const name = `E2E检查模板-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let original: TextSnippet[] = []
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()
    original = await fetchSnippets(api, token)

    await gotoSuggestList(page)
    await createSnippetViaUi(page, name, 'e2ebianli e2e', TEMPLATE_HTML)
    await closeSnippetDialog(page)

    await createBlankDoc(page, title)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('/')
    await page.keyboard.type('e2e')

    const menu = page.locator('.se-slash-menu')
    await expect(menu).toBeVisible({ timeout: 10_000 })
    const row = menu.locator('.slash-item[data-kind="snippet"]', { hasText: name })
    await expect(row).toBeVisible({ timeout: 10_000 })
    await row.click()

    // 模板 HTML 已插入
    const surface = page.locator('.doc-surface')
    await expect(surface).toContainText('检查项：', { timeout: 10_000 })
    await expect(surface).toContainText('检查方法：')
    await expect(surface).toContainText('检查结论：')

    // 第一个 ___ 已被选中：直接输入即替换
    await expect
      .poll(() => page.evaluate(() => window.getSelection()?.toString() ?? ''))
      .toBe('___')
    await page.keyboard.type('数据完整性')
    await expect(surface.locator('p').first()).toContainText('检查项：数据完整性')

    // / 文本已被消费（不残留 “/e2e”）
    await expect(surface).not.toContainText('/e2e')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putSnippets(api, token, original)
    } catch (error) {
      cleanupErrors.push(`恢复全局模板失败：${errorText(error)}`)
    }
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${errorText(error)}`)
    }
    await api.dispose()
    if (cleanupErrors.length) {
      const primary = testFailure ? `\n\n用例主体原始失败：\n${errorText(testFailure)}` : ''
      throw new Error(`清理未完成，环境可能残留脏数据：\n- ${cleanupErrors.join('\n- ')}${primary}`)
    }
    if (testFailure) throw testFailure
  }
})

test('另一标签页保存模板 → 已打开文档无需刷新，/ 菜单即出现', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-模板热更-${Date.now()}`
  const name = `E2E热更模板-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let original: TextSnippet[] = []
  let testFailure: unknown = null
  const pageB = await page.context().newPage()

  try {
    await page.goto('/home')
    token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()
    original = await fetchSnippets(api, token)

    // A：先打开文档
    await gotoSuggestList(page)
    await createBlankDoc(page, title)

    // B：另一标签页经管理面新建模板
    await gotoSuggestList(pageB)
    await createSnippetViaUi(pageB, name, 'regen e2e', TEMPLATE_HTML)
    await closeSnippetDialog(pageB)

    // A：不刷新，等广播重载完成后输入 / 过滤
    await page.waitForTimeout(1200)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('/')
    await page.keyboard.type('regen')
    const menu = page.locator('.se-slash-menu')
    await expect(menu).toBeVisible({ timeout: 10_000 })
    await expect(
      menu.locator('.slash-item[data-kind="snippet"]', { hasText: name }),
    ).toBeVisible({ timeout: 10_000 })
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putSnippets(api, token, original)
    } catch (error) {
      cleanupErrors.push(`恢复全局模板失败：${errorText(error)}`)
    }
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${errorText(error)}`)
    }
    await pageB.close().catch(() => {})
    await api.dispose()
    if (cleanupErrors.length) {
      const primary = testFailure ? `\n\n用例主体原始失败：\n${errorText(testFailure)}` : ''
      throw new Error(`清理未完成，环境可能残留脏数据：\n- ${cleanupErrors.join('\n- ')}${primary}`)
    }
    if (testFailure) throw testFailure
  }
})
