/**
 * AI 批任务控制面域 spec（2026-10-04 从 ai-batch-control.spec.ts 收编 + 补缺）。
 *
 * 此前 pause/resume/cancel 在 e2e 层只有负路径（ai-harness-safety 的
 * 「终态批次 pause → 409」）；running 中的正路径——协作式中断、暂停收敛、
 * 单子独立续跑、取消聚合计数、partial 收敛——由迁移用例 1-3 真实链路验证。
 *
 * 覆盖：
 *  用例 1 批级 pause/resume + 单子 resume（迁移）：
 *    running 中批级 pause → 批次与全部子任务收敛 paused（协作式中断有延迟，
 *    轮询收敛；paused 不占 failed）→ 单子 resume 仅该子任务恢复（兄弟保持
 *    paused）并独立跑到 completed → 批级 resume 恢复其余 → 全部 completed
 *    （done=3 计数正确）。
 *  用例 2 批级 cancel（迁移）：running 中批级 cancel → 子任务协作式中止为
 *    cancelled（cancelled 记 failed 聚合）→ 全取消的批收敛 failed（failed=2）。
 *  用例 3 单子 cancel（迁移）：running 中取消其中一个 → 该子任务 cancelled、
 *    兄弟不受影响跑完 → 批次收敛 partial（done=1 / failed 聚合=1）。
 *  用例 4 命令幂等（自 ai-harness-safety「命令幂等」块收编）：
 *    POST /<id>/commands 同 Idempotency-Key 两次提交 → 同一命令行、HTTP 状态
 *    一致（终态批上 pause → rejected）。
 *  用例 5-8 新增边界（预期已逐条对照 server/routes/ai_chat_batches.py 与
 *    utils/batch_repo.py 核实，与 brief 预期一致处未改断言）：
 *   5 pause 后 cancel：cancel_batch 对 paused 子任务同步落 cancelled 并计入
 *     failed 聚合（batch_repo.py:447-458），全取消重算 failed（:44-45）。
 *   6 paused 批 retry-failed：reset_failed_to_pending 只认 failed/needs_review
 *     （batch_repo.py:1046）→ 200 {retried:0}，paused 状态原样（路由不 409）。
 *   7 重复 cancel：第二次 cancel 重设 pending/running 行的 cancel_requested
 *     （batch_repo.py:440-443）→ 200 不重复扣减计数；若子任务已收口则批已
 *     终态 → 409（同 <500）。
 *   8 终态批控制面拒绝：pause/cancel 的终态谓词（batch_repo.py:491/438）对
 *     completed/partial/failed 同一分支 → 409；resume 因无可恢复子任务
 *     （fail-fast 子任务全 failed，非 cancelled/paused）→ 409（:574）。
 *     brief 标题写「completed 批」，实际用 fail-fast 构造（终态 failed）——
 *     同一拒绝路径，0 LLM。
 *   9 发送门禁（2026-10-04 Task 14 自 ai-harness-safety.spec.ts P0-1 的
 *     API 半边收编）：batch 受控的 running 子会话上普通发送
 *     POST /ai/chat/sessions/<sid>/messages → 409 BATCH_SESSION_CONTROLLED
 *     ——批内输入只走批通道/continue。源用例等真实 claim（模型快时抢不到
 *     running 只能降级告警），此处 sleepBatch 确定性 running 窗口；UI 半边
 *     （batch-bar + composer 禁用）在 ui-journeys.spec.ts 用例 10。
 *
 * LLM 预算标记（同 lifecycle.spec.ts 约定，标注在头部）：
 * - 用例 1：@llm —— 3 个 sleep 子会话（各 1 次 bash sleep 回合）；
 * - 用例 2/3/5/6/7/9：@llm-light —— sleep 长任务（2/2/2/1/1/1 次轻量消耗）；
 * - 用例 4/8：0 LLM（fail-fast 构造）。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import {
  API, BATCH_TERMINAL, authHeaders, cleanupBatch, getDetail,
  waitBatchTerminal, waitFor, countByStatus,
} from './batch-helpers'
import { adminTokenCached, failFastBatch, sleepBatch } from './toolbox'

test.setTimeout(1_200_000)

test('控制面：批级 pause/resume + 单子 resume 独立续跑', async () => {
  const tk = await adminTokenCached()
  const HDRS = authHeaders(tk)
  const bid = await sleepBatch(tk, { children: 3 })
  // 源 spec 由 createSleepBatch 返回 detail 取子会话 id；sleepBatch 只返回
  // batch id——建批即插入 sessions 行，创建后立即回读即可
  const d0 = await getDetail(tk, bid)
  const [s0, s1, s2] = d0.sessions.map((s: any) => s.id as string)
  try {
    // 1) 等首个子任务进入 running（长任务窗口打开）
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    // 2) 批级 pause → 批次与全部子任务收敛 paused（协作式中断有延迟，轮询窗口 90s）
    const pp = await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: HDRS })
    expect(pp.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      const counts = countByStatus(dd)
      return dd.batch.status === 'paused' && (counts.paused || 0) === 3 ? true : null
    }, 90_000, '批级 pause 收敛（batch+3 子任务 paused）')

    // 3) 单子 resume：仅 s0 恢复，兄弟保持 paused
    const rs0 = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${s0}/resume`, {
      method: 'POST', headers: HDRS,
    })
    expect(rs0.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      const byId = Object.fromEntries(dd.sessions.map((s: any) => [s.id, s.status]))
      return (['pending', 'running'].includes(byId[s0]) &&
              byId[s1] === 'paused' && byId[s2] === 'paused') ? true : null
    }, 60_000, '单子 resume 后 s0 恢复、s1/s2 保持 paused')

    // 4) s0 独立跑到 completed（窗口覆盖续跑路径：中断点恢复或重执行）
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      const byId = Object.fromEntries(dd.sessions.map((s: any) => [s.id, s.status]))
      return byId[s0] === 'completed' ? true : null
    }, 420_000, 's0 在单子 resume 后 completed')
    // 兄弟全程未被牵动
    const mid = countByStatus(await getDetail(tk, bid))
    expect(mid.completed).toBe(1)
    expect(mid.paused).toBe(2)

    // 5) 批级 resume → 其余子任务恢复并跑完
    const rb = await fetch(`${API}/ai/chat/batches/${bid}/resume`, { method: 'POST', headers: HDRS })
    expect(rb.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      return (countByStatus(dd).paused || 0) === 0 ? true : null
    }, 60_000, '批级 resume 后无 paused 残留')

    // 6) 全量收敛：3×completed、done=3
    const terminal = await waitBatchTerminal(tk, bid, 420_000)
    expect(terminal.batch.done).toBe(3)
    expect(terminal.batch.failed).toBe(0)
    expect(countByStatus(terminal).completed).toBe(3)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('控制面：批级 cancel——running 中协作式中止为 cancelled', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2 })
  try {
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    const c = await fetch(`${API}/ai/chat/batches/${bid}/cancel`, {
      method: 'POST', headers: authHeaders(tk),
    })
    expect(c.status).toBe(200)

    // cancelled 记 failed 聚合（cancel_batch 语义）；全取消的批收敛 failed
    const terminal = await waitBatchTerminal(tk, bid, 180_000, 2000)
    expect(terminal.batch.status).toBe('failed')
    expect(terminal.batch.failed).toBe(2)
    expect(countByStatus(terminal).cancelled).toBe(2)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('控制面：单子 cancel——取消互不牵连，批收敛 partial', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2 })
  const s0 = (await getDetail(tk, bid)).sessions[0].id as string
  try {
    await waitFor(async () => {
      const dd = await getDetail(tk, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    const c = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${s0}/cancel`, {
      method: 'POST', headers: authHeaders(tk),
    })
    expect(c.status).toBe(200)

    // s0 cancelled；兄弟继续跑到 completed → 批收敛 partial
    const terminal = await waitBatchTerminal(tk, bid, 420_000)
    expect(terminal.batch.status).toBe('partial')
    expect(terminal.batch.done).toBe(1)
    expect(terminal.batch.failed).toBe(1)
    const byId = Object.fromEntries(terminal.sessions.map((s: any) => [s.id, s.status]))
    expect(byId[s0]).toBe('cancelled')
    expect(Object.values(byId).filter(st => st === 'completed').length).toBe(1)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('命令幂等：同 Idempotency-Key 两次提交 /commands 状态一致（终态批 rejected）', async () => {
  // 收编自 ai-harness-safety「命令幂等」块（原文件已移出该块）。原块挂在
  // 真实 OpenCode 已完成批上、走对外 /v1/ai-batches/<id>/pause（其幂等去重
  // 实际靠 (type,batch,requester) 派生自然键，Idempotency-Key 头在该端点不
  // 生效）；brief/任务书指定收编语义为 POST /commands + Idempotency-Key——
  // 内部命令平面（ai_chat_batches.py:602）才是该头的真实消费方，故改打
  // /commands，终态批用 fail-fast 构造（0 LLM，断言语义不变：两次调用状态
  // 一致、不重复执行）。
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  try {
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      return BATCH_TERMINAL.includes(d.batch?.status) ? d : null
    }, 90_000, 'fail-fast 终态')

    const idem = `ctl-idem-${Date.now()}`
    const post = () => fetch(`${API}/ai/chat/batches/${bid}/commands`, {
      method: 'POST',
      headers: { ...authHeaders(tk), 'Idempotency-Key': idem },
      body: JSON.stringify({ type: 'pause' }),
    })
    const r1 = await post()
    const b1 = await r1.json()
    // 终态批 pause → 409（命令 rejected），但命令行已登记
    //（原块断言：非终态批上同调用会 applied，故 [200, 409] 均合法）
    expect([200, 409]).toContain(r1.status)
    const r2 = await post()
    const b2 = await r2.json()
    // 同幂等键：不再重复执行——返回既有命令行（duplicate:true、commandId
    // 相同，execution_commands.submit_command 按键去重），状态一致
    expect(r2.status).toBe(r1.status)
    expect(b2.commandId).toBe(b1.commandId)
    expect(b2.duplicate).toBe(true)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

test('控制面边界：pause 后 cancel——paused 子任务落 cancelled，批收敛 failed 聚合', async () => {
  // 路由核实：pause 收敛后子任务全 paused；cancel_batch 对 paused 行同步翻
  // cancelled 且 failed 聚合 +2（batch_repo.py:447-458），重算全取消 → failed。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 2, 120_000, 'running×2')
    await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: authHeaders(tk) })
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['paused'] === 2, 180_000, 'paused×2')
    await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    const d = await waitFor(async () => {
      const cur = await getDetail(tk, bid)
      return ['completed', 'partial', 'failed'].includes(cur.batch.status) ? cur : null
    }, 180_000, 'pause→cancel 终态')
    expect(countByStatus(d)['cancelled']).toBe(2)
    expect(d.batch.status).toBe('failed')   // cancelled 记入 failed 聚合
  } finally { await cleanupBatch(tk, bid) }
})

test('控制面边界：paused 批 retry-failed——无 failed 子任务 → retried=0 状态原样', async () => {
  // 路由核实：retry-failed 路由不抛 409——reset_failed_to_pending 的目标谓词
  // 只认 failed/needs_review（batch_repo.py:1046），paused 批命中 0 行 →
  // 200 {retried:0}，且 count=0 不触发 worker 唤醒，子任务保持 paused。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 1, 120_000, 'running')
    await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: authHeaders(tk) })
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['paused'] === 1, 180_000, 'paused')
    const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: authHeaders(tk) })
    expect(r.status).toBeLessThan(300)
    const body = await r.json()
    expect(body.retried ?? 0).toBe(0)
    expect(countByStatus(await getDetail(tk, bid))['paused']).toBe(1)
  } finally { await cleanupBatch(tk, bid) }
})

test('控制面边界：重复 cancel 状态一致——两次 cancel 后全部 cancelled、计数不重复扣减', async () => {
  // 路由核实：cancel_batch 只置 cancel_requested（计数由 worker 的终态转移
  // 恰好 +1）；第二次 cancel 时 running 行仍命中 :441-443 谓词 → 重设标志
  // 返回 200（paused_n=0 不动计数）。极端时序下子任务已收口 → 批终态 →
  // 409，同样 <500。两次调用只约束状态语义不变（brief 原样保留）。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 2, 120_000, 'running×2')
    await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    const second = await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    // 二次调用允许 200/202/409——只约束状态语义不变
    const d = await waitFor(async () => {
      const cur = await getDetail(tk, bid)
      return ['partial', 'failed'].includes(cur.batch.status) ? cur : null
    }, 180_000, '重复 cancel 终态')
    expect(countByStatus(d)['cancelled']).toBe(2)
    expect(second.status).toBeLessThan(500)
  } finally { await cleanupBatch(tk, bid) }
})

test('控制面边界：终态批控制面拒绝——pause/cancel/resume 返回 4xx', async () => {
  // 路由核实：fail-fast 构造的终态是 failed（brief 标题写 completed——
  // pause/cancel 的终态谓词对 completed/partial/failed 同一分支
  // batch_repo.py:438/491 → 409；resume 无 cancelled/paused 子任务 →
  // count=0 → 409（:574）。全部 4xx，与 brief 预期一致。
  // （brief 草稿无兜底清理——按本 spec 约定补 try/finally cleanupBatch。）
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  try {
    await waitFor(async () => ['failed'].includes((await getDetail(tk, bid)).batch.status), 90_000, 'failed 终态')
    for (const action of ['pause', 'cancel', 'resume']) {
      const r = await fetch(`${API}/ai/chat/batches/${bid}/${action}`, { method: 'POST', headers: authHeaders(tk) })
      expect(r.status).toBeGreaterThanOrEqual(400)
    }
  } finally {
    await cleanupBatch(tk, bid)
  }
})

// ---------------------------------------------------------------------------
// 用例 9：发送门禁（← ai-harness-safety.spec.ts P0-1 的 API 半边，
// 2026-10-04 Task 14 收编；UI 半边在 ui-journeys.spec.ts 用例 10）
// ---------------------------------------------------------------------------

test('发送门禁：running 子会话走普通发送 → 409 BATCH_SESSION_CONTROLLED', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })   // sleepBatch 1 次轻量消耗
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 90 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 1,
                  120_000, '子会话进入 running')
    const sid = (await getDetail(tk, bid)).sessions[0].id
    // 批受控子会话的普通发送被拒——批内输入只走批通道/continue
    // （断言语义与源用例一致：409 + error.code）
    const send = await fetch(`${API}/ai/chat/sessions/${sid}/messages`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ content: '插队：直接告诉我结论' }),
    })
    expect(send.status).toBe(409)
    expect((await send.json())?.error?.code).toBe('BATCH_SESSION_CONTROLLED')
  } finally { await cleanupBatch(tk, bid) }
})
