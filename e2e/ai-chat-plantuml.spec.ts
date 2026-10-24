/**
 * AI 会话 PlantUML 渲染 E2E —— 真实链路：真实前端/后端/DB 种子会话加载，
 * MarkdownView 真实渲染；仅 PlantUML 服务器在 HTTP 层 stub（浏览器真实发起
 * GET /svg/~h… 请求，由 Playwright 路由拦截回桩 SVG 或 abort）。
 *
 * 种子纪律：AITEST 前缀 + finally cleanupSessionsByPrefix。
 * 证据截图：e2e/screenshots/ai-full/plantuml-*.png。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import { AUTH_FILE, openChatSession, screenshot } from './ai-full/helpers'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from './ai-full/db-helpers'

const DIAGRAM = '@startuml\nBob -> Alice: e2e hello\n@enduml'
const HEX = Buffer.from(DIAGRAM, 'utf8').toString('hex')
const STUB_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="64">' +
  '<rect width="220" height="64" fill="#e8f4ff"/><text x="14" y="38" font-size="16">PUML-E2E-STUB</text></svg>'

test.setTimeout(120_000)

/** 共享登录态（e2e/.auth/admin.json）过期兜底：登录页出现则 UI 登录并写回。 */
async function ensureLogin(page: import('@playwright/test').Page) {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  if (!(await userInput.isVisible({ timeout: 4_000 }).catch(() => false))) return
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /AI 助手/ }).waitFor({ timeout: 20_000 })
  // 写回共享登录态（openChatSession 的 addInitScript 按原始字符串逐键恢复）
  const entries = await page.evaluate(() => {
    const out: Record<string, string> = {}
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)!
      out[k] = localStorage.getItem(k)!
    }
    return out
  })
  fs.writeFileSync(AUTH_FILE, JSON.stringify(entries))
}

test('种子会话 PlantUML 围栏 → 向服务器发起 ~h hex 渲染请求 → img 内联展示', async ({ page }) => {
  const sid = await seedPlainSession({
    key: newId('puml-'),
    messages: [
      { role: 'user', text: '请画一张 PlantUML 时序图' },
      { role: 'assistant', text: `时序图如下：\n\n\`\`\`plantuml\n${DIAGRAM}\n\`\`\`\n\n完成。` },
    ],
  })
  const pumlRequests: string[] = []
  await page.route(/plantuml\.com\/plantuml\//, (route) => {
    pumlRequests.push(route.request().url())
    return route.fulfill({ contentType: 'image/svg+xml', body: STUB_SVG })
  })
  try {
    await ensureLogin(page)
    await openChatSession(page, sid)
    const img = page.locator('.markdown-view__puml-img')
    await expect(img).toBeVisible({ timeout: 20_000 })
    // 浏览器真的向 PlantUML 服务器发起了渲染请求，且 ~h hex 与源码 UTF-8 一致
    await expect.poll(() => pumlRequests.some((u) => u.endsWith(`/svg/~h${HEX}`))).toBe(true)
    await screenshot(page, 'plantuml-render')
  } finally {
    cleanupSessionsByPrefix()
  }
})

test('PlantUML 服务不可达 → 降级卡；改自建服务地址重试后恢复渲染', async ({ page }) => {
  const sid = await seedPlainSession({
    key: newId('puml-err-'),
    messages: [
      { role: 'assistant', text: `图：\n\n\`\`\`puml\n${DIAGRAM}\n\`\`\`` },
    ],
  })
  const retried: string[] = []
  // 自建服务桩（重试目标）与官方服务不可达桩
  await page.route(/intranet-puml\.test/, (route) => {
    retried.push(route.request().url())
    return route.fulfill({ contentType: 'image/svg+xml', body: STUB_SVG })
  })
  await page.route(/plantuml\.com\/plantuml\//, (route) => route.abort())
  try {
    await ensureLogin(page)
    await openChatSession(page, sid)
    const err = page.locator('.markdown-view__puml-err')
    await expect(err).toBeVisible({ timeout: 20_000 })
    await expect(err).toContainText('@startuml')
    await expect(err).toContainText('自建 PlantUML 服务')
    // 改指向自建服务并重试 → img 恢复，请求打到自建桩且 hex 正确
    await err.locator('input').fill('http://intranet-puml.test/plantuml/')
    await err.locator('.markdown-view__puml-retry').click()
    const img = page.locator('.markdown-view__puml-img')
    await expect(img).toBeVisible({ timeout: 20_000 })
    await expect.poll(() =>
      retried.some((u) => u.endsWith(`/svg/~h${HEX}`))).toBe(true)
    await screenshot(page, 'plantuml-fallback-retry')
  } finally {
    cleanupSessionsByPrefix()
  }
})
