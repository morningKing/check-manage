/**
 * SkillOpt 任务拟合页冒烟（定义主从布局）——纯 UI，不烧 LLM。
 * 设计: docs/design/ai/SkillOpt任务拟合页面重设计.md
 * 约定: 等真实元素,绝不等 networkidle;空库时以空状态文案即视为通过。
 */
import { test, expect, type Page } from '@playwright/test'
import fs from 'node:fs'

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch {
    // 已是登录态(首页无登录表单)——直接继续
  }
}

test('主从布局：左栏列表或空状态 + 子标签切换', async ({ page }) => {
  test.setTimeout(180_000)
  await login(page)
  await page.goto('/admin/ai-skillopt')
  await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
  // vite 冷编译可能触发整页重载——元素迟迟不出现就重试最多 3 次
  const firstDef = page.locator('.fit-def').first()
  const empty = page.getByText('暂无拟合数据')
  const mask = page.locator('.skillopt__pane .el-loading-mask')
  for (let i = 0; i < 3; i++) {
    if (await firstDef.isVisible().catch(() => false)) break
    // 加载中 definitions 为空,空状态文案会先闪现——须等加载遮罩退场后再判定空库
    await mask.waitFor({ state: 'visible', timeout: 5_000 }).catch(() => {})
    await mask.waitFor({ state: 'hidden', timeout: 30_000 })
    if (await firstDef.isVisible().catch(() => false)) break
    if (await empty.isVisible().catch(() => false)) return  // 空库：空状态即通过
    await page.goto('/admin/ai-skillopt')
  }
  await firstDef.waitFor({ state: 'visible', timeout: 30_000 })
  await firstDef.click()
  await expect(page.locator('.def-ov')).toBeVisible()
  for (const name of ['版本演进', '偏离模式', '拟合结果']) {
    await page.getByRole('tab', { name }).click()
    await expect(
      page.locator('.fit-layout__main').getByRole('tabpanel').first(),
    ).toBeVisible()
  }
  fs.mkdirSync('e2e/screenshots/ai-full', { recursive: true })
  await page.screenshot({ path: 'e2e/screenshots/ai-full/skillopt-redesign.png',
                          fullPage: true })
})
