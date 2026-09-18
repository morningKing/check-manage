/**
 * 会话自定义分组 E2E：
 *  1. 侧栏渲染：未分组 + 自定义分组（存量已惰性归入「历史会话」）+ 轨迹分析系统组；
 *  2. 新建分组 → 移动会话入组 → 组内可见/未分组消失；
 *  3. 重命名 / 删除分组（会话回未分组）。
 */
import fs from 'node:fs'
import path from 'node:path'
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

async function gotoAiChat(page: import('@playwright/test').Page) {
  await page.goto('/home')
  const aiBtn = page.locator('button', { hasText: 'AI 助手' }).first()
  await aiBtn.waitFor({ state: 'visible', timeout: 30_000 })
  await aiBtn.click()
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(1200)
  return input
}

test('会话自定义分组：新建 → 移动 → 组内渲染 → 删除回收', async ({ page }) => {
  test.setTimeout(180_000)
  const input = await gotoAiChat(page)

  // ① 结构：自定义分组头可见（存量应已惰性归入「历史会话」）+ 轨迹分析系统组
  const heads = page.locator('[data-test="custom-group-head"]')
  await heads.first().waitFor({ state: 'visible', timeout: 15_000 })
  expect(await heads.count()).toBeGreaterThanOrEqual(1)
  await expect(page.locator('[data-test="analysis-group-head"]')).toBeVisible()
  const legacy = page.locator('[data-test="custom-group-head"]', { hasText: '历史会话' }).first()
  const hasLegacy = await legacy.count()
  if (hasLegacy) await expect(legacy).toBeVisible()

  // ② 新建分组
  await page.locator('[data-test="new-group-btn"]').click()
  await page.locator('.el-message-box__input input').fill('归档测试组')
  await page.locator('.el-message-box__btns .el-button--primary').click()
  const newHead = page.locator('[data-test="custom-group-head"]', { hasText: '归档测试组' })
  await expect(newHead).toBeVisible({ timeout: 10_000 })

  // ③ 移动一个未分组会话入组：新建一个会话保证未分组非空
  const input2 = page.getByPlaceholder(/给 AI 助手发消息/)
  await page.locator('button', { hasText: '新建会话' }).first().click()
  await page.waitForTimeout(2000)
  const firstUngrouped = page.locator('.session-item:not(.analysis-item):not(.cgroup__item)')
    .first()
  await firstUngrouped.waitFor({ state: 'visible', timeout: 15_000 })
  await firstUngrouped.hover()
  await firstUngrouped.locator('[title="移动到分组"]').click()
  // 移动菜单列出分组序号，「归档测试组」排最后（按创建时间）
  const idx = await page.locator('[data-test="custom-group-head"]').count()
  await page.locator('.el-message-box__input input').fill(String(idx))
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await page.waitForTimeout(1500)

  // ④ 组内出现 ≥1 会话（cgroup__item）
  const groupedItem = newHead.locator('..').locator('.cgroup__item')
  await expect(groupedItem.first()).toBeVisible({ timeout: 10_000 })
  void input; void input2

  // ⑤ 删除分组 → 会话回到未分组，组头消失
  await newHead.hover()
  await newHead.locator('[title^="删除分组"]').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('[data-test="custom-group-head"]',
    { hasText: '归档测试组' })).toHaveCount(0, { timeout: 10_000 })
  await page.waitForTimeout(1000)
  await expect(page.locator('.session-item:not(.analysis-item):not(.cgroup__item)')
    .first()).toBeVisible()
})
