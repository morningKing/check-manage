/**
 * 内置 MCP 工具治理 E2E（2026-10-25 spec，真实栈 3002+3003+前端）。
 * 覆盖：矩阵 API roundtrip（PUT→GET 反映/非法 400/恢复原矩阵）、管理抽屉 UI
 * （分组渲染+开关+保存）、会话侧 /sessions/:id/mcp 工具带分组字段、
 * 执法层即时生效（禁用工具 → MCP tools/call 拒绝 + tools/list 隐藏 → 恢复成功，
 * 直连 3003 stateless MCP，不经 LLM）。
 */
import { test, expect } from '@playwright/test'
import crypto from 'node:crypto'
import { api, gotoWithAuth, screenshot, waitFor } from './helpers'
import { dbSeed } from './batch/toolbox'
import { seedPlainSession } from './db-helpers'

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
  // 全局列头唯一 + 分组/工具渲染（矩阵改版：吸顶列头，角色标签不再逐行重复）
  await expect(drawer.locator('.gov-head')).toHaveCount(1)
  await expect(drawer.getByText('数据查询')).toBeVisible()
  await expect(drawer.getByText('通用执行与产物')).toBeVisible()
  await expect(drawer.getByText('run_python')).toBeVisible()
  await screenshot(page, 'mcp-governance-drawer')

  // 快照矩阵 → 切 run_python×developer（data-attr 定位，弃用脆弱下标）→ 保存 → 断言落库翻转 → 恢复
  const before = await api(request, 'GET', '/ai/mcp-tools')
  const orig = flatRules(before.json as Gov)
  const cur = orig.find(r => r.tool_name === 'run_python' && r.role === 'developer')!.allowed
  const devCell = drawer.locator(
    '[data-test="gov-cell"][data-tool="run_python"][data-role="developer"]')
  // EP switch 坐标点击在真机抽屉里偶发不触发 change——用 DOM click 直击组件根
  await devCell.locator('.el-switch').evaluate((el: any) => el.click())
  await expect(drawer.getByText(/有未保存改动/)).toBeVisible()
  await drawer.getByRole('button', { name: '保存', exact: true }).click()
  // 保存成功 → baseline 刷新 → 状态回「与线上一致」（比 3s 自动关闭的 el-message 更本质）
  await expect(drawer.getByText('与线上一致')).toBeVisible({ timeout: 10_000 })
  const after = await api(request, 'GET', '/ai/mcp-tools')
  const rp = (after.json as Gov).groups.flatMap(g => g.tools)
    .find(t => t.name === 'run_python')
  expect(rp.rules.developer).toBe(!cur)
  await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
})

test('执法层即时生效：禁用工具 → MCP call 拒绝 + list 隐藏 → 恢复成功',
     async ({ request }) => {
  // 种 admin 会话 + 有效 MCP session token（种子会话无 workspace，探测工具
  // 须不依赖工作区——list_collections 只读查菜单，合适）
  const sid = await seedPlainSession({ key: 'mtgv-call', status: 'active' })
  const token = `e2e-mtgv-${crypto.randomBytes(12).toString('hex')}`
  dbSeed(`UPDATE ai_chat_sessions SET session_token='${token}', ` +
         `token_expires_at=NOW()+interval '900 seconds' WHERE id='${sid}'`)

  // stateless streamable HTTP：单发 JSON-RPC，响应为 SSE 帧取末帧
  const mcpUrl = `http://127.0.0.1:3003/mcp?token=${token}`
  const rpc = async (method: string, params: Record<string, unknown>) => {
    const res = await request.fetch(mcpUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Accept': 'application/json, text/event-stream' },
      data: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    })
    expect(res.status()).toBe(200)
    const text = await res.text()
    const frames = text.split('\n').filter(l => l.startsWith('data:'))
    expect(frames.length, `SSE 帧存在: ${text.slice(0, 200)}`).toBeGreaterThan(0)
    return JSON.parse(frames[frames.length - 1].slice(5).trim())
  }
  const callListCollections = () =>
    rpc('tools/call', { name: 'list_collections', arguments: {} })

  // 基线：禁用前调用成功（result.content 文本非错误）
  const baseline = await callListCollections()
  const isDenied = (r: any) =>
    !!r?.error || r?.result?.isError === true ||
    JSON.stringify(r).includes('not available for this session')
  expect(isDenied(baseline), `基线应成功: ${JSON.stringify(baseline).slice(0, 300)}`).toBeFalsy()

  // 禁用 list_collections×admin（取反，对当前状态稳健）
  const before = await api(request, 'GET', '/ai/mcp-tools')
  const orig = flatRules(before.json as Gov)
  try {
    const cur = orig.find(r => r.tool_name === 'list_collections' && r.role === 'admin')!.allowed
    const mutated = orig.map(r =>
      r.tool_name === 'list_collections' && r.role === 'admin'
        ? { ...r, allowed: !cur } : r)
    const put = await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: mutated })
    expect(put.status).toBe(200)
    // 写生效先行确认（与 mcp-server 的读传播解耦——轮询期间若后端被并行用例
    // 重启，这里会立刻暴露而不是耗尽 20s 超时）
    await waitFor(async () => {
      const g = await api(request, 'GET', '/ai/mcp-tools')
      const t = (g.json as Gov).groups.flatMap(x => x.tools)
        .find(x => x.name === 'list_collections')
      return t && t.rules.admin === !cur ? true : null
    }, { timeoutMs: 10_000, intervalMs: 500 })

    // TTL 3s 内传播：轮询 call 直到被拒（fail-closed 语义，spec §5.2）
    await waitFor(async () => isDenied(await callListCollections()) || null,
                  { timeoutMs: 20_000, intervalMs: 1000 })

    // list_tools 过滤：禁用后不再返回该工具
    const list = await rpc('tools/list', {})
    const names = (list.result?.tools ?? []).map((t: any) => t.name)
    expect(names).not.toContain('list_collections')

    // 恢复 → TTL 内调用重新成功
    await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
    await waitFor(async () => {
      const r = await callListCollections()
      return !isDenied(r) ? true : null
    }, { timeoutMs: 20_000, intervalMs: 1000 })
  } finally {
    // 保险恢复（断言中途失败也不把禁用留在真库）
    await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${sid}'`)
  }
})
