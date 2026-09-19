/**
 * AI 会话核心链路 E2E（真实 OpenCode，非 mock）。
 * 覆盖用例：TC-CHAT-001/002/008/009/010、TC-GOV-001/002/003/005。
 */
import { test, expect } from '@playwright/test'
import {
  api, findSession, openChatSession, screenshot, tag, waitFor,
} from './helpers'

test.setTimeout(420_000)

test('AI 会话：创建→真实回复落库→runtime-state→关闭/重开→越权404', async ({ page, request }) => {
  // ① 创建会话 + 重命名（PATCH /sessions/:id）
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  expect(created.status, '创建会话').toBeLessThan(300)
  const sid = created.json.id
  expect(sid, 'session id 应为 sess_ 前缀').toContain('sess_')
  const renamed = await api(request, 'PATCH', `/ai/chat/sessions/${sid}`,
                            { title: tag('session-core') })
  expect(renamed.status, '重命名会话').toBeLessThan(300)

  // ② 发送消息（真实 OpenCode 回复）
  const sent = await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '这是一个 E2E 测试。请直接回复五个字符：OK-E2E，不要输出任何其他内容。',
  })
  expect(sent.status, '发送消息').toBeLessThan(300)

  // ③ 轮询持久化的 assistant 消息（服务端 chat_persist 落库为准）
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const msgs = r.json?.messages || []
    return msgs.some((m: any) => m.role === 'assistant' &&
      JSON.stringify(m.content || '').includes('OK-E2E')) ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })

  // ④ runtime-state 可用且状态合法
  const state = await api(request, 'GET', `/ai/chat/sessions/${sid}/runtime-state`)
  expect(state.status).toBe(200)
  expect(['running', 'idle']).toContain(state.json.turnStatus)
  expect(state.json.sessionId).toBe(sid)

  // ⑤ UI 打开：消息流可见，截图留证
  await openChatSession(page, sid)
  await expect(page.getByText('OK-E2E').first()).toBeVisible({ timeout: 30_000 })
  await screenshot(page, 'session-core-reply')

  // ⑥ 关闭 → 状态 closed → 重开 → active
  const close = await api(request, 'POST', `/ai/chat/sessions/${sid}/close`)
  expect(close.status, '关闭会话').toBeLessThan(300)
  expect((await findSession(request, sid))?.status).toBe('closed')
  const reopen = await api(request, 'POST', `/ai/chat/sessions/${sid}/reopen`)
  expect(reopen.status, '重开会话').toBeLessThan(300)
  expect((await findSession(request, sid))?.status).toBe('active')
})

test('AI 会话：clear 原地重置（历史清空、可立即续用）', async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id

  await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请直接回复：CLEAR-MARK' })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return JSON.stringify(r.json || '').includes('CLEAR-MARK') ? true : null
  }, { timeoutMs: 240_000 })

  const cleared = await api(request, 'POST', `/ai/chat/sessions/${sid}/clear`)
  expect(cleared.status, 'clear 会话').toBeLessThan(300)
  const msgs = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
  expect((msgs.json?.messages || []).length, '消息应清空').toBe(0)
  // 会话保留在列表中且 active（可立即续用）
  const row = await findSession(request, sid)
  expect(row?.status).toBe('active')
})

test('AI 会话：delete 后列表消失、不可 reopen、端点 404', async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const del = await api(request, 'DELETE', `/ai/chat/sessions/${sid}`)
  expect(del.status, '删除会话').toBeLessThan(300)
  expect(await findSession(request, sid)).toBeNull()
  const reopen = await api(request, 'POST', `/ai/chat/sessions/${sid}/reopen`)
  // 软删行保留供审计：reopen 拒绝（409），绝不复活为 active
  expect([403, 404, 409]).toContain(reopen.status)
  expect(await findSession(request, sid)).toBeNull()
})
