/**
 * 执行合规审计 E2E（execution-audit Spec §21.6）：
 *  1. 真实交互会话跑一轮 → 管理页执行审计抽屉出现 Attempt/模型/Prompt 快照；
 *  2. 种子契约 + 种子执行 → 确定性审计判定：已完成（有证据）/ 遗漏 / 声称完成；
 *  3. 轨迹分析触发返回 analysisId 并提示成功。
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

// Playwright 进程的 PATH 不保证含 python（shell 环境差异），按候选路径探测
const PYTHON = [
  process.env.PYTHON,
  'F:/llvm/anaconda3/python.exe',
  'C:/Python312/python.exe',
  'python',
].find((c) => c && (c === 'python' || fs.existsSync(c))) || 'python'

function runPy(script: string, args: string[]): string {
  return execFileSync(PYTHON,
    [path.join(process.cwd(), 'e2e', 'helpers', script), ...args],
    { encoding: 'utf-8' }).trim()
}
import { test, expect } from '@playwright/test'

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

function seedAudit(args: string[]): string {
  return runPy('seed_audit.py', args)
}

/** 从首页经顶栏按钮进入全屏 AI 助手（规避整页直达 /ai-chat 的初始化竞态） */
async function gotoAiChat(page: import('@playwright/test').Page) {
  await page.goto('/home')
  const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
  await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await aiBtn.click()
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1500)
  return input
}

async function openAdminSessions(page: import('@playwright/test').Page) {
  await page.goto('/admin/ai-sessions')
  await page.locator('.el-table__row').first()
    .waitFor({ state: 'visible', timeout: 30_000 })
}

async function openAuditDrawerFor(page: import('@playwright/test').Page,
                                  titleText: string) {
  const row = page.locator('.el-table__row', { hasText: titleText }).first()
  await row.waitFor({ state: 'visible', timeout: 15_000 })
  await row.locator('.el-dropdown').first().click()
  await page.locator('.el-dropdown-menu__item', { hasText: '执行审计' }).first().click()
  await page.locator('.el-drawer', { hasText: '执行合规审计' })
    .waitFor({ state: 'visible', timeout: 10_000 })
}

test('真实会话执行审计：Attempt / 生效模型 / 数据完整性', async ({ page, request }) => {
  test.setTimeout(240_000)
  // API 先建会话并重命名 → UI 侧栏精确选中，避免自动创建的时序竞态
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8')) as Record<string, string>
  const token = JSON.parse(auth['check-manage:token'])
  const title = `audit-chat-${Date.now()}`
  const headers = { Authorization: `Bearer ${token}` }
  const created = await request.post('/api/ai/chat/sessions', {
    headers, data: {},
  })
  const sid = (await created.json()).id as string
  await request.patch(`/api/ai/chat/sessions/${sid}`,
    { headers, data: { title } })

  const input = await gotoAiChat(page)
  // 侧栏点击目标会话（挂载可能自动建了别的会话，必须显式切换）
  const item = page.locator('.ai-sidebar .el-text, .ai-sidebar div, aside div',
    { hasText: title }).last()
  await item.waitFor({ state: 'visible', timeout: 20_000 })
  await item.click()
  await input.fill('hello audit e2e')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--assistant').first())
    .toBeVisible({ timeout: 120_000 })

  await openAdminSessions(page)
  const row = page.locator('.el-table__row', { hasText: title }).first()
  await row.waitFor({ state: 'visible', timeout: 20_000 })
  await row.locator('.el-dropdown').first().click()
  await page.locator('.el-dropdown-menu__item', { hasText: '执行审计' })
    .first().click()
  const drawer = page.locator('.el-drawer', { hasText: '执行合规审计' })
  await drawer.waitFor({ state: 'visible', timeout: 10_000 })

  // Attempt 表格出现且至少一条 completed（回合已收敛）
  await expect(drawer.locator('h4', { hasText: '执行尝试' })).toBeVisible()
  await expect(drawer.locator('.el-table__row', { hasText: 'completed' }).first())
    .toBeVisible({ timeout: 15_000 })
  // 生效模型解析方式被记录（平台配置了默认模型 → session_default）
  const firstAttemptRow = drawer.locator('.el-table__row')
    .filter({ hasText: 'session_default' }).first()
  await expect(firstAttemptRow).toBeVisible()
  // Prompt hash 已记录 → 可打开快照
  await expect(firstAttemptRow.getByText('查看快照')).toBeVisible()

  // 数据完整性区块渲染（分形进度条）
  await expect(drawer.locator('h4', { hasText: '数据完整性' })).toBeVisible({ timeout: 20_000 })
})

test('种子契约审计：已完成（有证据）/ 遗漏 / 声称完成', async ({ page }) => {
  test.setTimeout(120_000)
  const title = `audit-seed-${Date.now()}`
  const sid = seedAudit(['seed', title])
  expect(sid).toBeTruthy()

  try {
    await openAdminSessions(page)
    await openAuditDrawerFor(page, title)

    const drawer = page.locator('.el-drawer', { hasText: '执行合规审计' })
    // 确定性审计三态（Spec §11.3）
    await expect(drawer.getByText('已完成（有证据）').first())
      .toBeVisible({ timeout: 20_000 })
    await expect(drawer.getByText('遗漏', { exact: true }).first()).toBeVisible()
    await expect(drawer.getByText('声称完成（无证据）').first()).toBeVisible()
    // 无 run_python/save_artifact 证据 → 不允许出现契约外的伪造“已完成”
    const stepCell = drawer.locator('.el-table__row', { hasText: 'validate_schema' })
    await expect(stepCell).toContainText('遗漏')
  } finally {
    seedAudit(['cleanup', title])
  }
})

test('轨迹分析触发 + 会话关联与隐藏', async ({ page }) => {
  test.setTimeout(180_000)
  const title = `audit-seed-${Date.now()}`
  const targetSid = seedAudit(['seed', title])
  try {
    await openAdminSessions(page)

    // ① 触发分析
    const row = page.locator('.el-table__row', { hasText: title }).first()
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '轨迹分析' }).first().click()
    await expect(page.locator('.el-message', { hasText: '已触发轨迹分析' }))
      .toBeVisible({ timeout: 30_000 })

    // ② 默认管理列表隐藏本次触发的轨迹分析会话
    const analysisTitle = `轨迹分析: ${targetSid}`
    await page.waitForTimeout(1500)
    await expect(page.locator('.el-table__row', { hasText: analysisTitle }))
      .toHaveCount(0, { timeout: 15_000 })

    // ③ 勾选后可见，且带「轨迹分析」来源标签
    await page.getByText('显示轨迹分析会话').click()
    await page.getByRole('button', { name: '查询' }).click()
    const analysisRow = page.locator('.el-table__row', { hasText: analysisTitle }).first()
    await expect(analysisRow).toBeVisible({ timeout: 15_000 })
    await expect(analysisRow.getByText('轨迹分析', { exact: true })).toBeVisible()
    await page.getByText('显示轨迹分析会话').click()
    await page.getByRole('button', { name: '查询' }).click()
    await page.waitForTimeout(1000)

    // ④ 侧栏分组：普通会话不混排，分析会话独立折叠分组
    const input = await gotoAiChat(page)
    const groupHead = page.locator('[data-test="analysis-group-head"]')
    await expect(groupHead).toBeVisible({ timeout: 15_000 })
    await expect(groupHead).toContainText('轨迹分析')
    await expect(groupHead.locator('.analysis-group__count')).not.toHaveText('0')
    // 折叠态：普通会话区不出现分析会话条目（排除折叠体内的 .analysis-item）
    await expect(page.locator('.session-item:not(.analysis-item)', { hasText: '轨迹分析: ' }))
      .toHaveCount(0, { timeout: 10_000 })
    // 展开子分组：分析会话可见且带关联目标
    await groupHead.click()
    const analysisItem = page.locator('.analysis-item', { hasText: '轨迹分析: ' }).first()
    await expect(analysisItem).toBeVisible({ timeout: 10_000 })
    await expect(analysisItem).toHaveAttribute('title', /分析目标：sess_/)

    // ⑤ 关联：目标会话的审计抽屉显示分析历史，可打开分析会话
    await openAdminSessions(page)
    await openAuditDrawerFor(page, title)
    const drawer = page.locator('.el-drawer', { hasText: '执行合规审计' })
    await expect(drawer.locator('h4', { hasText: '轨迹分析历史' }))
      .toBeVisible({ timeout: 20_000 })
    await expect(drawer.locator('.analysis-row').first()).toBeVisible()
    await expect(drawer.getByText('打开会话').first()).toBeVisible()
    void input
  } finally {
    seedAudit(['cleanup', title])
  }
})
