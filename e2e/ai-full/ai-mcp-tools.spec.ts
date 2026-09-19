/**
 * 内置 MCP 能力 E2E（真实 OpenCode + 真实 MCP Server :3003）。
 * 覆盖用例：TC-MCP-001/002/004/008、TC-MEM-005。
 *
 * 策略：通过真实会话让 Agent 调用 MCP 数据工具，验证「会话 → OpenCode →
 * MCP → Flask」全链路；工具气泡渲染以前端组件测试与注入式 spec 兜底。
 */
import { test, expect } from '@playwright/test'
import {
  api, findSession, openChatSession, screenshot, tag, waitFor,
} from './helpers'

test.setTimeout(600_000)

test('MCP 数据工具：Agent 真实调用 list_collections 并渲染工具气泡',
     async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id

  const sent = await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请调用 list_collections 工具查看当前平台有哪些数据集合，'
           + '然后用一句话告诉我集合总数（形如“共 N 个集合”）。不要做别的事情。',
  })
  expect(sent.status, '发送 MCP 指令').toBeLessThan(300)

  // 等待回合完成（assistant 消息包含「个集合」或工具已调用）
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const s = JSON.stringify(r.json || '')
    return (s.includes('list_collections') &&
            (s.includes('个集合') || s.includes('共 '))) ? true : null
  }, { timeoutMs: 480_000, intervalMs: 5000 })

  // UI：打开会话，工具气泡应出现（tool-call 节点），并截图留证
  await openChatSession(page, sid)
  const bubble = page.locator('.tool-call').first()
  await expect(bubble).toBeVisible({ timeout: 30_000 })
  const summary = await page.locator('.tool-call__summary').first().textContent()
  console.log('tool bubble summary:', summary)
  await screenshot(page, 'mcp-list-collections-bubble')
})

test('MCP 记忆通道：内部端点未带/错 token 一律 403（TC-MEM-005）', async ({ request }) => {
  const noToken = await request.fetch('/api/ai/memory/internal/search', {
    method: 'POST', data: JSON.stringify({ userId: 'u', query: 'q' }),
    headers: { 'Content-Type': 'application/json' },
  })
  expect(noToken.status()).toBe(403)
  const badToken = await request.fetch('/api/ai/memory/internal/search', {
    method: 'POST', data: JSON.stringify({ userId: 'u', query: 'q' }),
    headers: { 'Content-Type': 'application/json', 'X-Internal-Token': 'wrong' },
  })
  expect(badToken.status()).toBe(403)
})

test('MCP 会话 mcp 配置端点：/sessions/:id/mcp 返回内置服务（/mcps 命令数据源）',
     async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/mcp`)
  expect(r.status).toBe(200)
  // 平台内置 MCP（name=check-manage）+ 工具清单 → /mcps 命令确定性渲染的数据源
  const servers = r.json?.servers || []
  const builtin = servers.find((s: any) => s.name === 'check-manage')
  expect(builtin, '应包含内置 check-manage MCP 服务').toBeTruthy()
  const toolNames = (builtin.tools || []).map((t: any) => t.name)
  expect(toolNames).toContain('list_collections')
  expect(toolNames).toContain('query_collection')
  await api(request, 'DELETE', `/ai/chat/sessions/${sid}`)
})
