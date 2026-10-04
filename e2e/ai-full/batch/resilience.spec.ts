/**
 * 容灾自愈域（resilience）：对账矩阵（_reconcile_stale_running）+ 工具看门狗 + 进程级租约接管。
 *
 * 源码核实的契约（写断言前逐条对过）：
 * - 对账决策顺序 batch_engine.py:3295-3366：租约未过期→跳过；unknown 副作用→
 *   needs_review（error 含 'unknown'）；无 oc→requeue_fresh（retry_count>=
 *   MAX_AUTO_RETRY=2 时 _requeue_lost 内部直接 failed『…自动重试预算已用尽』，
 *   外层仍会补发 requeue_fresh 事件——本 spec 只对 failed 状态+预算未动做双证）；
 *   oc 404→failed（error 含『对账器』）；oc 存活+无 checkpoint+无消息+无副作用→
 *   failed_no_progress；其余→requeue_continue。注意 continue 轮不做 _check_agent
 *   （batch_engine.py:1421 仅 fresh 分支）——requeue_continue 后的认领会真实续跑
 *   oc 会话，故该分支只锚定「决策事件+DB retry_count」双证，不等续跑终局。
 * - 事件字段（routes/ai_chat_batches.py:773 → utils/batch_events.read_events）：
 *   `GET /<bid>/events` 返回 `{events:[{event_seq, event_type, payload, …}]}`——
 *   类型列叫 **event_type**、decision 在 **payload.decision**（jsonb，psycopg2 回 dict）。
 * - attempt 链（`GET /<bid>/attempts` → `{attempts:[{id, attempt_no,
 *   parent_attempt_id, status, error_code, recovery_reason, …}]}`）：重排收口
 *   `_close_attempt_for_requeue` 落 status='recovering' + error_code='AUTO_RETRY'/
 *   'RECONCILE_REQUEUE'（execution_audit.finish_latest_running:163-171 写的是
 *   error_code 列），下一次 create_attempt 以 parent_attempt_id 串链（同进程
 *   _parent_attempt 表）——「无双执行」的强证据是单链而非字符串匹配。
 * - detail.sessions 无 retry_count 列（batch_repo.get_batch_detail:373-380），预算
 *   未动断言直查 DB。
 * - 对账间隔 AI_BATCH_RECONCILE_SEC 默认 60s；子租约 TTL 90s。
 *
 * 清理铁律（T2 裁定）：seedRunningChild 的行**不能**走 API `DELETE ?stop=1`
 * （cancel_batch 只置 cancel_requested，worker 从未认领过该子任务 → drain 409）。
 * 一律 `dbSeed("DELETE FROM ai_chat_batches WHERE id=…")` FK 级联；API cleanup
 * 只用于本文件里经 API 创建的真实批（看门狗/进程级两例）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import { API, authHeaders, cleanupBatch, getDetail, waitFor, waitBatchTerminal } from './batch-helpers'
import { adminTokenCached, dbSeed, restartBackend, seedRunningChild, sleepBatch } from './toolbox'

test.setTimeout(300_000)

/** 批事件全量（对账决策断言用）。字段名 event_type/payload 见文件头源码核实。 */
async function batchEvents(tk: string, bid: string): Promise<any[]> {
  const r = await fetch(`${API}/ai/chat/batches/${bid}/events`, { headers: authHeaders(tk) })
  expect(r.status).toBeLessThan(300)
  return (await r.json()).events ?? []
}

async function recoveredEvents(tk: string, bid: string): Promise<any[]> {
  return (await batchEvents(tk, bid)).filter((e: any) => e.event_type === 'child.recovered')
}

function hasDecision(events: any[], decision: string): boolean {
  return events.some((e: any) => e.payload?.decision === decision)
}

function child0(tk: string, bid: string): Promise<any> {
  return waitFor(async () => (await getDetail(tk, bid)).sessions[0] ?? null, 15_000, 'detail.sessions[0]')
}

/** 种子行清理（铁律）：dbSeed DELETE 级联 + 尽力回收种子工作区临时目录。 */
function cleanupSeedBatch(bid: string, sid: string): void {
  try {
    const ws = dbSeed(`SELECT workspace_path FROM ai_chat_sessions WHERE id='${sid}'`)[0]?.[0]
    dbSeed(`DELETE FROM ai_chat_batches WHERE id='${bid}'`)
    if (ws) fs.rmSync(String(ws), { recursive: true, force: true })
  } catch { /* 清理尽力而为，不遮蔽主断言 */ }
}

// ---------------------------------------------------------------------------
// 对账矩阵 6 分支（RECONCILE_INTERVAL_SEC=60 → 决策观察窗 ≤90s）
// ---------------------------------------------------------------------------

test('对账：租约未过期 → 不动（70s 后仍 running，零 recovered 事件）', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: 'ses-x', lease: 'future' })
  try {
    // 停满一个对账窗口（60s 间隔 + 余量），期间至少一个对账 tick 扫过该行
    await new Promise(rr => setTimeout(rr, 70_000))
    const detail = await getDetail(tk, bid)
    expect(detail.sessions[0].status).toBe('running')
    expect(detail.batch.status).toBe('running')
    expect(await recoveredEvents(tk, bid)).toHaveLength(0)
  } finally { cleanupSeedBatch(bid, sid) }
})

test('对账：unknown 副作用 → needs_review（禁自动重放）', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: 'ses-x', lease: 'past', effectUnknown: true })
  try {
    await waitFor(async () =>
      (await getDetail(tk, bid)).sessions[0].status === 'needs_review' ? true : null,
      90_000, 'needs_review')
    const s = (await getDetail(tk, bid)).sessions[0]
    expect(s.error_message ?? '').toContain('unknown')
    expect(hasDecision(await recoveredEvents(tk, bid), 'needs_review')).toBe(true)
  } finally { cleanupSeedBatch(bid, sid) }
})

test('对账：无 oc 会话 → requeue_fresh（种子批 agent 缺失 → 认领即 fail-fast，0 LLM）', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: null, lease: 'past' })
  try {
    await waitFor(async () => {
      const ev = await recoveredEvents(tk, bid)
      return hasDecision(ev, 'requeue_fresh') ? ev : null
    }, 90_000, 'requeue_fresh 事件')
    // 重排后的 pending 被认领 → _check_agent fail-fast（默认 agent e2e-no-such-agent）确定性收敛
    const s = await waitFor(async () => {
      const c = (await getDetail(tk, bid)).sessions[0]
      return c.status === 'failed' ? c : null
    }, 90_000, '重排后 fail-fast 收敛')
    expect(s.error_message ?? '').not.toBe('')
  } finally { cleanupSeedBatch(bid, sid) }
})

test('对账：oc 会话 404 → failed（带对账原因）', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: 'ses-e2e-nonexistent-000', lease: 'past' })
  try {
    await waitFor(async () => {
      const c = (await getDetail(tk, bid)).sessions[0]
      return c.status === 'failed' && (c.error_message ?? '').includes('对账器') ? c : null
    }, 90_000, 'oc 404 → failed（对账器文案）')
    expect(hasDecision(await recoveredEvents(tk, bid), 'opencode_404')).toBe(true)
  } finally { cleanupSeedBatch(bid, sid) }
})

test('对账：retry_count 预算用尽（=MAX_AUTO_RETRY 2）→ failed 且不重排', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: null, lease: 'past', retryCount: 2 })
  try {
    const s = await waitFor(async () => {
      const c = (await getDetail(tk, bid)).sessions[0]
      return c.status === 'failed' && (c.error_message ?? '').includes('预算') ? c : null
    }, 90_000, '预算用尽 → failed')
    expect(s.error_message ?? '').toContain('预算')
    // 「不重排」双证：DB 里 retry_count 原样（requeue 路径会 +1）
    expect(dbSeed(`SELECT retry_count FROM ai_chat_sessions WHERE id='${sid}'`)[0][0]).toBe(2)
  } finally { cleanupSeedBatch(bid, sid) }
})

test('对账：有 checkpoint + oc 活着 → requeue_continue（@resilience）', async ({ }, testInfo) => {
  // 先经 OpenCode 真实 POST /session 建会话再回填种子行（不烧推理 token）；
  // 冷目录 bootstrap 可达 120s → 观察窗放宽
  test.setTimeout(330_000)
  testInfo.annotations.push({ type: 'resilience' })
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: null, lease: 'past', checkpoint: true })
  try {
    const ws = (await child0(tk, bid)).workspace_path
    const ocBase = process.env.OPENCODE_BASE_URL ?? 'http://127.0.0.1:4096'
    const r = await fetch(`${ocBase}/session?directory=${encodeURIComponent(ws)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'e2e-reconcile' }),
    })
    expect(r.status).toBeLessThan(300)
    const ocId = (await r.json()).id
    expect(ocId).toBeTruthy()
    dbSeed(`UPDATE ai_chat_sessions SET opencode_session_id='${ocId}' WHERE id='${sid}'`)
    // 决策双证：事件 decision=requeue_continue + DB retry_count 已 +1（重排换代）。
    // 注意：continue 轮不走 _check_agent（batch_engine.py:1421 仅在 fresh 分支），
    // 重排后的认领会拿 continue_prompt 对真 oc 会话续跑——本用例只锚定「决策发生」，
    // 不等它的续跑终局（种子 agent 未知，续跑无产出，由 cleanup 收口）。
    await waitFor(async () => {
      const ev = await recoveredEvents(tk, bid)
      return hasDecision(ev, 'requeue_continue') ? ev : null
    }, 150_000, 'requeue_continue 事件')
    expect(dbSeed(`SELECT retry_count FROM ai_chat_sessions WHERE id='${sid}'`)[0][0]).toBe(1)
  } finally {
    // 先 API stop（若已被认领则中止在跑的 continue 轮、防止单 worker 线程被
    // 挂到 STALL_TIMEOUT），再 dbSeed DELETE 铁律硬删（未认领时 API 会 409）。
    await cleanupBatch(tk, bid).catch(() => {})
    cleanupSeedBatch(bid, sid)
  }
})

// ---------------------------------------------------------------------------
// 看门狗 / 进程级
// ---------------------------------------------------------------------------

test('工具卡死自动重试：AI_BATCH_TOOL_STALL_SEC 缩短 + sleep 长任务（@llm）', async ({ }, testInfo) => {
  test.setTimeout(540_000)
  testInfo.annotations.push({ type: 'llm' }, { type: 'resilience' })
  await restartBackend({ AI_BATCH_TOOL_STALL_SEC: '20' })
  try {
    const tk = await adminTokenCached()
    // sleep 600s ≫ stall 20s → 工具卡死看门狗触发（abort + _SessionTimeout('tool stuck')）
    const bid = await sleepBatch(tk, { children: 1, sleepSec: 600 })
    try {
      await waitFor(async () => {
        const c = (await getDetail(tk, bid)).sessions[0]
        const rc = dbSeed(`SELECT retry_count FROM ai_chat_sessions WHERE id='${c.id}'`)[0]?.[0] ?? 0
        return rc >= 1 || c.status === 'failed' ? c : null
      }, 360_000, '自动重试触发或预算终局')
      // attempt 链收口证据：重排收口落 status='recovering'/error_code='AUTO_RETRY'
      // （execution_audit.finish_latest_running 写 error_code 列，源码核实）
      await waitFor(async () => {
        const att = await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`,
          { headers: authHeaders(tk) })).json()
        return /recovering|auto_retry/i.test(JSON.stringify(att)) ? att : null
      }, 60_000, 'attempts 含 recovering/AUTO_RETRY 收口')
    } finally { await cleanupBatch(tk, bid) }
  } finally { await restartBackend() }   // 恢复默认 env，杜绝污染后续用例
})

test('进程级：worker 运行中重启 → 租约接管 → 批继续收敛（@llm @resilience）', async ({ }, testInfo) => {
  test.setTimeout(600_000)
  testInfo.annotations.push({ type: 'llm' }, { type: 'resilience' })
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 30 })
  try {
    await waitFor(async () => {
      const c = (await getDetail(tk, bid)).sessions[0]
      return c.status === 'running' ? c : null
    }, 180_000, 'running 中重启（先确认真实 running）')
    await restartBackend()   // 无特殊 env：老进程被 kill，新进程接管
    // 接管链路：租约 TTL 90s 过期 → 对账 tick（60s）→ requeue_continue → 认领续跑
    const final = await waitBatchTerminal(tk, bid, 480_000)
    expect(final.batch.status).toBe('completed')
    // 无双执行强证据：attempt 链单线血缘——首 attempt 被对账收口为
    // recovering/RECONCILE_REQUEUE，后续 attempt 逐一 parent 链接（无平行分叉）
    const att = (await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`,
      { headers: authHeaders(tk) })).json()).attempts
    expect(att.length).toBeGreaterThanOrEqual(2)
    expect(att[0].status).toBe('recovering')
    expect(String(att[0].error_code ?? '')).toContain('RECONCILE_REQUEUE')
    for (let i = 1; i < att.length; i++) expect(att[i].parent_attempt_id).toBe(att[i - 1].id)
  } finally { await cleanupBatch(tk, bid) }
})
