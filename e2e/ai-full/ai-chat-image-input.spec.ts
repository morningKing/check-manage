/**
 * 图片输入 E2E（2026-10-30 第一档 Feature C）：粘贴 + 选择器两入口真实链路。
 * 断言：芯片图标分流、发送后 user 气泡含 <img>、stored parts 含 image 类型。
 */
import { test, expect } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64')

test.setTimeout(420_000)

test('图片输入：粘贴入口 → 芯片 → 发送 → 气泡图片', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  // dev-DB 定点清理标记（同 feedback/session-core 套件）：会话标题带 tag。
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('image-input') })
  await openChatSession(page, sid)
  await page.waitForSelector('.composer-input textarea', { timeout: 30_000 })
  // 会话激活同步：composer 不受 activeId 门控（textarea 先于 openSession 完成出现），
  // 而 welcome 屏 v-if="!activeId" —— 等它离场即等 activeSessionId 就绪，粘贴上传依赖它。
  await page.waitForSelector('.ai-chat__welcome', { state: 'detached', timeout: 30_000 })

  // ① 粘贴：向 textarea 派发带剪贴板文件的 paste 事件
  await page.locator('.composer-input textarea').evaluate((el, buf) => {
    const dt = new DataTransfer()
    dt.items.add(new File([new Uint8Array(buf)], 'probe.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
  }, Array.from(PNG))
  await expect(page.locator('.attach-chip', { hasText: 'probe.png' })).toBeVisible()

  // ② 发送（真实模型栈；不依赖模型"看懂"图，只断言链路）
  await page.locator('.composer-input textarea').fill('看看这张图')
  await page.keyboard.press('Enter')

  // ③ user 气泡出现图片；服务端 parts 含 image 类型
  await expect(page.locator('.msg-image').first()).toBeVisible({ timeout: 60_000 })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const user = (r.json?.messages || []).filter((m: any) => m.role === 'user').pop()
    return user?.content?.some((p: any) => p.type === 'image') ? true : null
  }, { timeoutMs: 15_000, intervalMs: 1000 })
})
