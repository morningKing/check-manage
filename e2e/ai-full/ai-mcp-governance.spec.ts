/**
 * 内置 MCP 工具治理 E2E（2026-10-25 spec，真实栈 3002+3003+前端）。
 * 覆盖：矩阵 API roundtrip（PUT→GET 反映/非法 400/恢复原矩阵）、管理抽屉 UI
 * （分组渲染+开关+保存）、会话侧 /sessions/:id/mcp 工具带分组字段。
 * 不做「禁用后真调用被拒」的重链路断言——dispatch 拒绝语义归 mcp-server 单测
 * （test_rbac/test_tool_dispatch_allowlist），真调用链路已由 ai-mcp-tools.spec.ts 覆盖。
 */
import { test, expect } from '@playwright/test'
import { api, gotoWithAuth, screenshot } from './helpers'

test.setTimeout(180_000)

type Gov = { groups: any[]; roles: { role: string; label: string }[] }

function flatRules(gov: Gov) {
  return gov.groups.flatMap(g => g.tools).flatMap((t: any) =>
    Object.entries(t.rules).map(([role, allowed]) =>
      ({ role, tool_name: t.name, allowed })))
}

test('矩阵 API：GET 分组结构 / PUT 全量替换 / 非法 400 / 恢复', async ({ request }) => {
  const before = await api(request, 'GET', '/ai/mcp-tools')
  expect(before.status).toBe(200)
  const gov: Gov = before.json
  expect(gov.groups.length).toBeGreaterThanOrEqual(7)
  expect(gov.roles.map(r => r.role)).toEqual(
    ['admin', 'developer', 'guest', 'kefu-guest'])
  const all = gov.groups.flatMap(g => g.tools)
  expect(all.length).toBe(27)
  const runPython = all.find(t => t.name === 'run_python')
  expect(runPython.access).toBe('write')

  const bad = await api(request, 'PUT', '/ai/mcp-tool-rules',
    { rules: [{ role: 'hacker', tool_name: 'run_python', allowed: true }] })
  expect(bad.status).toBe(400)

  const orig = flatRules(gov)
  // 取当前值取反——对「此前已被管理员禁用」的状态也稳健
  const cur = orig.find(r => r.tool_name === 'run_python' && r.role === 'developer')!.allowed
  const mutated = orig.map(r =>
    r.tool_name === 'run_python' && r.role === 'developer' ? { ...r, allowed: !cur } : r)
  const put = await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: mutated })
  expect(put.status).toBe(200)
  expect(put.json.updated).toBe(1)

  const after = await api(request, 'GET', '/ai/mcp-tools')
  const rp2 = (after.json as Gov).groups.flatMap(g => g.tools)
    .find(t => t.name === 'run_python')
  expect(rp2.rules.developer).toBe(!cur)

  const restore = await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
  expect(restore.status).toBe(200)
})

test('会话侧：/sessions/:id/mcp 的 check-manage 工具带分组字段', async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/mcp`)
  expect(r.status).toBe(200)
  const ours = r.json.servers.find((s: any) => s.name === 'check-manage')
  expect(ours).toBeTruthy()
  const qc = ours.tools.find((t: any) => t.name === 'query_collection')
  expect(qc.group_name).toBe('数据查询')
  await api(request, 'POST', `/ai/chat/sessions/${sid}/close`, {})
})

test('管理抽屉 UI：分组渲染 + 开关 + 保存', async ({ page, request }) => {
  await gotoWithAuth(page, '/admin/ai-settings?tab=runtime')
  // 运行时页自身的 el-tabs 嵌在壳层 pane 内 —— 用 .oc-runtime 限定（照抄 ai-admin-nav.spec.ts）
  await page.locator('.oc-runtime .el-tabs__item').filter({ hasText: 'MCP 服务' }).click()
  await expect(page.locator('.oc-runtime .mcp-card')).toBeVisible()
  await page.getByRole('button', { name: '工具治理' }).click()
  const drawer = page.locator('.el-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('数据查询')).toBeVisible()
  await expect(drawer.getByText('通用执行与产物')).toBeVisible()
  await expect(drawer.getByText('run_python')).toBeVisible()
  await screenshot(page, 'mcp-governance-drawer')

  // 快照矩阵 → 切 run_python×developer → 保存 → 断言落库翻转 → 恢复
  const before = await api(request, 'GET', '/ai/mcp-tools')
  const orig = flatRules(before.json as Gov)
  const cur = orig.find(r => r.tool_name === 'run_python' && r.role === 'developer')!.allowed
  const row = drawer.locator('.tool-row', { hasText: 'run_python' })
  const switches = row.locator('.el-switch')
  // EP switch 坐标点击在真机抽屉里偶发不触发 change——用 DOM click 直击组件根
  await switches.nth(1).evaluate((el: any) => el.click())
  await expect(drawer.getByText('有未保存改动')).toBeVisible()
  await drawer.getByRole('button', { name: /保存/ }).click()
  // 保存成功 → baseline 刷新 → 脏标记消失（比 3s 自动关闭的 el-message 更本质）
  await expect(drawer.getByText('有未保存改动')).toBeHidden({ timeout: 10_000 })
  const after = await api(request, 'GET', '/ai/mcp-tools')
  const rp = (after.json as Gov).groups.flatMap(g => g.tools)
    .find(t => t.name === 'run_python')
  expect(rp.rules.developer).toBe(!cur)
  await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
})
