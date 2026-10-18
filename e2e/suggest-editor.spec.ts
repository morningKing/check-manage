/**
 * 智能写作（suggest-editor）E2E —— 真实链路：
 *   登录 → 侧栏「智能写作」→ 从模板新建文档 → 模板内容渲染 →
 *   输入触发文本 → 本地即时建议 + mock agent AI 建议卡片 →
 *   刷新页面（正文 + 建议恢复 = 工作状态持久化验收）→
 *   列表：改名 / 再建 / 删除。
 *
 * 前置：dev:all 全栈（Flask 3002 + vite 5173 + suggest-editor-server 1234）。
 * 建议引擎用内置 mock agent（确定性规则：非常→删除、但是→然而、连续空格→单空格），
 * 不依赖 OpenCode。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

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

const TRIGGER_TEXT = '这个方案非常不错，但是 我们的文档里存在连续  空格的问题。'

async function gotoSuggestList(page: Page) {
  await page.goto('/home')
  // 侧栏菜单「智能写作」（menu_type=system，path=/suggest-editor）
  const menuItem = page.locator('.el-menu-item', { hasText: '智能写作' }).first()
  await menuItem.waitFor({ state: 'visible', timeout: 30_000 })
  await menuItem.click()
  await expect(page.getByRole('button', { name: '新建文档' })).toBeVisible({ timeout: 15_000 })
}

async function createDocFromTemplate(page: Page, title: string, templateName: string | null) {
  await page.getByRole('button', { name: '新建文档' }).click()
  const dialog = page.locator('.el-dialog', { hasText: '新建文档' })
  await dialog.waitFor({ state: 'visible' })
  await dialog.getByPlaceholder('未命名文档').fill(title)
  if (templateName) {
    await dialog.locator('.template-card', { hasText: templateName }).click()
  }
  await dialog.getByRole('button', { name: '创建' }).click()
  // 编辑页就绪：正文 surface 出现且顶栏连接徽标变为已同步
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.se-badge').first()).toHaveText(/已同步|连接中/, { timeout: 20_000 })
  await page.waitForTimeout(1500) // 等 synced 事件与模板灌入
}

test('模板建文档 → 建议 → 刷新恢复 → 列表管理', async ({ page }) => {
  test.setTimeout(180_000)
  const title = `E2E-周报-${Date.now()}`

  await gotoSuggestList(page)

  // 1) 从「项目周报」模板新建
  await createDocFromTemplate(page, title, '项目周报')
  await expect(page.locator('.doc-surface h1')).toHaveText('本周工作汇报', { timeout: 10_000 })

  // 2) 输入触发文本：另起一段
  await page.locator('.doc-surface').click()
  await page.keyboard.press('Control+End')
  await page.keyboard.press('Enter')
  await page.keyboard.type(TRIGGER_TEXT, { delay: 20 })
  await page.waitForTimeout(2500)

  // 本地即时检查（毫秒级）：连续空格 → 绿色「即时」卡片
  await expect(
    page.locator('.se-suggest-note', { hasText: '连续空格' }),
  ).toBeVisible({ timeout: 10_000 })

  // AI 建议（mock agent 确定性规则：「但是」→「然而」）
  await expect(
    page.locator('.se-suggest-note', { hasText: '然而' }).first(),
  ).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-suggestions.png' })

  // 3) 落库窗口（onStoreDocument 防抖 ~2s）后整页刷新：工作状态恢复
  await page.waitForTimeout(3500)
  await page.reload()
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(2500) // 等 synced 回灌

  // 正文恢复（含已接受的模板与输入的触发段）。
  // 注意不能整句匹配：恢复的 AI 建议会在正文中插入编号圆标 widget，
  // 把文本按建议位置切开（这本身就是「建议已恢复」的证据）。
  await expect(page.locator('.doc-surface')).toContainText('本周工作汇报')
  await expect(page.locator('.doc-surface')).toContainText('这个方案非常')
  await expect(page.locator('.doc-surface')).toContainText('空格的问题。')

  // 建议恢复：持久化在 Y.Map("suggestions") 里，随 Y.Doc 回灌
  await expect(
    page.locator('.se-suggest-note', { hasText: '然而' }).first(),
  ).toBeVisible({ timeout: 30_000 })
  await expect(
    page.locator('.se-suggest-note', { hasText: '连续空格' }),
  ).toBeVisible({ timeout: 10_000 })
  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-recovered.png' })

  // 4) 回列表：文档在列、模板列显示「项目周报」
  await gotoSuggestList(page)
  const row = page.locator('.el-table__row', { hasText: title })
  await expect(row).toBeVisible({ timeout: 15_000 })
  await expect(row).toContainText('项目周报')

  // 5) 重命名（ElMessageBox prompt）
  await row.getByRole('button', { name: '重命名' }).click()
  const renameBox = page.locator('.el-message-box')
  await renameBox.waitFor({ state: 'visible' })
  const newTitle = `${title}-改`
  await renameBox.locator('input').fill(newTitle)
  await renameBox.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.el-table__row', { hasText: newTitle })).toBeVisible({ timeout: 15_000 })

  // 6) 再建一个空白文档并删除（列表管理闭环）
  await createDocFromTemplate(page, `${title}-临时`, null)
  await gotoSuggestList(page)
  const tempRow = page.locator('.el-table__row', { hasText: `${title}-临时` })
  await expect(tempRow).toBeVisible({ timeout: 15_000 })
  await tempRow.getByRole('button', { name: '删除' }).click()
  const confirmBox = page.locator('.el-message-box')
  await confirmBox.waitFor({ state: 'visible' })
  await confirmBox.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-table__row', { hasText: `${title}-临时` })).toHaveCount(0, {
    timeout: 15_000,
  })

  // 清理主文档
  const renamedRow = page.locator('.el-table__row', { hasText: newTitle })
  await renamedRow.getByRole('button', { name: '删除' }).click()
  await confirmBox.waitFor({ state: 'visible' })
  await confirmBox.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-table__row', { hasText: newTitle })).toHaveCount(0, {
    timeout: 15_000,
  })

  await page.screenshot({ path: 'e2e/screenshots/suggest-editor-e2e.png', fullPage: true })
})
