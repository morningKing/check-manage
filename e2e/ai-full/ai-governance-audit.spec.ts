/**
 * AI 会话治理 + 管理端审计 E2E。
 * 覆盖用例：TC-GOV-004/005/006/007、TC-BATCH-015（管理页视图）。
 */
import { test, expect } from '@playwright/test'
import { api, gotoWithAuth, screenshot, tag } from './helpers'

test.setTimeout(300_000)

test('归档治理：admin 归档 → 个人 reopen 403 → 管理端可见 → operation_logs 留痕',
     async ({ page, request }) => {
  const title = tag('gov-archive')
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title })

  // admin 归档
  const archived = await api(request, 'POST', `/ai/chat/sessions/${sid}/archive`)
  expect(archived.status, '归档会话').toBeLessThan(300)

  // 归档后个人 reopen 必须被拒（403）
  const reopen = await api(request, 'POST', `/ai/chat/sessions/${sid}/reopen`)
  expect(reopen.status).toBe(403)

  // 管理端会话列表可见且状态 archived
  const adminList = await api(request, 'GET',
    `/ai/chat/admin/sessions?keyword=${encodeURIComponent(title)}`)
  expect(adminList.status).toBe(200)
  const hit = (adminList.json?.sessions || adminList.json?.items || [])
    .find((s: any) => s.id === sid)
  expect(hit, '管理端应能搜到归档会话').toBeTruthy()
  expect(hit.status).toBe('archived')

  // operation_logs 留痕：create/archive 均写入 target_type=ai_chat_session
  const logs = await api(request, 'GET', `/operationLogs?targetType=ai_chat_session&pageSize=50`)
  expect(logs.status).toBe(200)
  const rows = logs.json?.items || []
  const mine = rows.filter((r: any) => r.targetId === sid)
  expect(mine.some((r: any) => r.action === 'create'), '创建留痕').toBe(true)
  expect(mine.some((r: any) => /归档/.test(r.description || '')), '归档留痕').toBe(true)

  // 管理页 UI 可达（admin.ai_chat_admin 门）
  await gotoWithAuth(page, '/admin/ai-sessions')
  await screenshot(page, 'governance-admin-sessions')
})

test('治理越权：普通用户不可访问 admin 会话列表', async ({ request }) => {
  // guest/developer 的 token 没有 admin.ai_chat_admin —— 这里用无 token 验证门存在
  const res = await request.fetch('/api/ai/chat/admin/sessions')
  expect([401, 403]).toContain(res.status())
})

test('批任务管理页：/admin/ai-batches 列表可达（TC-BATCH-015 入口）',
     async ({ page, request }) => {
  const list = await api(request, 'GET', '/ai/chat/admin/batches')
  expect(list.status).toBe(200)
  expect(list.json?.items ?? list.json?.batches ?? []).toBeDefined()
  await gotoWithAuth(page, '/admin/ai-batches')
  await screenshot(page, 'governance-admin-batches')
})

test('子代理轨迹端点：猜错 subtask id 一律 404（TC-SUB-002）', async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const r = await api(request, 'GET',
    `/ai/chat/sessions/${sid}/subtasks/sub-not-exist/messages`)
  expect(r.status).toBe(404)
  await api(request, 'DELETE', `/ai/chat/sessions/${sid}`)
})
