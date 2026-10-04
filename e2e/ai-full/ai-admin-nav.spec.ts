/**
 * AI 域入口导航冒烟（docs/ai-testing 交付物）。
 *
 * 背景：AI 菜单 8→4 收敛后（ai-settings/ai-skills/ai-execution 域入口 + ai-scan），
 * 5 条旧路径经路由重定向落到对应域入口页签：
 *   /admin/ai-batches|sessions|orchestrations → /admin/ai-execution?tab=*
 *   /admin/ai-opencode → /admin/ai-settings?tab=runtime
 *   /admin/ai-skillopt → /admin/ai-skills?tab=fit
 * 同时断言「AI 能力」组侧边栏收敛为 4 项。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth } from './helpers'

test.setTimeout(120_000)

const CASES = [
  ['/admin/ai-batches', '/admin/ai-execution', 'batches', '批量执行'],
  ['/admin/ai-sessions', '/admin/ai-execution', 'sessions', '会话审计'],
  ['/admin/ai-orchestrations', '/admin/ai-execution', 'orchestrations', '编排管理'],
  ['/admin/ai-opencode', '/admin/ai-settings', 'runtime', '运行时'],
  ['/admin/ai-skillopt', '/admin/ai-skills', 'fit', '拟合优化'],
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

test('AI 能力组侧边栏收敛为 4 项', async ({ page }) => {
  await gotoWithAuth(page, '/admin/ai-execution')
  const group = page.locator('.settings-menu__group', { hasText: 'AI 能力' })
  await expect(group.locator('a', { hasText: 'AI 配置' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 定时巡检' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 技能' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 执行中心' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 批量执行' })).toHaveCount(0)
})
