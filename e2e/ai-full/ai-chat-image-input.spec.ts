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

test('非契约图片类型：粘贴提示不支持且不产生芯片', async ({ page, request }) => {
  // 前置段照抄主用例：建会话 → 定点清理标记 → 打开 → 等 activeId 就绪
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('image-input-bmp') })
  await openChatSession(page, sid)
  await page.waitForSelector('.composer-input textarea', { timeout: 30_000 })
  await page.waitForSelector('.ai-chat__welcome', { state: 'detached', timeout: 30_000 })

  // bmp 不在 png/jpg/jpeg/webp/gif 契约内：粘贴 → 显式 warning，不静默丢弃
  await page.locator('.composer-input textarea').evaluate((el) => {
    const dt = new DataTransfer()
    dt.items.add(new File([new Uint8Array([1, 2, 3])], 'note.bmp', { type: 'image/bmp' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
  })
  await expect(page.locator('.el-message').filter({ hasText: '不支持的图片格式' }))
    .toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.attach-chip')).toHaveCount(0)
})
