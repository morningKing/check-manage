/**
 * 智能写作 · 检查规则 E2E：
 *  1) 全局映射规则（经 API 直连 Flask 重置）在输入后毫秒级命中，接受后正文替换；
 *  2) 文档级 presence（条件完整性）规则用 UI 建：出现不可接受的提醒卡片，
 *     补写豁免词后卡片自动消失（实时重算，无需重开文档）；
 *  3) 文档级隔离：另一篇文档不出现该 presence 提示，但全局映射规则仍生效。
 *
 * 前置：dev:all mock 栈（Flask 3002 + vite 5173 + suggest-editor-server 1234）。
 * 规则真源：Flask `/suggest-editor/rules/global` 与 `/suggest-editor/documents/<id>/rules`。
 * 本 spec 自包含登录态与列表导航（复制自 suggest-editor.spec.ts，保持独立）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

const API_BASE = 'http://127.0.0.1:3002'

/** 服务端 DEFAULT_RULES 的 9800 示例（清理时兜底恢复；正常路径恢复测试前抓到的原值） */
const DEFAULT_GLOBAL_RULES = [
  {
    id: 'rule-9800ups-drag',
    name: '9800UPS 拖带完整性',
    enabled: true,
    severity: 'warn',
    presence: [{
      triggers: ['9800M12'],
      requiresAny: ['9800UPS', '不涉及拖带', '无需拖带', '不拖带'],
      message: '方案涉及 9800M12，但未提及 9800UPS（可拖带 9800M12），也未标识「不涉及拖带」',
    }],
    aiPrompt: '检查设备配套与拖带声明：涉及 9800M12 时须写明 9800UPS 或明确不涉及拖带。',
  },
]

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
  // 编辑页就绪：正文 surface 出现且顶栏同步徽标不再停在「已断开」
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.se-badge').first()).toHaveText(/已同步|连接中/, { timeout: 20_000 })
  await page.waitForTimeout(1200) // 等 synced 事件后规则装载/空文档就绪
}

async function deleteDocRow(page: Page, title: string): Promise<void> {
  const row = page.locator('.el-table__row', { hasText: title })
  await expect(row).toBeVisible({ timeout: 15_000 })
  await row.getByRole('button', { name: '删除' }).click()
  const confirmBox = page.locator('.el-message-box')
  await confirmBox.waitFor({ state: 'visible' })
  await confirmBox.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.el-table__row', { hasText: title })).toHaveCount(0, {
    timeout: 15_000,
  })
}

async function putGlobalRules(
  page: Page, token: string, rules: unknown[],
): Promise<void> {
  const res = await page.request.put(`${API_BASE}/suggest-editor/rules/global`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { rules },
  })
  expect(res.ok(), `PUT global rules failed: ${res.status()} ${await res.text()}`).toBeTruthy()
}

/**
 * 文档编辑页顶栏「规则」→「新建规则」→ 保存 presence 规则并关闭对话框。
 * 对话框内层 append-to-body，用标题精确区分外层（检查规则）与内层（新建规则）。
 */
async function createDocPresenceRule(
  page: Page,
  name: string,
  trigger: string,
  requiresAny: string[],
  message: string,
): Promise<void> {
  await page.getByRole('button', { name: '规则', exact: true }).click()
  const rulesDialog = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '检查规则' }),
  })
  await rulesDialog.waitFor({ state: 'visible' })
  await rulesDialog.getByRole('button', { name: '新建规则' }).click()

  const form = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '新建规则' }),
  })
  await form.waitFor({ state: 'visible' })
  await form.getByPlaceholder(/规则名/).fill(name)
  await form.getByRole('button', { name: /\+ 完整性/ }).click()
  // Element Plus 2.13 把非 prop attrs 直接合并到内部 textarea/input 上，
  // 因此 data-field 命中的就是可填元素本身（不是外层 wrapper）
  await form.locator('[data-field="triggers"]').fill(trigger)
  await form.locator('[data-field="requiresAny"]').fill(requiresAny.join('\n'))
  await form.locator('[data-field="message"]').fill(message)
  await form.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.el-message', { hasText: '规则已保存' })).toBeVisible({
    timeout: 10_000,
  })
  await rulesDialog.getByRole('button', { name: '关闭', exact: true }).click()
  await rulesDialog.waitFor({ state: 'hidden', timeout: 10_000 })
}

test('映射毫秒级命中并接受；presence 随豁免词消失；文档级隔离', async ({ page }) => {
  test.setTimeout(180_000)
  const ts = Date.now()
  const titleMap = `E2E-规则-${ts}`
  const titleIsolated = `E2E-规则隔离-${ts}`

  // 0) 先登录态页面就绪，读 token（beforeEach 已注入 localStorage）
  await page.goto('/home')
  const token = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
  expect(token).toBeTruthy()

  // 保存测试前的全局规则，结束时原样恢复（首次 GET 会播种 9800 示例）
  let originalGlobalRules: unknown[] = DEFAULT_GLOBAL_RULES
  try {
    const current = await page.request.get(`${API_BASE}/suggest-editor/rules/global`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (current.ok()) {
      const body = await current.json() as { rules?: unknown[] }
      if (Array.isArray(body.rules)) originalGlobalRules = body.rules
    }
  } catch { /* 恢复时退回 DEFAULT_GLOBAL_RULES */ }

  try {
    // 0) 全局规则重置为可控集合：只保留一条映射（避免默认全局 presence 干扰断言）
    await putGlobalRules(page, token, [{
      id: 'e2e-map',
      name: 'E2E 型号纠错',
      enabled: true,
      severity: 'error',
      maps: [{ wrong: 'S-5700', right: 'S5700' }],
    }])

    // 1) 列表页 → 新建文档 → 文档级 presence 规则（UI 全流程）
    await gotoSuggestList(page)
    await createBlankDoc(page, titleMap)
    await createDocPresenceRule(page, 'E2E 拖带完整性', '9800M12', ['9800UPS', '不涉及拖带'], '缺拖带说明')

    // 2) 映射规则：输入错误写法 → 卡片即时出现（≤5s）→ 接受后正文替换
    await page.locator('.doc-surface').click()
    await page.keyboard.type('设备 S-5700 与 9800M12 组网。', { delay: 20 })
    const mapCard = page.locator('.se-suggest-note', { hasText: 'E2E 型号纠错' })
    await expect(mapCard).toBeVisible({ timeout: 5_000 })
    await mapCard.getByRole('button', { name: '接受' }).click()
    await expect(page.locator('.doc-surface')).toContainText('S5700')
    await expect(page.locator('.doc-surface')).not.toContainText('S-5700')

    // 3) presence：出现 notice（无「接受」按钮）→ 补豁免词 → 实时消失
    const presenceCard = page.locator('.se-suggest-note', { hasText: 'E2E 拖带完整性' })
    await expect(presenceCard).toBeVisible({ timeout: 5_000 })
    await expect(presenceCard.getByRole('button', { name: '接受' })).toHaveCount(0)
    await page.screenshot({ path: 'e2e/screenshots/suggest-editor-rules-presence.png' })

    await page.locator('.doc-surface').click()
    await page.keyboard.press('Control+End')
    await page.keyboard.type('本方案不涉及拖带。', { delay: 20 })
    await expect(presenceCard).toHaveCount(0, { timeout: 5_000 })

    // 4) 隔离：另一文档不出现该 presence 提示；全局映射仍生效
    await gotoSuggestList(page)
    await createBlankDoc(page, titleIsolated)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('设备 S-5700 与 9800M12 组网。', { delay: 20 })
    await expect(
      page.locator('.se-suggest-note', { hasText: 'E2E 型号纠错' }),
    ).toBeVisible({ timeout: 5_000 })
    // 给规则重算留出时间：若文档级规则被错误地全局应用，这里必然出现卡片
    await page.waitForTimeout(1800)
    await expect(
      page.locator('.se-suggest-note', { hasText: 'E2E 拖带完整性' }),
    ).toHaveCount(0)
  } finally {
    // 5) 清理：全局规则恢复（优先恢复测试前原值），删除两篇文档
    try {
      await putGlobalRules(page, token, originalGlobalRules)
    } catch (error) {
      console.warn('[suggest-editor-rules] 全局规则恢复失败：', error)
    }
    for (const title of [titleMap, titleIsolated]) {
      try {
        await gotoSuggestList(page)
        await deleteDocRow(page, title)
      } catch (error) {
        console.warn(`[suggest-editor-rules] 删除文档 ${title} 失败：`, error)
      }
    }
  }
})
