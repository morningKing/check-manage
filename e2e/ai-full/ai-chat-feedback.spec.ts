/**
 * 消息点赞/点踩 E2E（2026-10-30 第一档 Feature A）：真实链路——
 * API 建会话 + 真实 OpenCode 回复落库 → UI 点赞 → 刷新保留 → 切踩 → 清除。
 */
import { test, expect } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'

test.setTimeout(420_000)

test('消息反馈：点赞持久化 + 切换 + 清除', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('feedback') })
  await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请直接回复四个字符：GOOD，不要输出任何其他内容。',
  })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant') ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })

  await openChatSession(page, sid)
  const up = page.locator('[data-test="feedback-up"]').last()
  await expect(up).toBeVisible({ timeout: 30_000 })
  await up.click()
  // API 侧确认落库
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const last = (r.json?.messages || []).filter((m: any) => m.role === 'assistant').pop()
    return last?.rating === 1 ? true : null
  }, { timeoutMs: 15_000, intervalMs: 1000 })
  // 刷新保留
  await page.reload()
  await expect(page.locator('[data-test="feedback-up"]').last(),
    '刷新后仍高亮').toHaveClass(/feedback-on/, { timeout: 30_000 })
  // 切踩 → 清除
  await page.locator('[data-test="feedback-down"]').last().click()
  await expect(page.locator('[data-test="feedback-down"]').last(),
    '切踩后踩高亮').toHaveClass(/feedback-on/)
  await expect(page.locator('[data-test="feedback-up"]').last(),
    '切踩后赞取消').not.toHaveClass(/feedback-on/)
  await page.locator('[data-test="feedback-down"]').last().click()
  await expect(page.locator('[data-test="feedback-down"]').last(),
    '再点取消高亮').not.toHaveClass(/feedback-on/)
})
