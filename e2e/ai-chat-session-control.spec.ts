import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * AI 会话控制 E2E（2026-10-04 更名自 ai-chat-stop-resume.spec.ts）。
 *
 * 批任务「停止全部/暂停全部 → 继续运行」两条批用例已收编至
 * e2e/ai-full/batch/ui-journeys.spec.ts（「停止续跑保留消息」/
 * 「暂停续跑保留消息」）；本文件只保留普通会话侧的用例：
 *
 * AI 会话回合失败提示：会话历史里持久化的 error part（session.error /
 * info.error 经 chat_persist 落库后的形态）在对话流里渲染成错误条。
 * 真实触发上游超时无法确定性复现，持久化侧逻辑由 server 端单测覆盖，
 * 这里验证可见性这一环。
 */

const INJECT = path.join(path.dirname(fileURLToPath(import.meta.url)),
                         'helpers', 'inject_error.py')

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })
}

/** token 存的是 JSON.stringify 后的字符串，读出来要 parse。 */
async function bearer(page: import('@playwright/test').Page) {
  const raw = await page.evaluate(() => localStorage.getItem('check-manage:token'))
  return `Bearer ${JSON.parse(raw!)}`
}

test('AI 会话：回合失败的 error part 渲染为错误提示条', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page)

  // 与前端同一端点建会话，拿到真实 session id
  const auth = await bearer(page)
  const res = await page.request.post('/api/ai/chat/sessions', {
    headers: { Authorization: auth },
    data: {},
  })
  expect(res.status()).toBe(201)
  const { id: sid } = await res.json()

  const msgId = 'msg_e2e_err_1'
  const errText = '本轮执行失败：工具调用超时（e2e 模拟）'
  try {
    execFileSync('python', [INJECT, 'insert', sid, msgId, errText], { encoding: 'utf-8' })

    await page.goto(`/ai-chat?session=${sid}`)
    const banner = page.locator('.msg__turn-error')
    await expect(banner).toHaveCount(1, { timeout: 20_000 })
    await expect(banner).toContainText(errText)
  } finally {
    // 清理：注入的消息行 + 会话本身（软删 + 工作区清理 + 杀 OpenCode 会话）
    try {
      execFileSync('python', [INJECT, 'cleanup', sid, msgId], { encoding: 'utf-8' })
    } catch { /* best-effort */ }
    try {
      await page.request.delete(`/api/ai/chat/sessions/${sid}`, {
        headers: { Authorization: auth },
      })
    } catch { /* best-effort */ }
  }
})
