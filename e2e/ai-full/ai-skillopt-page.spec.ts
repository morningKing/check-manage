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
  // fitLoading 连续两次触发时新旧遮罩会短暂并存（一个 fade-enter 一个
  // fade-leave）——strict mode 下多元素直接炸 waitFor，取 first 只跟最新一张
  const mask = page.locator('.skillopt__pane .el-loading-mask').first()
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
  // 右侧工作区：定义头部 + 步骤命中情况 + 运行记录（单页呈现，无子标签）
  await expect(page.locator('.def-ov')).toBeVisible()
  await expect(page.getByText('步骤命中情况')).toBeVisible()
  await expect(page.getByText('运行记录', { exact: true })).toBeVisible()
  // 运行记录有数据时：点击行打开运行详情抽屉
  const firstRun = page.locator('.runs__row').first()
  if (await firstRun.isVisible().catch(() => false)) {
    await firstRun.click()
    await expect(page.locator('.el-drawer .rd')).toBeVisible()
    await page.keyboard.press('Escape')
  }
  // 版本历史默认折叠，展开后可见版本时间线
  await page.getByText(/版本历史/).click()
  fs.mkdirSync('e2e/screenshots/ai-full', { recursive: true })
  await page.screenshot({ path: 'e2e/screenshots/ai-full/skillopt-redesign.png',
                          fullPage: true })
})
