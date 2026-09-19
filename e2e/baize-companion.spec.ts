/**
 * 白泽数字人助手 E2E：
 *  1. 半透明悬浮形象可见（SVG 降级形象，模型未配置时）；
 *  2. 点击展开对话面板 → 发送消息 → 收到流式回复（白泽大脑）；
 *  3. 最小化/恢复；
 *  4. 主动指导气泡（路由映射）出现且可关闭。
 */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from '@playwright/test'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

test.beforeEach(async ({ context }) => {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await context.addInitScript((entries) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

async function gotoHome(page: import('@playwright/test').Page) {
  await page.goto('/home')
  await page.getByRole('button', { name: /AI 助手|首页/ }).first()
    .waitFor({ state: 'visible', timeout: 30_000 })
}

test('数字人助手：形象可见 + 点击展开对话 + 收到回复', async ({ page }) => {
  test.setTimeout(180_000)
  await gotoHome(page)
  const companion = page.locator('[data-test="baize-companion"]')
  await expect(companion).toBeVisible({ timeout: 15_000 })
  // 半透明
  const opacity = await companion.evaluate(el => parseFloat(getComputedStyle(el).opacity))
  expect(opacity).toBeLessThan(1)

  // 点击形象展开面板
  await page.locator('[data-test="baize-stage"]').click()
  const panel = page.locator('[data-test="baize-panel"]')
  await expect(panel).toBeVisible({ timeout: 10_000 })
  await expect(panel).toContainText('白泽小助手')

  // 发送消息 → 白泽大脑回复
  await page.locator('[data-test="baize-input"]').fill('你好，简单介绍一下你自己')
  await page.locator('[data-test="baize-send"]').click()
  const reply = page.locator('.panel__msg.is-assistant').last()
  await expect(reply).toBeVisible({ timeout: 90_000 })
  const text = await reply.innerText()
  expect(text.trim().length).toBeGreaterThan(0)

  // 最小化 → 悬浮球 → 恢复
  await companion.evaluate(el => { localStorage.setItem('baize-companion:minimized', '1') })
  await page.reload()
  await page.locator('[data-test="baize-fab"]').waitFor({ state: 'visible', timeout: 20_000 })
  await page.locator('[data-test="baize-fab"]').click()
  await expect(page.locator('[data-test="baize-stage"]')).toBeVisible({ timeout: 10_000 })
})

test('主动指导：路由映射气泡出现且可关闭', async ({ page }) => {
  await gotoHome(page)
  // 首次进入首页的指导气泡（localStorage 无 last-tip 时）
  const tip = page.locator('[data-test="baize-tip"]')
  await expect(tip.first()).toBeVisible({ timeout: 20_000 })
  // 关闭后不再出现（同路由）
  await tip.first().locator('.companion__tip-close').click()
  await page.waitForTimeout(500)
  await expect(page.locator('[data-test="baize-tip"]')).toHaveCount(0)
})
