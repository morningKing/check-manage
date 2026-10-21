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
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

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
  // 编辑页就绪：正文 surface 出现且顶栏徽标已同步（不接受「连接中」，
  // 否则规则装载/模板灌入等 synced 后的动作尚未完成，后续断言会随机失败）
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 20_000 })
  await page.waitForTimeout(1200) // 等 synced 事件后规则装载/空文档就绪
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.stack ?? error.message
  return String(error)
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

async function putGlobalRules(
  api: APIRequestContext, token: string, rules: unknown[],
): Promise<void> {
  const res = await api.put(`${API_BASE}/suggest-editor/rules/global`, {
    headers: authHeaders(token),
    data: { rules },
  })
  expect(res.ok(), `PUT global rules failed: ${res.status()} ${await res.text()}`).toBeTruthy()
}

/**
 * 按标题删除文档（清理专用，走 REST，不依赖 page 生命周期）。
 * 找不到同名文档视为已清理（用例可能在建文档前就失败）。
 */
async function deleteDocsByTitle(
  api: APIRequestContext, token: string, title: string,
): Promise<void> {
  const res = await api.get(`${API_BASE}/suggest-editor/documents`, {
    headers: authHeaders(token),
  })
  expect(res.ok(), `GET documents failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  const body = await res.json() as { documents?: { id: string; title: string }[] }
  const matches = (body.documents ?? []).filter((doc) => doc.title === title)
  for (const doc of matches) {
    const deleted = await api.delete(`${API_BASE}/suggest-editor/documents/${doc.id}`, {
      headers: authHeaders(token),
    })
    expect(deleted.ok(), `DELETE document ${doc.id} failed: ${deleted.status()}`).toBeTruthy()
  }
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

test('映射毫秒级命中并接受；presence 随豁免词消失；文档级隔离', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const ts = Date.now()
  const titleMap = `E2E-规则-${ts}`
  const titleIsolated = `E2E-规则隔离-${ts}`

  // 清理与 API 直连用独立 request context（playwright.request.newContext()）：
  // 不依赖 page 生命周期，用例超时/页面被关闭后仍能恢复全局规则、删除测试文档
  const api = await playwright.request.newContext()
  let testFailure: unknown = null
  try {
    // 0) 先登录态页面就绪，读 token（beforeEach 已注入 localStorage）
    await page.goto('/home')
    const token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()

    // 保存测试前的全局规则，结束时原样恢复（首次 GET 会播种 9800 示例）
    let originalGlobalRules: unknown[] = DEFAULT_GLOBAL_RULES
    try {
      const current = await api.get(`${API_BASE}/suggest-editor/rules/global`, {
        headers: authHeaders(token),
      })
      if (current.ok()) {
        const body = await current.json() as { rules?: unknown[] }
        if (Array.isArray(body.rules)) originalGlobalRules = body.rules
      }
    } catch { /* 恢复时退回 DEFAULT_GLOBAL_RULES */ }

    try {
      // 0) 全局规则重置为可控集合：只保留一条映射（避免默认全局 presence 干扰断言）
      await putGlobalRules(api, token, [{
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
    } catch (error) {
      testFailure = error
    }

    // 5) 清理：全局规则恢复与文档删除各自收集错误；任何一步失败都必须让用例失败，
    //    不允许仅 warn 后仍判 PASS（否则会静默把环境留在 e2e-map-only 状态）。
    const cleanupErrors: string[] = []
    try {
      await putGlobalRules(api, token, originalGlobalRules)
    } catch (error) {
      cleanupErrors.push(`恢复全局规则失败：${errorText(error)}`)
    }
    for (const title of [titleMap, titleIsolated]) {
      try {
        await deleteDocsByTitle(api, token, title)
      } catch (error) {
        cleanupErrors.push(`删除文档「${title}」失败：${errorText(error)}`)
      }
    }
    if (cleanupErrors.length) {
      const primary = testFailure ? `\n\n用例主体原始失败：\n${errorText(testFailure)}` : ''
      throw new Error(`清理未完成，环境可能残留脏数据：\n- ${cleanupErrors.join('\n- ')}${primary}`)
    }
    if (testFailure) throw testFailure
  } finally {
    await api.dispose()
  }
})

// ---------------------------------------------------------------------------
// 自然语言 → 规则：转换端点用 page.route 拦截（确定性、不依赖真模型），
// 验证「AI 生成 → 入表 → 立即生效」的 UI 与本地通道接线。真模型转换由
// 后端 pytest + 手工探针覆盖（见 docs/user-guide/ai/suggest-editor.md）。
// 作用域用文档级（规则随文档删除，不污染全局）。
// ---------------------------------------------------------------------------
test('AI 生成：自然语言转规则 → 入表保存 → 本地检查立即生效', async ({ page, playwright }) => {
  test.setTimeout(120_000)
  const title = `E2E-AI规则-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let testFailure: unknown = null

  await page.route('**/api/suggest-editor/rules/convert', async (route) => {
    const body = route.request().postDataJSON() as { description?: string }
    expect(route.request().method()).toBe('POST')
    expect(String(body.description ?? '')).toContain('X-1')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        rules: [{
          id: 'rule-ai-gen',
          name: 'E2E AI 生成映射',
          enabled: true,
          severity: 'warn',
          maps: [{ wrong: 'X-1', right: 'X1' }],
          aiPrompt: '型号统一写作 X1',
        }],
      }),
    })
  })

  try {
    await page.goto('/home')
    token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()

    await gotoSuggestList(page)
    await createBlankDoc(page, title)

    // 文档级规则对话框 → AI 生成
    await page.getByRole('button', { name: '规则', exact: true }).click()
    const rulesDialog = page.locator('.el-dialog').filter({
      has: page.locator('.el-dialog__title', { hasText: '检查规则' }),
    })
    await rulesDialog.waitFor({ state: 'visible' })
    await rulesDialog.getByRole('button', { name: 'AI 生成' }).click()

    const aiDialog = page.locator('.el-dialog').filter({
      has: page.locator('.el-dialog__title', { hasText: '用自然语言生成规则' }),
    })
    await aiDialog.waitFor({ state: 'visible' })
    await aiDialog.locator('[data-field="nl-rule"]').fill('型号 X-1 应写作 X1')
    await aiDialog.getByRole('button', { name: '生成' }).click()

    await expect(page.locator('.el-message', { hasText: 'AI 已生成' })).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      rulesDialog.locator('.el-table__row', { hasText: 'E2E AI 生成映射' }),
    ).toBeVisible({ timeout: 10_000 })
    await rulesDialog.getByRole('button', { name: '关闭', exact: true }).click()
    await rulesDialog.waitFor({ state: 'hidden', timeout: 10_000 })

    // 生成的规则立即生效：输入错误写法 → 本地卡片（毫秒级）
    await page.locator('.doc-surface').click()
    await page.keyboard.type('型号 X-1 上线。', { delay: 20 })
    await expect(
      page.locator('.se-suggest-note', { hasText: 'E2E AI 生成映射' }),
    ).toBeVisible({ timeout: 5_000 })
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
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

// ---------------------------------------------------------------------------
// 跨标签页规则即时生效：标签页 A 打开文档编辑，标签页 B 在列表页改全局规则，
// 保存后 A 不刷新、不再输入，也应立即出现新规则的提示（BroadcastChannel）。
// 这正是「编辑了规则没生效」的典型场景，防止回归。
// ---------------------------------------------------------------------------
test('规则保存后其它标签页即时生效（无需刷新/编辑）', async ({ page, context, playwright }) => {
  test.setTimeout(150_000)
  const title = `E2E-跨标签规则-${Date.now()}`
  const api = await playwright.request.newContext()
  const pageB = await context.newPage()
  let token = ''
  let testFailure: unknown = null
  let originalGlobalRules: unknown[] = DEFAULT_GLOBAL_RULES

  try {
    await page.goto('/home')
    token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()

    // 快照 + 受控规则：只保留一条 9800M12 触发词（9600M12 尚未覆盖）
    try {
      const current = await api.get(`${API_BASE}/suggest-editor/rules/global`, {
        headers: authHeaders(token),
      })
      if (current.ok()) {
        const body = await current.json() as { rules?: unknown[] }
        if (Array.isArray(body.rules)) originalGlobalRules = body.rules
      }
    } catch { /* 恢复时退回默认 */ }
    await putGlobalRules(api, token, [{
      id: 'e2e-cross-tab',
      name: 'E2E 跨标签规则',
      enabled: true,
      severity: 'warn',
      presence: [{
        triggers: ['9800M12'],
        requiresAny: ['9800UPS', '不涉及拖带'],
        message: '缺拖带说明',
      }],
    }])

    // A 标签页：打开文档，输入 9600M12（当前规则不覆盖 → 不应有卡片）
    await gotoSuggestList(page)
    await createBlankDoc(page, title)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('设备 9600M12 组网。', { delay: 20 })
    const card = page.locator('.se-suggest-note', { hasText: 'E2E 跨标签规则' })
    await expect(card).toHaveCount(0)
    await page.waitForTimeout(800)

    // B 标签页：列表页改全局规则，triggers 追加 9600M12
    await gotoSuggestList(pageB)
    await pageB.getByRole('button', { name: '检查规则' }).click()
    const dialogB = pageB.locator('.el-dialog').filter({
      has: pageB.locator('.el-dialog__title', { hasText: '检查规则' }),
    })
    await dialogB.waitFor({ state: 'visible' })
    await dialogB.locator('.el-table__row').first().getByRole('button', { name: '编辑' }).click()
    const formB = pageB.locator('.el-dialog').filter({
      has: pageB.locator('.el-dialog__title', { hasText: '编辑规则' }),
    })
    await formB.waitFor({ state: 'visible' })
    await formB.locator('[data-field="triggers"]').fill('9800M12\n9600M12')
    await formB.getByRole('button', { name: '确定' }).click()
    await expect(pageB.locator('.el-message', { hasText: '规则已保存' })).toBeVisible({
      timeout: 10_000,
    })

    // A 标签页：不刷新、不输入，卡片应立即出现（BroadcastChannel → reloadRules → 重新判定）
    await expect(card).toBeVisible({ timeout: 10_000 })
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putGlobalRules(api, token, originalGlobalRules)
    } catch (error) {
      cleanupErrors.push(`恢复全局规则失败：${errorText(error)}`)
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

// ---------------------------------------------------------------------------
// 上游 502 的可诊断性：接口把具体原因（serve 不可达/模型错误/超时）放在
// JSON error 里；前端用可复制的弹窗展示，而不是 3 秒 toast。
// 这里用路由拦截固定一个 502（不依赖真 opencode）。
// ---------------------------------------------------------------------------
test('AI 生成 502：弹窗展示上游诊断信息', async ({ page }) => {
  test.setTimeout(120_000)
  const upstreamError =
    'opencode serve 不可达（http://127.0.0.1:4096）：连接被拒绝。'
    + '请检查 proxy.py 启动日志 [3/5] 段（OpenCode reachable/started）'
  await page.route('**/api/suggest-editor/rules/convert', (route) =>
    route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ error: upstreamError }),
    }))

  await page.goto('/home')
  await gotoSuggestList(page)
  await page.getByRole('button', { name: '检查规则' }).click()
  const rulesDialog = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '检查规则' }),
  })
  await rulesDialog.waitFor({ state: 'visible' })
  await rulesDialog.getByRole('button', { name: 'AI 生成…' }).click()

  const aiDialog = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '用自然语言生成规则' }),
  })
  await aiDialog.waitFor({ state: 'visible' })
  await aiDialog.locator('[data-field="nl-rule"]').fill('型号 S-5700 应写作 S5700')
  await aiDialog.getByRole('button', { name: '生成' }).click()

  const alert = page.locator('.el-message-box')
  await expect(alert).toBeVisible({ timeout: 10_000 })
  await expect(alert).toContainText('上游服务 502')
  await expect(alert).toContainText('opencode serve 不可达')
  await expect(alert).toContainText('[3/5]')
  await alert.getByRole('button', { name: '知道了' }).click()
})

// ---------------------------------------------------------------------------
// 白名单多值：锚点后是列表（9800M12；5000M12）时，第二个非法值也必须毫秒级命中。
// 回归 2026-10-21：原实现只看锚点后的第一个 token，5000M12 被漏检。
// ---------------------------------------------------------------------------
test('白名单多值：锚点后第二个非法值实时命中', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-白名单多值-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let original: unknown[] = DEFAULT_GLOBAL_RULES
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('check-manage:token') || '""')) as string
    expect(token).toBeTruthy()
    const current = await api.get(`${API_BASE}/suggest-editor/rules/global`, {
      headers: authHeaders(token),
    })
    if (current.ok()) {
      const body = await current.json() as { rules?: unknown[] }
      if (Array.isArray(body.rules)) original = body.rules
    }
    await putGlobalRules(api, token, [{
      id: 'e2e-whitelist-multi',
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
    await page.keyboard.type('设备类型：9800M12；5000M12', { delay: 20 })

    const card = page.locator('.se-suggest-note', { hasText: '设备类型只能是 9800M12 或 9600M12' })
    await expect(card).toBeVisible({ timeout: 10_000 })
    await expect(card.locator('.se-suggest-note-quote')).toContainText('5000M12')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putGlobalRules(api, token, original)
    } catch (error) {
      cleanupErrors.push(`恢复全局规则失败：${errorText(error)}`)
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


