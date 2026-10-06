/**
 * AI 轨迹分析功能正确性 E2E（spec §4，TC-TRACE-01~05）。
 * fail-closed 三分支确定性触发（设置开关 / restartBackend 死端口）；
 * 分析闭环只做 @llm 冒烟（TC-TRACE-05）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { api, gotoWithAuth, restartBackend } from './helpers'
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
