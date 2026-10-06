/**
 * AI 会话管理 v2 功能正确性 E2E（spec §2，TC-SESS-01~05）。
 * 零 LLM：DB 种子 + API 契约 + UI 真实链路。判别力登记见
 * docs/ai-testing/evidence/2026-10-06-ai-linked-correctness-判别力登记.md
 */
import { test, expect } from '@playwright/test'
import { api, gotoWithAuth } from './helpers'
import { API, authHeaders, adminToken, secondUser, dbSeed } from './batch/toolbox'
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
