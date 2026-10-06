/**
 * AI 会话管理 v2 功能正确性 E2E（spec §2，TC-SESS-01~05）。
 * 零 LLM：DB 种子 + API 契约 + UI 真实链路。判别力登记见
 * docs/ai-testing/evidence/2026-10-06-ai-linked-correctness-判别力登记.md
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { api, gotoWithAuth } from './helpers'
import { API, authHeaders, adminToken, secondUser, dbSeed, seedRunningChild } from './batch/toolbox'
import { newId, seedPlainSession, seedBatch, cleanupSessionsByPrefix, cleanupBatchesByPrefix } from './db-helpers'

test.setTimeout(120_000)

// 逐例清理：批先删（FK 级联其子会话），再兜底删剩余种子会话
async function cleanup() {
  cleanupBatchesByPrefix()
  cleanupSessionsByPrefix()
}

test('TC-SESS-01 v2 筛选：sourceType 五类 × status × kind 默认隐藏 × 400 校验', async ({ request }) => {
  const key = newId('f1-')          // 随机 keyword，收敛到种子集
  const bBatch = seedBatch(null)
  const bApi = seedBatch('ak-e2e-fake')
  const sRegular = await seedPlainSession({ key, status: 'completed' })
  const sBatch = await seedPlainSession({ key, batchId: bBatch })
  const sApi = await seedPlainSession({ key, batchId: bApi })
  const sScan = await seedPlainSession({ key, scanTaskId: true })
  const sKefu = await seedPlainSession({ key, kefu: true })
  const sTrace = await seedPlainSession({ key, kind: 'trace_analysis' })
  try {
    const q = async (params: string) =>
      (await api(request, 'get', `/ai/chat/admin/sessions/v2?pageSize=100&${params}`)).json
    const ids = (r: any) => r.items.map((x: any) => x.id)

    // sourceType 逐类精确命中，且返回项无跨类泄漏
    for (const [t, sid] of [['regular', sRegular], ['batch', sBatch], ['api_batch', sApi],
      ['scan', sScan], ['kefu', sKefu]] as const) {
      const r = await q(`sourceType=${t}&keyword=${key}`)
      expect(ids(r)).toEqual([sid])
    }
    // 判别力：api_batch（批行带 api_key_id）不得被 sourceType=batch 命中（计算列优先级）
    const rb = await q(`sourceType=batch&keyword=${key}`)
    expect(ids(rb)).toEqual([sBatch])
    // kind 默认隐藏 trace_analysis；kind=all 可见；kind=trace_analysis 只看分析会话
    expect(ids(await q(`keyword=${key}`))).not.toContain(sTrace)
    expect(ids(await q(`keyword=${key}`))).toHaveLength(5)
    const all = await q(`keyword=${key}&kind=all`)
    expect(ids(all)).toHaveLength(6)
    expect(ids(all)).toContain(sTrace)
    expect(ids(await q(`keyword=${key}&kind=trace_analysis`))).toEqual([sTrace])
    // status 过滤
    const st = await q(`keyword=${key}&status=completed&kind=all`)
    expect(ids(st)).toHaveLength(6)   // 种子会话全部 completed
    // 分页 total 与 items 一致
    expect(all.total).toBe(6)
    // 非法参数 400（中文错误契约）
    const badStatus = await api(request, 'get', '/ai/chat/admin/sessions/v2?status=bogus')
    expect(badStatus.status).toBe(400)
    expect(badStatus.json.error).toContain('无效状态')
    const badSrc = await api(request, 'get', '/ai/chat/admin/sessions/v2?sourceType=bogus')
    expect(badSrc.status).toBe(400)
    expect(badSrc.json.error).toContain('无效来源类型')
  } finally { await cleanup() }
})

test('TC-SESS-02 关键词搜索：命中消息正文、精确排除、无匹配返空', async ({ request }) => {
  const key = newId('kw-')
  const needle = `${key}-needle`    // 只出现在消息正文，不出现在标题
  const sHit = await seedPlainSession({ key, messages: [{ role: 'user', text: needle }] })
  await seedPlainSession({ key })   // 标题同前缀但正文无 needle
  try {
    const r = (await api(request, 'get', `/ai/chat/admin/sessions/v2?keyword=${encodeURIComponent(needle)}&kind=all`)).json
    expect(r.items.map((x: any) => x.id)).toEqual([sHit])
    expect(r.total).toBe(1)
    const none = (await api(request, 'get', `/ai/chat/admin/sessions/v2?keyword=${key}-nope&kind=all`)).json
    expect(none.items).toEqual([])
  } finally { await cleanup() }
})

test('TC-SESS-03 详情抽屉：基本信息/对话历史/文件列表 + 下载内容一致', async ({ page, request }) => {
  const key = newId('detail-')
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'aitest-ws-'))
  fs.mkdirSync(path.join(ws, 'outputs'))
  fs.writeFileSync(path.join(ws, 'outputs', 'report.md'), 'AITEST-REPORT-CONTENT-42')
  const sid = await seedPlainSession({
    key,
    messages: [{ role: 'user', text: 'hello drawer' }, { role: 'assistant', text: 'hi drawer' }],
    workspacePath: ws,
  })
  // db-helpers 统一以正斜杠入库；产品真实会话由 create_session_workspace 写入
  // Windows 原生反斜杠路径，而 files/download 的 commonpath().startswith(ws) 在
  // Windows 上对正斜杠 ws 恒判 400（分隔符不一致）。改写种子行与产品真实格式一致，
  // 使下载断言覆盖真实契约（self-made 路径，插值合规）。
  dbSeed(`UPDATE ai_chat_sessions SET workspace_path='${ws.replace(/\//g, '\\')}' WHERE id='${sid}'`)
  try {
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '详情' }).first().click()
    // 抽屉标题 = 会话标题（drawerTitle computed；'会话详情' 仅为 detail 未加载的兜底文案）
    const drawer = page.locator('.el-drawer', { hasText: `AITEST-SESSV2-${key}` })
    await drawer.waitFor({ state: 'visible' })
    // 基本信息：会话 ID / 标题 精确渲染（标题同时出现在抽屉标题与基本信息表，故按抽屉标题元素全等断言）
    await expect(drawer.locator('.session-admin__sid', { hasText: sid })).toBeVisible()
    await expect(drawer.locator('.el-drawer__title')).toHaveText(`AITEST-SESSV2-${key}`)
    // 对话历史：种子消息渲染
    await drawer.locator('.el-tabs__item', { hasText: '对话历史' }).click()
    await expect(drawer.getByText('hello drawer')).toBeVisible({ timeout: 15_000 })
    // 文件 tab：outputs 分组与文件名（文件名/路径两处渲染，exact 匹配文件名元素）
    await drawer.locator('.el-tabs__item', { hasText: '文件' }).click()
    await expect(drawer.getByText('report.md', { exact: true })).toBeVisible({ timeout: 15_000 })
    // 下载内容与种子一致（API 通道；UI 下载走 window.open 不做 UI 捕获）
    const tk = await adminToken()
    const dl = await fetch(`${API}/ai/chat/admin/sessions/v2/${sid}/files/download?path=${encodeURIComponent('outputs/report.md')}`, { headers: authHeaders(tk) })
    expect(dl.status).toBe(200)
    expect(await dl.text()).toBe('AITEST-REPORT-CONTENT-42')
  } finally {
    await cleanup()
    fs.rmSync(ws, { recursive: true, force: true })
  }
})

test('TC-SESS-04 v2 归档：UI 归档 active 会话 + oplog 留痕；批控会话 409', async ({ page, request }) => {
  const key = newId('arch-')
  const sid = await seedPlainSession({ key, status: 'active' })
  const running = await seedRunningChild({})
  try {
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '归档' }).first().click()
    await page.locator('.el-message-box').getByText('确定', { exact: true }).click()
    await expect(page.locator('.el-message', { hasText: '已归档' })).toBeVisible()
    // 列表刷新后状态徽标
    await page.locator('button', { hasText: '查询' }).click()
    await expect(page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first())
      .toContainText('已归档')
    // oplog 留痕（描述逐字来自 ai_chat.py archive 端点）
    const logs = dbSeed(`SELECT action, description FROM operation_logs WHERE target_type='ai_chat_session' AND target_id='${sid}' ORDER BY created_at DESC LIMIT 1`)
    expect(logs).toHaveLength(1)
    expect(logs[0]).toEqual(['update', '归档会话（admin）'])
    // 批控 running 子会话：归档 409 BATCH_SESSION_CONTROLLED（API 通道；UI 对 running 行不渲染归档项）
    const tk = await adminToken()
    const res = await fetch(`${API}/ai/chat/sessions/${running.sid}/archive`, { method: 'POST', headers: authHeaders(tk) })
    expect(res.status).toBe(409)
    expect((await res.json()).error.code).toBe('BATCH_SESSION_CONTROLLED')
  } finally {
    dbSeed(`DELETE FROM ai_chat_batches WHERE id='${running.bid}'`)
    await cleanup()
  }
})
