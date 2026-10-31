/**
 * bypass 会话权限自动放行 E2E（spec 2026-10-31 §3，真实链路）：
 * bypass 开 → external_directory 询问自动放行 → 权限卡绝不出现、
 * 「已自动放行」chip 出现、回合正常完成。
 */
import { test, expect } from '@playwright/test'
import { api, tag, waitFor } from './helpers'

test.setTimeout(300_000)

test('bypass：external_directory 询问自动放行，无卡、chip 出现、回合完成',
     async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  expect(created.status).toBeLessThan(300)
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('bypass') })
  const toggled = await api(request, 'PATCH',
                            `/ai/chat/sessions/${sid}/permission-mode`, { mode: 'bypass' })
  expect(toggled.status).toBeLessThan(300)

  // 真实 UI 登录（照 ai-permission-ask 先例，规避 .auth 过期风险），直开会话
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForTimeout(2000)
  await page.goto(`/ai-chat?session=${sid}`)
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 60_000 })
  // 显式 60s：worker 首个用例的冷 context 上，会话打开链路（列表加载+open+
  // SSE）可超默认 5s——本用例跑在 worker 第一位，实测两次波次均卡在这。
  // 等待的元素与文本不变，只放宽环境延迟预算（2026-10-31 遗留清理波实测）。
  await expect(page.locator('[data-test="bypass-toggle"]'))
    .toContainText('Bypass', { timeout: 60_000 })

  await input.fill(
    '必须真的调用 read 工具读取文件 C:\\Windows\\System32\\drivers\\etc\\hosts '
    + '的前 3 行，不要凭记忆回答，然后把读到的内容原样贴出来。')
  await page.getByRole('button', { name: '发送' }).click()

  // chip 出现 = 询问已发生且被自动放行；随后权限卡必须恒为 0
  await expect(page.locator('[data-test="auto-approved-chip"]'))
    .toBeVisible({ timeout: 180_000 })
  await expect(page.locator('[data-test="permission-card"]')).toHaveCount(0)

  // 回合正常完成（服务端落库为准）
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant' &&
      JSON.stringify(m.content || '').includes('hosts')) ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })
})
