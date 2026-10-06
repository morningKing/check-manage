/**
 * AI 域入口导航冒烟（docs/ai-testing 交付物）。
 *
 * 背景：AI 菜单 8→4 收敛后（ai-settings/ai-skills/ai-execution 域入口 + ai-scan），
 * 5 条旧路径经路由重定向落到对应入口：
 *   /admin/ai-batches|sessions|orchestrations → /admin/ai-execution?tab=*
 *   /admin/ai-opencode → /admin/ai-settings?tab=runtime
 *   /admin/ai-skillopt → /admin/ai-skills（技能广场并入运行时后单页化为拟合优化，无 tab）
 * 同时断言「AI 能力」组侧边栏收敛为 4 项，且运行时页承载平台技能/MCP 服务页签。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth } from './helpers'

test.setTimeout(120_000)

const CASES = [
  ['/admin/ai-batches', '/admin/ai-execution', 'batches', '批量执行'],
  ['/admin/ai-sessions', '/admin/ai-execution', 'sessions', '会话审计'],
  ['/admin/ai-orchestrations', '/admin/ai-execution', 'orchestrations', '编排管理'],
  ['/admin/ai-opencode', '/admin/ai-settings', 'runtime', '运行时'],
] as const

for (const [from, path, tab, label] of CASES) {
  test(`旧路径 ${from} 重定向到 ${path}?tab=${tab}`, async ({ page }) => {
    await gotoWithAuth(page, from)
    await expect(page).toHaveURL(new RegExp(`${path.replace(/\//g, '\\/')}\\?tab=${tab}`))
    // 内页组件（AiSkillOpt/AiOrchestrationManager/AiOpencodeRuntime）自带嵌套
    // el-tabs，且 pane 是壳层后代 —— 用直接子级 header 限定壳层自己的页签。
    await expect(page.locator('.settings-tab-shell > .el-tabs__header .el-tabs__item.is-active')).toContainText(label)
  })
}

test('旧路径 /admin/ai-skillopt 重定向到单页化的技能拟合优化（不带 tab）', async ({ page }) => {
  await gotoWithAuth(page, '/admin/ai-skillopt')
  await expect(page).toHaveURL(/\/admin\/ai-skills$/)
  // 单页条目不再有壳层页签，直接渲染 SkillOpt 页头
  await expect(page.locator('.skillopt__title')).toContainText('技能优化')
})

test('AI 能力组侧边栏收敛为 4 项', async ({ page }) => {
  await gotoWithAuth(page, '/admin/ai-execution')
  const group = page.locator('.settings-menu__group', { hasText: 'AI 能力' })
  await expect(group.locator('a', { hasText: 'AI 配置' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 定时巡检' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: '技能拟合优化' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 技能' })).toHaveCount(0)
  await expect(group.locator('a', { hasText: 'AI 执行中心' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 批量执行' })).toHaveCount(0)
})

test('运行时页承载平台技能与 MCP 服务页签', async ({ page }) => {
  await gotoWithAuth(page, '/admin/ai-settings?tab=runtime')
  // 运行时页自身的 el-tabs 嵌在壳层 pane 内 —— 用 .oc-runtime 限定，避免命中壳层页签
  const tabs = page.locator('.oc-runtime .el-tabs__item')
  await expect(tabs.filter({ hasText: '技能 (Skill)' })).toHaveCount(1)
  await expect(tabs.filter({ hasText: '智能体 (Agent)' })).toHaveCount(1)

  await tabs.filter({ hasText: '平台技能' }).click()
  await expect(page.locator('[data-test="platform-skill-upload"]')).toBeVisible()
  await expect(page.locator('.oc-runtime .platform-skills .el-table__row').first()).toBeVisible()

  await tabs.filter({ hasText: 'MCP 服务' }).click()
  await expect(page.locator('.oc-runtime .mcp-card')).toBeVisible()
  // 内置 MCP 行在位（开关 + 检测连接）
  await expect(page.locator('.oc-runtime .mcp-card .internal-mcp')).toBeVisible()
})
