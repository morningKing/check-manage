/**
 * 批任务执行控制面端到端（2026-09-30 覆盖矩阵补缺）。
 *
 * 此前 pause/resume/cancel 在 e2e 层只有负路径（ai-harness-safety 的
 * 「终态批次 pause → 409」）；running 中的正路径——协作式中断、暂停收敛、
 * 单子独立续跑、取消聚合计数、partial 收敛——从未被真实链路验证过。
 *
 * 长任务构造：默认 agent + 明确指令「bash sleep 90 后写 outputs/done.txt」，
 * 提供充足的 running 窗口让控制面操作有确定落点。
 *
 * 覆盖：
 *  用例 1 批级 pause/resume + 单子 resume：
 *    running 中批级 pause → 批次与全部子任务收敛 paused（协作式中断有延迟，
 *    轮询收敛）→ 单子 resume 仅该子任务恢复（兄弟保持 paused）并独立跑到
 *    completed → 批级 resume 恢复其余 → 全部 completed（done=3 计数正确）。
 *  用例 2 批级 cancel：running 中批级 cancel → 子任务协作式中止为 cancelled
 *    （cancelled 记 failed 聚合）→ 全取消的批收敛 failed（failed=2）。
 *  用例 3 单子 cancel：running 中取消其中一个 → 该子任务 cancelled、兄弟
 *    不受影响跑完 → 批次收敛 partial（done=1 / failed 聚合=1）。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import {
  adminToken, authHeaders, uploadStaging, createBatch, getDetail,
  waitBatchTerminal, cleanupBatch, waitFor, countByStatus,
} from './batch-helpers'

const API = 'http://127.0.0.1:3002'
const SLEEP_PROMPT = '用 bash 工具执行命令 `sleep 90`（必须完整等待 90 秒），结束后把文本 "done" 写入 outputs/done.txt，最后回复 DONE。'

test.setTimeout(1_200_000)

async function createSleepBatch(token: string, name: string, n: number): Promise<any> {
  const files: any[] = []
  for (let i = 0; i < n; i++) {
    files.push(await uploadStaging(token, `in-${i}.txt`, `child-${i}`, `ctl-${Date.now()}`))
  }
  return await createBatch(token, {
    name: `AITEST-ctl-e2e-${name}-${Date.now()}`,
    prompt: SLEEP_PROMPT,
    files,
  })
}

test('控制面：批级 pause/resume + 单子 resume 独立续跑', async () => {
  const token = await adminToken()
  const HDRS = authHeaders(token)
  const d = await createSleepBatch(token, 'pr', 3)
  const bid = d.batch.id as string
  const [s0, s1, s2] = d.sessions.map((s: any) => s.id as string)
  try {
    // 1) 等首个子任务进入 running（长任务窗口打开）
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    // 2) 批级 pause → 批次与全部子任务收敛 paused（协作式中断有延迟，轮询窗口 90s）
    const pp = await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: HDRS })
    expect(pp.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      const counts = countByStatus(dd)
      return dd.batch.status === 'paused' && (counts.paused || 0) === 3 ? true : null
    }, 90_000, '批级 pause 收敛（batch+3 子任务 paused）')

    // 3) 单子 resume：仅 s0 恢复，兄弟保持 paused
    const rs0 = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${s0}/resume`, {
      method: 'POST', headers: HDRS,
    })
    expect(rs0.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      const byId = Object.fromEntries(dd.sessions.map((s: any) => [s.id, s.status]))
      return (['pending', 'running'].includes(byId[s0]) &&
              byId[s1] === 'paused' && byId[s2] === 'paused') ? true : null
    }, 60_000, '单子 resume 后 s0 恢复、s1/s2 保持 paused')

    // 4) s0 独立跑到 completed（窗口覆盖续跑路径：中断点恢复或重执行）
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      const byId = Object.fromEntries(dd.sessions.map((s: any) => [s.id, s.status]))
      return byId[s0] === 'completed' ? true : null
    }, 420_000, 's0 在单子 resume 后 completed')
    // 兄弟全程未被牵动
    const mid = countByStatus(await getDetail(token, bid))
    expect(mid.completed).toBe(1)
    expect(mid.paused).toBe(2)

    // 5) 批级 resume → 其余子任务恢复并跑完
    const rb = await fetch(`${API}/ai/chat/batches/${bid}/resume`, { method: 'POST', headers: HDRS })
    expect(rb.status).toBe(200)
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      return (countByStatus(dd).paused || 0) === 0 ? true : null
    }, 60_000, '批级 resume 后无 paused 残留')

    // 6) 全量收敛：3×completed、done=3
    const terminal = await waitBatchTerminal(token, bid, 420_000)
    expect(terminal.batch.done).toBe(3)
    expect(terminal.batch.failed).toBe(0)
    expect(countByStatus(terminal).completed).toBe(3)
  } finally {
    await cleanupBatch(token, bid)
  }
})

test('控制面：批级 cancel——running 中协作式中止为 cancelled', async () => {
  const token = await adminToken()
  const d = await createSleepBatch(token, 'cc', 2)
  const bid = d.batch.id as string
  try {
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    const c = await fetch(`${API}/ai/chat/batches/${bid}/cancel`, {
      method: 'POST', headers: authHeaders(token),
    })
    expect(c.status).toBe(200)

    // cancelled 记 failed 聚合（cancel_batch 语义）；全取消的批收敛 failed
    const terminal = await waitBatchTerminal(token, bid, 180_000, 2000)
    expect(terminal.batch.status).toBe('failed')
    expect(terminal.batch.failed).toBe(2)
    expect(countByStatus(terminal).cancelled).toBe(2)
  } finally {
    await cleanupBatch(token, bid)
  }
})

test('控制面：单子 cancel——取消互不牵连，批收敛 partial', async () => {
  const token = await adminToken()
  const d = await createSleepBatch(token, 'sc', 2)
  const bid = d.batch.id as string
  const s0 = d.sessions[0].id as string
  try {
    await waitFor(async () => {
      const dd = await getDetail(token, bid)
      return (countByStatus(dd).running || 0) > 0 ? true : null
    }, 120_000, '子任务进入 running')

    const c = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${s0}/cancel`, {
      method: 'POST', headers: authHeaders(token),
    })
    expect(c.status).toBe(200)

    // s0 cancelled；兄弟继续跑到 completed → 批收敛 partial
    const terminal = await waitBatchTerminal(token, bid, 420_000)
    expect(terminal.batch.status).toBe('partial')
    expect(terminal.batch.done).toBe(1)
    expect(terminal.batch.failed).toBe(1)
    const byId = Object.fromEntries(terminal.sessions.map((s: any) => [s.id, s.status]))
    expect(byId[s0]).toBe('cancelled')
    expect(Object.values(byId).filter(st => st === 'completed').length).toBe(1)
  } finally {
    await cleanupBatch(token, bid)
  }
})
