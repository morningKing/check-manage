/**
 * AI 轨迹分析功能正确性 E2E（spec §4，TC-TRACE-01~05）。
 * fail-closed 三分支确定性触发（设置开关 / restartBackend 死端口）；
 * 分析闭环只做 @llm 冒烟（TC-TRACE-05）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { api, gotoWithAuth, openChatSession, restartBackend } from './helpers'
import { API, authHeaders, adminToken, dbSeed } from './batch/toolbox'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'
import { resolveWorkspaceRoot } from './fit-seed'

test.setTimeout(180_000)

function analysisRowCount(sid: string): number {
  return Number(dbSeed(`SELECT count(*) FROM ai_chat_sessions WHERE title = '轨迹分析: ${sid}'`)[0][0])
}

test('TC-TRACE-04 分析历史与报告契约（种子：snake_case 历史 / camelCase 轮询 / report 形状）', async ({ request }) => {
  const key = newId('ana-')
  const sid = await seedPlainSession({ key })
  const asid = newId('sess_')
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status, kind) VALUES ('${asid}', 'user-admin', '轨迹分析: ${sid}', 'completed', 'trace_analysis')`)
  const aDone = newId('ana_')
  const aPending = newId('ana_')
  dbSeed(`INSERT INTO ai_execution_diagnoses (id, target_session_id, analysis_session_id, status, report, created_at)
          VALUES ('${aDone}', '${sid}', '${asid}', 'completed', '{"summary":"e2e-seed","findings":[]}', NOW())`)
  dbSeed(`INSERT INTO ai_execution_diagnoses (id, target_session_id, analysis_session_id, status, created_at)
          VALUES ('${aPending}', '${sid}', '${asid}', 'pending', NOW() - INTERVAL '5 minutes')`)
  try {
    // v2 列表契约：trace_analysis 会话默认隐藏 / kind 过滤三态
    // （keyword 用 sid：分析会话标题是 `轨迹分析: <sid>`，key 只存在于目标会话标题——
    //   keyword 只命中会话自身标题/消息，用 key 永远筛不出分析会话行）
    const q = async (p: string) =>
      (await api(request, 'get', `/ai/chat/admin/sessions/v2?pageSize=100&keyword=${sid}${p}`)).json
    expect((await q('')).items.map((x: any) => x.id)).not.toContain(asid)
    expect((await q('&kind=all')).items.map((x: any) => x.id)).toContain(asid)
    expect((await q('&kind=trace_analysis')).items.map((x: any) => x.id)).toEqual([asid])
    // 会话维度历史（snake_case + created_at DESC）
    const hist = (await api(request, 'get', `/ai/chat/admin/sessions/v2/${sid}/analyses`)).json.analyses
    expect(hist.map((x: any) => x.id)).toEqual([aDone, aPending])
    expect(hist[0]).toMatchObject({ status: 'completed', analysis_session_id: asid })
    // 状态轮询（camelCase 契约）+ 404
    const st = (await api(request, 'get', `/ai/chat/admin/analyses/${aDone}`)).json
    expect(st).toMatchObject({ analysisId: aDone, targetSessionId: sid, analysisSessionId: asid, status: 'completed' })
    expect((await api(request, 'get', `/ai/chat/admin/analyses/${newId('ana_')}`)).status).toBe(404)
    // 报告契约：已完成行回种子 JSONB；pending 行无 summary
    const rep = (await api(request, 'get', `/ai/chat/admin/analyses/${aDone}/report`)).json
    expect(rep.report).toMatchObject({ summary: 'e2e-seed' })
    const repEmpty = (await api(request, 'get', `/ai/chat/admin/analyses/${aPending}/report`)).json
    expect(repEmpty.status).toBe('pending')
    expect(repEmpty.report.summary).toBeUndefined()
  } finally {
    dbSeed(`DELETE FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${asid}'`)   // 种子分析会话标题不带 AITEST 前缀，按 id 清
    cleanupSessionsByPrefix()
  }
})

test('TC-TRACE-01 fail-closed：内置 MCP 禁用 → 409 且会话/诊断零残留', async ({ request }) => {
  const key = newId('tr1-')
  const sid = await seedPlainSession({ key })
  try {
    expect((await api(request, 'put', '/ai/mcp-servers/internal', { enabled: false })).status).toBe(200)
    try {
      const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
      expect(r.status).toBe(409)
      expect(r.json.error).toContain('内置 MCP 已被禁用')
      expect(analysisRowCount(sid)).toBe(0)
      expect(Number(dbSeed(`SELECT count(*) FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)[0][0])).toBe(0)
    } finally {
      expect((await api(request, 'put', '/ai/mcp-servers/internal', { enabled: true })).status).toBe(200)
    }
  } finally { cleanupSessionsByPrefix() }
})

test('TC-TRACE-02 fail-closed：MCP 不可达 → 502（进程级 env 覆盖）', async ({ request }) => {
  test.setTimeout(240_000)
  const key = newId('tr2-')
  const sid = await seedPlainSession({ key })
  try {
    await restartBackend({ MCP_SERVER_URL: 'http://127.0.0.1:1' })
    const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
    expect(r.status).toBe(502)
    expect(r.json.error).toContain('MCP 服务不可用')
    expect(analysisRowCount(sid)).toBe(0)
  } finally {
    await restartBackend()          // 无参恢复默认 env
    cleanupSessionsByPrefix()
  }
})

function listSessWorkspacePaths(root: string): Set<string> {
  // 收集 ai-workspaces 树内 sess_<12hex> 形态的路径（分析会话工作区命名）
  const out = new Set<string>()
  const walk = (dir: string) => {
    let entries: fs.Dirent[]
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = path.join(dir, e.name)
      if (/sess_[0-9a-f]{12}/.test(e.name)) out.add(p)
      else if (e.isDirectory()) walk(p)
    }
  }
  walk(root)
  return out
}

test('TC-TRACE-03 派发失败清理：diagnosis failed + 无孤儿会话行 + 工作区零残留', async ({ request }) => {
  test.setTimeout(240_000)
  const key = newId('tr3-')
  const sid = await seedPlainSession({ key })
  const wsRoot = resolveWorkspaceRoot()          // 来自 ../fit-seed（跨 spec 复用该 helper）
  const before = listSessWorkspacePaths(wsRoot)
  try {
    await restartBackend({ OPENCODE_BASE_URL: 'http://127.0.0.1:1' })
    const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
    expect(r.status).toBe(502)
    expect(r.json.error).toContain('OpenCode 会话创建失败')
    const d = dbSeed(`SELECT status, error_message FROM ai_execution_diagnoses WHERE target_session_id='${sid}' ORDER BY created_at DESC LIMIT 1`)
    expect(d).toHaveLength(1)
    expect(d[0][0]).toBe('failed')
    expect(String(d[0][1])).toContain('OpenCode 会话创建失败')
    expect(analysisRowCount(sid)).toBe(0)
    const grown = listSessWorkspacePaths(wsRoot).difference(before)   // Node 22+ Set.prototype.difference
    expect([...grown]).toEqual([])
  } finally {
    await restartBackend()
    cleanupSessionsByPrefix()
  }
})

test('TC-TRACE-05 @llm 轨迹分析闭环：真实会话→触发→收敛→报告结构→抽屉历史', async ({ page, request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  // 预算 660s（探针+assistant 180s+轮询 480s）< 900s，留 headroom
  test.setTimeout(900_000)
  const tk = await adminToken()
  // LLM 预检（ledger Ruling Task 16）：dev LLM 不可达时步骤 1 会挂满 180s——
  // 先打一发真实 LLM 调用探活，502 即整例 skip，避免环境性红。
  const gsRoot = path.join(resolveWorkspaceRoot(), 'global-skills')
  let defPath = path.join(gsRoot, 'trace-analyzer', 'SKILL.md')
  if (!fs.existsSync(defPath)) {
    const pick = fs.readdirSync(gsRoot, { withFileTypes: true })
      .find(e => e.isDirectory() && fs.existsSync(path.join(gsRoot, e.name, 'SKILL.md')))
    if (!pick) throw new Error(`LLM 预检定义文件缺失：${gsRoot} 下没有 */SKILL.md`)
    defPath = path.join(gsRoot, pick.name, 'SKILL.md')
  }
  const probe = await fetch(`${API}/ai/chat/admin/skill-def-steps/generate`, {
    method: 'POST',
    headers: { ...authHeaders(tk), 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'skill', path: defPath }),
  })
  if (probe.status === 502) return test.skip(true, 'LLM 不可达（预检 502），冒烟跳过')
  expect(probe.status).toBe(200)   // 探活即弃：生成的 steps 不参与后续断言
  // 1) 真实会话真跑一轮（API 建会话改名 + UI 发消息，规避自动创建竞态）
  const create = await fetch(`${API}/ai/chat/sessions`, { method: 'POST', headers: { ...authHeaders(tk), 'Content-Type': 'application/json' }, body: '{}' })
  expect(create.status).toBeLessThan(300)
  const sid = (await create.json()).id
  const title = newId('AITEST-trace-live-')
  await fetch(`${API}/ai/chat/sessions/${sid}`, { method: 'PATCH', headers: authHeaders(tk), body: JSON.stringify({ title }) })
  await openChatSession(page, sid)
  // 深链挂载竞态兜底（ai-harness-safety 手法）：composer 15s 不可见则 reload 一次再等
  const composer = page.getByPlaceholder(/给 AI 助手发消息/)
  try {
    await composer.waitFor({ state: 'visible', timeout: 15_000 })
  } catch {
    await page.reload()
    await composer.waitFor({ state: 'visible', timeout: 15_000 })
  }
  await composer.fill('hello trace e2e')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--assistant').first()).toBeVisible({ timeout: 180_000 })
  // 2) API 触发轨迹分析
  const r = await fetch(`${API}/ai/chat/admin/sessions/v2/${sid}/analyze`, { method: 'POST', headers: authHeaders(tk) })
  if (r.status === 502) return test.skip(true, 'LLM/MCP 链路不可用（analyze 502），冒烟跳过')
  expect(r.status).toBe(200)
  const { analysisId, analysisSessionId } = await r.json()
  try {
    // 3) 轮询至终态并要求 completed（96×5s = 480s 预算）
    let final: any = null
    for (let i = 0; i < 96; i++) {
      const s = await (await fetch(`${API}/ai/chat/admin/analyses/${analysisId}`, { headers: authHeaders(tk) })).json()
      if (['completed', 'partial', 'failed'].includes(s.status)) { final = s; break }
      await page.waitForTimeout(5000)
    }
    expect(final?.status).toBe('completed')
    // 4) 报告结构完整（断言结构，不断言内容语义）
    const rep = await (await fetch(`${API}/ai/chat/admin/analyses/${analysisId}/report`, { headers: authHeaders(tk) })).json()
    expect(Object.keys(rep.report ?? {}).length).toBeGreaterThan(0)
    expect(analysisRowCount(sid)).toBe(1)
    expect(dbSeed(`SELECT count(*) FROM ai_chat_sessions WHERE id='${analysisSessionId}' AND kind='trace_analysis'`)[0][0]).toBe(1)
    // 5) 审计抽屉「轨迹分析历史」出现该行
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: title }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '执行审计' }).first().click()
    const drawer = page.locator('.el-drawer', { hasText: '执行合规审计' })
    await expect(drawer.getByText('轨迹分析历史')).toBeVisible({ timeout: 15_000 })
    await expect(drawer.locator('.analysis-row').first()).toBeVisible()
  } finally {
    // 定点清理：目标会话 + 分析会话 + 诊断行（按 id，防误删）
    dbSeed(`DELETE FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${analysisSessionId}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${sid}'`)
  }
})
