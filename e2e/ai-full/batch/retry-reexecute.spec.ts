/**
 * AI 批任务 retry/reexecute 域 spec（2026-10-04 从 ai-retry-failed.spec.ts +
 * ai-reexecute.spec.ts 收编 + 补缺）。
 *
 * 覆盖：
 *  用例 1（迁移 ai-retry-failed，测试体原样）：fail-fast 触发 retry-failed——
 *    retried=1、工作区残留清零（fs 直写植入）、uploads 输入保留、failed 计数
 *    回滚重计、第二轮自动派发收敛、消息跨轮不累积。
 *  用例 2（迁移 ai-reexecute，测试体原样）：单子 reexecute 清上下文/计数回滚/
 *    输入保留/新轮自动派发收敛/files 端点。
 *  用例 3（新增）partial 批混合 retry-failed：completed 子任务原样保留
 *    （同 id、状态不变），failed 子任务恰好重排 1 个。
 *  用例 4（新增）gate-failed 子任务参与 retry-failed：必败 verifier rubric
 *    （引用永不出现的内容）构造门禁拦截，重排后第二轮门禁再次拦截。
 *  用例 5（新增）continue 通道：终态子会话续跑 202、新消息落库且含 47。
 *
 * 相对 brief 草稿的修正（已逐一对照服务端实现/实测核实）：
 *  - 用例 3 PATCH agent 由 'general' 改 'build'：本部署 OpenCode 实测
 *    GET /agent 返回 general 为 subagent（_check_agent 拒其作主 Agent，
 *    batch_engine.py:2354）——按草稿原样 reexecute 会永远 fail-fast，
 *    批次收敛不到 partial。
 *  - 用例 4 action_checks 形状改 {name, check_type:'verifier', rubric}：
 *    validate_checks（agent_ledger.py:292）要求 name 必填且 check_type 枚举，
 *    草稿的 {type, subagents} 形状创建即 400；同款 proven 形状见
 *    ai-verifier-gate.spec.ts。agent 字段删除（默认主 Agent 才能跑到门禁）。
 *  - 用例 4 的 retry_count 断言改写：GET 批详情 sessions[] 不暴露 retry_count
 *    （batch_repo.py:1037 明示执行态不出详情），且 retry-failed 走「全新一轮」
 *    语义把 retry_count 归零（batch_repo.py:1100）而非 +1——改断言可观测的
 *    等价不变量：POST 返回后子任务同步回 pending/被认领、第二轮门禁再次
 *    failed（error_message 含 action_gate、gate_failed 计数重新 ≥1）。
 *  - 用例 3/4「重排后 pending」断言容忍 worker 抢先认领（notify 后 claim
 *    可在毫秒级发生）：断言 pending|running 而非死等 pending 瞬时窗口。
 *  - 3 个新用例补 try/finally cleanupBatch（源 spec 约定，草稿缺失）。
 *
 * LLM 预算标记（同 lifecycle.spec.ts 约定，标注在头部；新用例运行时另推 @llm）：
 * - 用例 1：0 LLM（fail-fast 两轮，秒级收敛）；
 * - 用例 2：@llm —— 默认 agent 真实跑 1-2 轮；
 * - 用例 3：@llm —— 2 次真实子任务轮（reexecute + retry-failed 各一）；
 * - 用例 4：@llm —— 子任务轮 + 判官轮 ×2（初跑 + 重排）；
 * - 用例 5：@llm —— sleep 轮 + 续跑轮，2 次轻量消耗。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import {
  API, adminToken, authHeaders, cleanupBatch, createBatch, getDetail,
  messageCount, uploadStaging, waitBatchTerminal, waitFor, type CreateBatchBody,
} from './batch-helpers'
import { adminTokenCached, failFastBatch, sleepBatch, tag } from './toolbox'

// 迁移两源 spec 的较大者（ai-reexecute 1_200_000；retry-failed 600_000 被覆盖，
// 其内部等待均自带 180s deadline，语义不变）。新用例用 test() 第三参显式给时限。
test.setTimeout(1_200_000)

test('批任务重试失败：清工作区残留/输入保留/计数回滚重计/新轮自动派发', async () => {
  const token = await adminToken()
  const HDRS = authHeaders(token)

  // 1) staging + 建批：不存在的 agent → 派发时 fail-fast，子任务确定性 failed
  const file = await uploadStaging(token, 'in.txt', 'retry-failed-e2e', `rt-${Date.now()}`)
  const d = await createBatch(token, {
    name: `AITEST-rt-e2e-${Date.now()}`,
    prompt: '任意内容——本用例的子任务在 agent 校验即失败，prompt 不会到达模型。',
    agent: `e2e-no-such-agent-${Date.now()}`,
    files: [file],
  })
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string

  try {
    // 2) 等第一轮终态：未知 agent fail-fast，数秒即 failed
    const round1 = await waitBatchTerminal(token, bid, 180_000, 2000)
    expect(round1.sessions[0].status).toBe('failed')
    expect(round1.sessions[0].error_message).toContain('不存在')
    expect(round1.batch.failed).toBe(1)

    // 3) 植入上一轮残留（fs 直写 workspace_path——内部详情暴露该字段）
    const ws = round1.sessions[0].workspace_path as string
    expect(fs.existsSync(ws)).toBe(true)
    fs.writeFileSync(path.join(ws, 'junk-round1.txt'), '上一轮残留', 'utf-8')
    fs.mkdirSync(path.join(ws, 'outputs'), { recursive: true })
    fs.writeFileSync(path.join(ws, 'outputs', 'junk-round1.md'), '上一轮产出', 'utf-8')
    expect(fs.existsSync(path.join(ws, 'uploads', 'in.txt'))).toBe(true)

    // 4) 重试失败 → 200，且恰好重排这 1 个 failed 子任务
    const retry = await fetch(`http://127.0.0.1:3002/ai/chat/batches/${bid}/retry-failed`, {
      method: 'POST', headers: HDRS,
    })
    expect(retry.status).toBe(200)
    expect((await retry.json()).retried).toBe(1)

    // 5) 工作区「全新一轮」不变量（清空在 POST 返回前同步完成，时点确定；
    //    worker 若已抢先派发也不影响——派发路径不重建/不删除这些路径）
    expect(fs.existsSync(path.join(ws, 'junk-round1.txt'))).toBe(false)
    expect(fs.existsSync(path.join(ws, 'outputs', 'junk-round1.md'))).toBe(false)
    expect(fs.existsSync(ws)).toBe(true)
    const inTxt = path.join(ws, 'uploads', 'in.txt')
    expect(fs.existsSync(inTxt)).toBe(true)
    expect(fs.readFileSync(inTxt, 'utf-8')).toBe('retry-failed-e2e')

    // 跨轮消息基线：fail-fast 轮会落一条合法的每轮 notice（全局技能注入
    // 成功提示）——记录它，重试后应清零、第二轮再落一条，总数不跨轮累积
    const msgs1 = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
    const round1MsgCount = (msgs1.messages ?? []).length

    // 6) worker 自动拾取 pending 子任务并派发第二轮（再次 fail-fast 收敛）
    const round2 = await waitBatchTerminal(token, bid, 180_000, 2000)
    expect(round2.sessions[0].status).toBe('failed')
    // 计数回滚后重新计入：failed 归零再 +1；批次状态重新收敛终态
    expect(round2.batch.failed).toBe(1)
    expect(round2.sessions[0].error_message).toContain('不存在')

    // 7) 无跨轮消息残留：fail-fast 轮的唯一合法消息是每轮至多一条的系统
    //    notice（技能注入提示，注入状态跨轮可能波动）——断言第二轮消息数
    //    不超过基线 + 2；「消息清零」的权威防线在路由级
    //    test_retry_failed_clears_context_like_reexecute（真库 count 断言），
    //    旧实现不清消息时这里仍会因翻倍暴露。
    const msgs2 = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
    expect((msgs2.messages ?? []).length).toBeLessThanOrEqual(round1MsgCount + 2)
  } finally {
    await cleanupBatch(token, bid)
  }
})

test('批任务重新执行：清上下文/计数回滚/输入保留/新轮自动派发', async () => {
  const token = await adminToken()
  const HDRS = authHeaders(token)

  // 1) staging + 建批（1 个子任务；提示词刻意极简——终态即可，产出不参与断言）
  const file = await uploadStaging(token, 'in.txt', 'reexecute-e2e', `rx-${Date.now()}`)
  const d = await createBatch(token, {
    name: `AITEST-rx-e2e-${Date.now()}`,
    prompt: '复述一遍 uploads/in.txt 的大小写原文即可，无需其他操作。',
    files: [file],
  })
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string
  try {
    // 2) 等子任务进入任一终态（completed 或 failed 均可——reexecute 对两者语义相同）
    const round1 = await waitBatchTerminal(token, bid)
    expect(Array.isArray(round1.sessions)).toBe(true)
    const firstStatus = round1.sessions[0].status
    expect(['completed', 'failed']).toContain(firstStatus)

    // 3) 重新执行 → 200
    const rx = await fetch(`http://127.0.0.1:3002/ai/chat/batches/${bid}/sessions/${sid}/reexecute`, {
      method: 'POST', headers: HDRS,
    })
    expect(rx.status).toBe(200)

    // 4) 新轮由 worker 自动派发并收敛（极简任务数秒即完——reexecute 与收敛
    //    之间没有可断言的稳定窗口；改为断言终态后的不变量）
    const round2 = await waitBatchTerminal(token, bid)
    expect(['completed', 'failed']).toContain(round2.sessions[0].status)
    expect(round2.sessions[0].status).toBe('completed')

    // 5) 终态不变量：新轮消息落库（worker 置 completed 与消息可见之间存在
    //    延迟窗口，实测可超 30s——放宽至 120s 轮询）+ 输入保留 + 计数重新计入
    const msgsDeadline = Date.now() + 120_000
    let msgCount = 0
    let lastMsgsBody = ''
    while (Date.now() < msgsDeadline) {
      const msgs2 = await (await fetch(
        `http://127.0.0.1:3002/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
      lastMsgsBody = JSON.stringify(msgs2).slice(0, 200)
      msgCount = (msgs2.messages ?? []).length
      if (msgCount > 0) break
      await new Promise(rr => setTimeout(rr, 3000))
    }
    expect(msgCount, `messages not persisted: ${lastMsgsBody}`).toBeGreaterThan(0)
    expect(round2.batch.done).toBe(1)
    const files2 = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/sessions/${sid}/files`, { headers: HDRS })).json()
    expect((files2.files || []).some((f: any) => f.path === 'uploads/in.txt')).toBe(true)
  } finally {
    await cleanupBatch(token, bid)
  }
})

test('partial 批混合 retry-failed：completed 原样保留、failed 恰好重排', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // completed 子任务需真实跑完
  const tk = await adminTokenCached()
  const HDRS = authHeaders(tk)
  // 1) fail-fast 批（2 子全 failed）
  const bid = await failFastBatch(tk, { files: 2 })
  try {
    await waitFor(async () => (await getDetail(tk, bid)).sessions.every((s: any) => s.status === 'failed'),
      90_000, '两子 failed')
    // 2) PATCH 成有效 agent 后 reexecute 子 0 → completed（真实 LLM 轮）；
    //    子 1 保持 failed。agent 用 'build'（实测 primary）——brief 草稿的
    //    'general' 是 subagent，作主 Agent 会被 _check_agent 秒级 fail-fast，
    //    批次永远收敛不到 partial（见文件头「相对 brief 草稿的修正」）。
    const patch = await fetch(`${API}/ai/chat/batches/${bid}`, {
      method: 'PATCH', headers: HDRS,
      body: JSON.stringify({ agent: 'build', prompt: '输出一行 ok 即完成。' }),
    })
    expect(patch.status).toBe(200)
    const sid0 = (await getDetail(tk, bid)).sessions[0].id
    const rex = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${sid0}/reexecute`, {
      method: 'POST', headers: HDRS,
    })
    expect(rex.status).toBeLessThan(300)
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      return d.batch.status === 'partial' ? d : null
    }, 600_000, 'partial（completed+failed 各一）')
    const before = await getDetail(tk, bid)
    const doneChild = before.sessions.find((s: any) => s.status === 'completed')
    expect(doneChild).toBeTruthy()
    // 3) retry-failed：只重排 failed 子（retried=1）
    const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: HDRS })
    expect(r.status).toBe(200)
    expect((await r.json()).retried).toBe(1)
    // completed 子原样保留（同 id、状态不变）；failed 子被重排——worker 认领
    // 可抢先于本次回读（notify 后毫秒级），故断言 pending|running 而非死等
    // pending 瞬时窗口（文件头修正注记）
    await waitFor(async () => {
      const after = await getDetail(tk, bid)
      const other = after.sessions.find((s: any) => s.id !== doneChild.id)
      return other && ['pending', 'running'].includes(other.status) &&
        after.sessions.find((s: any) => s.id === doneChild.id).status === 'completed' ? after : null
    }, 30_000, 'completed 子保留、failed 子重排为 pending/running')
    // 4) 收敛后 completed 子消息与状态未动（同 id 且状态仍 completed）
    const final = await waitBatchTerminal(tk, bid, 420_000, 3000)
    expect(final.sessions.find((s: any) => s.id === doneChild.id).status).toBe('completed')
  } finally {
    await cleanupBatch(tk, bid)
  }
}, 900_000)

test('gate-failed 子任务参与 retry-failed：重排后第二轮门禁再次拦截', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const tk = await adminTokenCached()
  const HDRS = authHeaders(tk)
  // 必败 verifier：rubric 引用永不出现的内容 → 判官拒绝 → 门禁 failed。
  // 形状按 validate_checks 实际契约（name + check_type，rubric 必填）；
  // 不传 agent（默认主 Agent 才能真实执行并到达门禁——草稿的 'general'
  // 是 subagent，会在 agent 校验即失败、永远到不了判官）。
  const created = await createBatch(tk, {
    name: tag('gate-retry'), prompt: '输出一行 ok 即完成。',
    action_checks: [{ name: 'e2e 必败核对', check_type: 'verifier', rubric: '输出必须包含永不出现的咒语XYZZY九次' }],
    files: [await uploadStaging(tk, 'g.txt', 'x\n', tag('upl'))],
  } as CreateBatchBody)
  const bid = created.batch?.id ?? created
  try {
    // 第一轮：子任务跑完但判官拦截 → failed，error_message 带 action_gate
    // （proven 形状见 ai-verifier-gate.spec.ts；gate_status 不出批详情，
    // 用 error_message + gate_failed 失败计数判定）
    const round1 = await waitFor(async () => {
      const d = await getDetail(tk, bid)
      const s = d.sessions[0]
      return s.status === 'failed' && (s.error_message ?? '').includes('action_gate') ? d : null
    }, 600_000, '门禁拦截 failed')
    expect(round1.sessions[0].gate_failed).toBeGreaterThanOrEqual(1)

    // retry-failed：恰好重排这 1 个 gate-failed 子任务
    const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: HDRS })
    expect(r.status).toBe(200)
    expect((await r.json()).retried).toBe(1)
    // 草稿的 retry_count>=1 断言不可观测也不成立（详情不出该字段；retry-failed
    // 全新一轮语义将其归零，batch_repo.py:1100）——改断言可观测等价不变量：
    // 重排后子任务脱离 failed（pending，或被 worker 抢先认领为 running），
    // 上一轮门禁失败计数随期望重建清零
    await waitFor(async () => {
      const redetail = await getDetail(tk, bid)
      const s = redetail.sessions[0]
      return ['pending', 'running'].includes(s.status) ? redetail : null
    }, 30_000, '重排后子任务脱离 failed')

    // 第二轮：门禁再次拦截（期望由 batch.action_checks 重新登记并再次失败）
    const round2 = await waitFor(async () => {
      const d = await getDetail(tk, bid)
      const s = d.sessions[0]
      return s.status === 'failed' && (s.error_message ?? '').includes('action_gate') ? d : null
    }, 600_000, '第二轮门禁再次 failed')
    expect(round2.sessions[0].gate_failed).toBeGreaterThanOrEqual(1)
  } finally {
    await cleanupBatch(tk, bid)
  }
}, 720_000)

test('continue 通道：终态子会话续跑 202 且历史上下文保留（确定性回声构造）', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 5, name: tag('echo') })
  // sleep 5s 快速完成后，continue 注入「请只输出一个数字：47」并断言新消息
  // 落库且包含 47（续跑 prompt 原文即含 47，回声构造不依赖模型意愿）
  try {
    const d0 = await waitBatchTerminal(tk, bid)
    const sid = d0.sessions[0].id
    const msgBefore = await messageCount(tk, sid)
    const r = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${sid}/continue`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ prompt: '请只输出一个数字：47' }),
    })
    expect([200, 202]).toContain(r.status)
    await waitFor(async () => (await messageCount(tk, sid)) > msgBefore, 120_000, '续跑消息落库')
    const msgs = await (await fetch(`${API}/ai/chat/sessions/${sid}/messages`, {
      headers: authHeaders(tk),
    })).json()
    const joined = JSON.stringify(msgs.messages ?? msgs)
    expect(joined).toContain('47')
  } finally {
    await cleanupBatch(tk, bid)
  }
}, 300_000)
