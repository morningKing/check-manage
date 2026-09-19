/**
 * 变更文件 md 双模式「真实会话」E2E —— 不 seed 任何文件：
 *   新建会话 → 发消息让 AI 真实写出 md 报告 → 等回合结束 →
 *   变更文件面板出现该 md → 预览抽屉默认「渲染预览」→ 切「diff 对照」→ 切回。
 * 依赖真实 LLM/工具链，分钟级耗时；测试结束后会话与文件保留，供人工复核。
 *
 * 证据截图输出到 e2e/screenshots/changed-md-real-*.png（已 gitignore）。
 */
import { test, expect } from '@playwright/test'

const PROMPT =
  '请把文件 report-e2e.md 直接创建在当前工作区根目录（不要放在 outputs、uploads 等' +
  '子目录）。内容：一级标题「E2E 真实会话验证」、一段说明文字、一个两行的表格、' +
  '一个 Python 代码块。直接完成，不要提问。'

test('真实会话：AI 写出 md → 变更文件面板 → 渲染/diff 双模式', async ({ page }) => {
  test.setTimeout(420_000)

  // ① 登录进 AI 会话
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /AI 助手/ }).click()
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1200)
  // 侧栏视觉留证（行内只保留 置顶 / 更多⋯ 两个操作）
  await page.locator('.session-item').first().hover()
  await page.waitForTimeout(400)
  await page.screenshot({ path: 'e2e/screenshots/session-sidebar-actions.png' })

  // ② 新建会话并发任务
  await page.locator('button', { hasText: '新建会话' }).first().click()
  await page.waitForTimeout(1500)
  await input.fill(PROMPT)
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--user').last()).toContainText('report-e2e.md')

  // ③ 等回合结束：占位符从「AI 正在回复…」回到「给 AI 助手发消息」
  await expect(page.getByPlaceholder(/给 AI 助手发消息/))
    .toBeVisible({ timeout: 360_000 })

  // ④ 轮询变更文件面板（点重新扫描），等 md 出现
  const row = page.locator('.change-file__row', { hasText: 'report-e2e.md' }).first()
  let found = false
  for (let i = 0; i < 12 && !found; i++) {
    const refresh = page.locator('.ai-changes__refresh')
    if (await refresh.count()) await refresh.click().catch(() => { /* ignore */ })
    await page.waitForTimeout(4000)
    found = await row.isVisible().catch(() => false)
  }
  expect(found, '变更文件面板应出现 AI 写出的 report-e2e.md').toBe(true)
  await page.screenshot({ path: 'e2e/screenshots/changed-md-real-panel.png' })

  // ⑤ 预览 → 抽屉打开，默认「渲染预览」：结构级断言（h1 渲染成 HTML）
  await row.getByRole('button', { name: '预览' }).click()
  const drawer = page.locator('.el-drawer').filter({ hasText: 'report-e2e.md' })
  await drawer.waitFor({ state: 'visible', timeout: 15_000 })
  const toggle = drawer.locator('[data-test="diff-mode-toggle"]')
  await expect(toggle).toBeVisible()
  const body = drawer.locator('.preview-body')
  await expect(body.locator('h1').first()).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: 'e2e/screenshots/changed-md-real-rendered.png' })

  // ⑥ 切「diff 对照」→ 源码 diff 态（渲染 h1 消失）
  await toggle.getByText('diff 对照').click()
  await expect(body.locator('.file-diff')).toBeVisible({ timeout: 10_000 })
  await expect(body.locator('.preview-body h1').first()).toHaveCount(0)
  await page.screenshot({ path: 'e2e/screenshots/changed-md-real-diff.png' })

  // ⑦ 切回「渲染预览」→ 排版恢复
  await toggle.getByText('渲染预览').click()
  await expect(body.locator('h1').first()).toBeVisible({ timeout: 10_000 })
})
