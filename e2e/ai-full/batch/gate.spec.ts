/**
 * AI 批任务动作门禁域 spec（2026-10-04 从三个 spec 收编 + 3 端点补缺）。
 *
 * 迁移来源（原文件本任务末尾删除）：
 *  - ai-verifier-gate.spec.ts → 组 1：verifier 判官三向（达标 completed /
 *    不达标 failed + action_gate 明细）+ gate_retry 闭环（/attempts 断言
 *    GATE_RETRY）。
 *  - ai-verifier-subagent.spec.ts → 组 2：verifier 定向 subagent 组
 *    （判官材料=general 名下子代理消息；错误文案 [agent=general] 前缀；
 *    负向拦截证据走 owner 拟合摘要 skill-fit）。
 *  - ai-delegation-gate.spec.ts → 组 3：委派级门禁（提前判定 + 不过即停，
 *    自定义 primary/subagent 预置仓库，agent 定义经 makeProvisionRepo 收敛）。
 *
 * 新增 3 端点用例（请求/响应形状经 server/routes/ai_chat_batches.py 与
 * utils/agent_ledger.py 核实，与 brief 草稿的差异逐条注明在用例内）：
 *  - gate/dry-run：正则预演命中数（{evidence,samples}）；verifier 类型
 *    「显式标注无法预演」（200 {supported:false}，非 4xx）。
 *  - children/<sid>/tool-calls：动作账本取材（bash sleep 入账）。
 *  - action-checks/attach：运行中补挂期望（session_ids + validate_checks
 *    口径 checks），终态核对 gate 通过（sessions[].gate_passed）。
 *
 * LLM 预算标记（同 lifecycle/control spec 约定）：
 * - 组 1 用例 1-3：@llm —— 各 1 个真实 OpenCode 子会话（达标/不达标/重试闭环）；
 * - 组 2 用例 4-5：@llm —— 各 1 个子会话（委派 general 两次）；
 * - 组 3 用例 6-7：@llm —— 各 1 个预置仓库子会话（串行委派）；
 * - 用例 8/9（dry-run/tool-calls）：@llm-light —— sleep 10s 轻量消耗；
 * - 用例 10（attach）：@llm —— sleep 120s 长窗口 + 终态门禁核对。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import {
  API, BATCH_TERMINAL, adminToken, authHeaders, uploadStaging, createBatch,
  getDetail, cleanupBatch, makeProvisionRepo, waitFor, waitBatchTerminal,
  countByStatus,
} from './batch-helpers'
import { adminTokenCached, sleepBatch } from './toolbox'

const _json = (v: unknown) => JSON.stringify(v)

test.setTimeout(1_200_000)

// ---------------------------------------------------------------------------
// 组 1：verifier 三向 + gate_retry 闭环（迁移自 ai-verifier-gate.spec.ts，
// 用例体与断言原样保留；仅 imports 换为本目录 batch-helpers、URL 走 API 常量）
// ---------------------------------------------------------------------------

interface RunOpts {
  prompt: string
  rubric: string
  gateRetry?: boolean
}

interface RunResult {
  status: string
  errMsg: string
  gateRetried: boolean
}

async function runVerifierBatch(token: string, opts: RunOpts): Promise<RunResult> {
  const HDRS = authHeaders(token)
  const file = await uploadStaging(token, 'in.txt', 'VERIFIER-E2E-INPUT-42', `vf-${Date.now()}`)
  const d = await createBatch(token, {
    name: `AITEST-vf-e2e-${Date.now()}`,
    prompt: opts.prompt,
    gate_retry: opts.gateRetry ?? false,
    action_checks: [{ name: '语义核对', check_type: 'verifier', rubric: opts.rubric }],
    files: [file],
  })
  const bid = d.batch.id as string
  try {
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await getDetail(token, bid)
      if (BATCH_TERMINAL.includes(dd.batch.status)) {
        const s = dd.sessions[0]
        // 终态即取 attempt 链：批删除（finally）后就查不到了
        const att = await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`, { headers: HDRS })).json()
        const gateRetried = (att.attempts as any[]).some(a => (a.error_code || '') === 'GATE_RETRY')
        return { status: s.status, errMsg: String(s.error_message || ''), gateRetried }
      }
      await new Promise(rr => setTimeout(rr, 3000))
    }
    throw new Error('子任务未在时限内达到终态')
  } finally {
    await cleanupBatch(token, bid)
  }
}

test('verifier 门禁：达标 → completed 无失败', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const r = await runVerifierBatch(token, {
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含输入文件 uploads/in.txt 的原文内容(VERIFIER-E2E-INPUT-42)。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('verifier 门禁：不达标 → failed 且 error_message 带判官理由', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const r = await runVerifierBatch(token, {
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ(正常任务不可能满足,用于验证判官拦截)。',
  })
  expect(r.status).toBe('failed')
  expect(r.errMsg).toContain('action_gate')
  expect(r.errMsg).toContain('判官未通过')
})

test('verifier 门禁：gate_retry 闭环——判官理由驱动修复续跑后通过', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const r = await runVerifierBatch(token, {
    prompt: '只复述 uploads/in.txt 的原文,不要写任何文件。',
    rubric: '工作区 outputs/report.md 必须存在,且其内容包含输入原文(VERIFIER-E2E-INPUT-42)。请打开工作区核对文件。',
    gateRetry: true,
  })
  expect(r.gateRetried).toBe(true)      // 第一轮判官拦截 → GATE_RETRY 定向修复续跑
  expect(r.status).toBe('completed')    // 第二轮补写文件 → 判官取证通过
})

// ---------------------------------------------------------------------------
// 组 2：verifier 定向 subagent 组（迁移自 ai-verifier-subagent.spec.ts，
// 用例体与断言原样保留；仅 imports 换为本目录模块）
// ---------------------------------------------------------------------------

interface SubRunOpts { rubric: string; name: string }

async function runSubagentBatch(token: string, opts: SubRunOpts): Promise<{ status: string; errMsg: string; sid: string; fits: Array<{ defName: string; status: string }> }> {
  const HDRS = authHeaders(token)
  const file = await uploadStaging(token, 'in.txt', 'SUBAGENT-VERIFIER-E2E', `vs-${Date.now()}`)
  const d = await createBatch(token, {
    name: `AITEST-vs-e2e-${opts.name}-${Date.now()}`,
    prompt: '用 task 工具委派 general 子代理完成两项调研（串行执行）：'
      + '第一次委派让它输出「FIRST-ANSWER-OK」；'
      + '第二次委派的 prompt 必须以「基于 <第一次回答原文>」开头，让它输出「SECOND-ANSWER-OK」。'
      + '两次委派完成后回复 DONE。',
    action_checks: [{ name: '子代理语义核对', check_type: 'verifier',
                      rubric: opts.rubric, subagents: ['general'] }],
    files: [file],
  })
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string
  try {
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await getDetail(token, bid)
      if (BATCH_TERMINAL.includes(dd.batch.status)) {
        const s = dd.sessions[0]
        // 拟合摘要必须在删批前取：fit 结果行 FK 级联于批删除
        const sf = await (await fetch(
          `${API}/ai/chat/sessions/${sid}/skill-fit`,
          { headers: { Authorization: `Bearer ${token}` } })).json()
        return { status: s.status, errMsg: String(s.error_message || ''),
                 sid, fits: sf.data || [] }
      }
      await new Promise(rr => setTimeout(rr, 3000))
    }
    throw new Error('子任务未在时限内达到终态')
  } finally {
    await cleanupBatch(token, bid)
  }
}

test('定向组：subagents=[general] 判官读子代理材料核对 → completed', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const r = await runSubagentBatch(token, {
    name: 'ok',
    rubric: '指定子代理的会话消息中应有两次委派任务（两条 user 消息），'
      + '且第二条 user 消息内容以「基于」开头并引用了第一条 assistant 回答。请核对子代理会话消息。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('定向组：负向 → 委派级即停 cancelled，判官失败记入拟合摘要', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  // 2026-10-01 委派级门禁上线后的新语义：名单内子代理终态即判定 failed
  // 且未开 gate_retry → 立即 cancel（不再跑完全程），终态为 cancelled。
  // 判官拦截证据改由 owner 拟合摘要（GET /sessions/<sid>/skill-fit）断言。
  const token = await adminToken()
  const r = await runSubagentBatch(token, {
    name: 'bad',
    rubric: '指定子代理的会话消息中必须包含字符串 SUBAGENT-NO-MARK-XYZ'
      + '（正常任务不可能满足，用于验证定向组判官拦截与 agent 标注）。',
  })
  // cancelled（委派级即停抢到）与 failed（回合先收口、终态核对拦截）都是
  // 有效拦截——判官耗时与模型完成剩余委派存在竞态，不锁定具体终态
  expect(['cancelled', 'failed']).toContain(r.status)
  const fit = (r.fits || []).find(f => f.defName === '子代理语义核对')
  expect(fit, `判官失败应写入拟合结果（fits=${_json(r.fits)}）`).toBeTruthy()
  expect(fit.status).toBe('failed')
})

// ---------------------------------------------------------------------------
// 组 3：委派级门禁（迁移自 ai-delegation-gate.spec.ts，用例体与断言原样
// 保留；本地 git 预置仓库改走 batch-helpers.makeProvisionRepo——agent 定义
// 正文原样，frontmatter（description/mode）由 makeProvisionRepo 统一生成）
// ---------------------------------------------------------------------------

const PRIMARY = 'e2e-primary'
const SUB = 'e2e-sub'

function makeAgentRepo(): string {
  return makeProvisionRepo({
    [PRIMARY]: {
      mode: 'primary',
      body: `你是委派编排代理。严格按用户消息里指定的委派次数与内容执行，每次委派用 task 工具
（subagent_type 填 "${SUB}"）。上一次委派完全结束后才发起下一次（严格串行）。
全部委派完成后回复「DONE」。`,
    },
    [SUB]: {
      mode: 'subagent',
      body: `你是子代理。直接用文本回答，不要调用任何工具。
收到「委派一」回答「ONE-ANSWER-OK」；其他任何输入回答「SUB-OK」。`,
    },
  })
}

test('委派级门禁：定向组达标 → completed', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const repo = makeAgentRepo()
  let bid: string | null = null
  try {
    const file = await uploadStaging(token, 'in.txt', 'dg-ok', `dg1-${Date.now()}`)
    const d = await createBatch(token, {
      name: `AITEST-dg-ok-${Date.now()}`,
      prompt: `请连续两次委派 ${SUB} 子代理（严格串行）：第一次 prompt 为「委派一」，`
        + '第二次 prompt 为「请基于你第一次的回答做确认」。两次完成后回复 DONE。',
      agent: PRIMARY,
      provision_repo: repo,
      action_checks: [{ name: '子代理委派核对', check_type: 'verifier',
        rubric: '指定子代理的会话消息中应有两次委派任务（两条 user 消息），'
          + '且第二条 user 消息内容应表明是对第一次回答的确认（含「基于」字样）。',
        subagents: [SUB] }],
      files: [file],
    })
    bid = d.batch.id as string
    const terminal = await waitFor(async () => {
      const dd = await getDetail(token, bid!)
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) return dd
      return null
    }, 420_000, '批次收敛终态', 4000)
    expect(terminal.sessions[0].status).toBe('completed')
    expect(String(terminal.sessions[0].error_message || '')).not.toContain('action_gate')
  } finally {
    if (bid) await cleanupBatch(token, bid)
    fs.rmSync(repo, { recursive: true, force: true })
  }
})

test('委派级门禁：不过即停 → cancelled 且不再发起后续委派', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const token = await adminToken()
  const repo = makeAgentRepo()
  let bid: string | null = null
  try {
    const file = await uploadStaging(token, 'in.txt', 'dg-stop', `dg2-${Date.now()}`)
    const d = await createBatch(token, {
      name: `AITEST-dg-stop-${Date.now()}`,
      prompt: `请连续三次委派 ${SUB} 子代理（严格串行，每次间隔 5 秒）：三次的 prompt 都是「委派一」。`
        + '三次全部完成后回复 DONE。这一步很重要，必须完成全部三次委派。',
      agent: PRIMARY,
      provision_repo: repo,
      action_checks: [{ name: '不可能满足', check_type: 'verifier',
        rubric: '指定子代理的会话消息中必须包含字符串 NO-SUCH-MARK-XYZ'
          + '（不可能满足，用于验证委派级门禁的失败即停）。',
        subagents: [SUB] }],
      files: [file],
    })
    bid = d.batch.id as string
    const terminal = await waitFor(async () => {
      const dd = await getDetail(token, bid!)
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) return dd
      return null
    }, 420_000, '批次收敛终态', 4000)
    // 失败即停：委派级拦截 → cancel_requested → 子任务 cancelled
    // （若未停，模型会继续完成三次委派 → 终态应为 completed 而非 cancelled）
    expect(terminal.sessions[0].status, '委派级门禁应在第一次委派后停止子任务')
      .toBe('cancelled')
    // 即停语义 = 门禁检出即中止（cancelled），不让任务跑完变成 completed。
    // 委派次数不锁死 1：模型并行发起委派时（已知边界），三次会同时完成，
    // 门禁在全部终态后才拦截——此时 3 次都已发生，能保证的是「未跑完」。
    const tc = await (await fetch(
      `${API}/ai/chat/batches/${bid}/children/${terminal.sessions[0].id}/tool-calls`,
      { headers: authHeaders(token) })).json()
    const taskCalls = (tc.calls || []).filter((c: any) => c.tool === 'task')
    expect(taskCalls.length, `委派次数 ${taskCalls.length} 应 ≥1`).toBeGreaterThanOrEqual(1)
  } finally {
    if (bid) await cleanupBatch(token, bid)
    fs.rmSync(repo, { recursive: true, force: true })
  }
})

// ---------------------------------------------------------------------------
// 新增：3 端点补缺（确定性构造，sleepBatch 烧轻量 LLM；attach 烧长窗口）
// ---------------------------------------------------------------------------

test('gate/dry-run：正则预演命中数；verifier 类型明确拒绝预演', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })
  // 路由核实（ai_chat_batches.py:219-244 + agent_ledger.count_tree_tool_calls:824）：
  //  - tool+args_pattern 齐全且正则过 PG 校验 → {evidence, samples:[{args,state}]}；
  //    brief 草稿的 /match|hit|count/ 响应形状不存在，改为按实况断言 evidence。
  //  - check_type=verifier → 200 {supported:false, note}——「显式标注无法预演」，
  //    不是 4xx 拒绝；brief 草稿预期 >=400 按路由实况改为断言 supported:false。
  //    （且入参字段名是 check_type 而非 type——type 会被当未知字段落下、
  //    走 tool/pattern 缺失的 400 分支，那就不是在测 verifier 拒绝路径了。）
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 10 })
  try {
    await waitBatchTerminal(tk, bid, 180_000)   // bash sleep 落账且 state=completed 后再预演
    const sid = (await getDetail(tk, bid)).sessions[0].id
    const ok = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/gate/dry-run`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ tool: 'bash', args_pattern: 'sleep' }),
    })
    expect(ok.status).toBeLessThan(300)
    const body = await ok.json()
    expect(body.evidence, `预演命中数应 ≥1（body=${_json(body)}）`).toBeGreaterThanOrEqual(1)
    expect((body.samples || []).length).toBeGreaterThanOrEqual(1)

    const bad = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/gate/dry-run`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ check_type: 'verifier', rubric: 'x' }),
    })
    expect(bad.status).toBeLessThan(300)
    const bb = await bad.json()
    expect(bb.supported).toBe(false)          // 明确「不假装跑过证据匹配」
    expect(String(bb.note)).toContain('verifier')
  } finally { await cleanupBatch(tk, bid) }
})

test('children/<sid>/tool-calls 账本取材：bash 调用入账', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })
  // 路由核实（ai_chat_batches.py:185-216）：{calls:[{ocSessionId,subtaskId,
  // tool,args,state,occurredAt}]}，覆盖该子会话及其子代理树。brief 草稿的
  // 「JSON 串 contains sleep」改为结构化断言（tool==='bash' 且 args 含 sleep）。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 10 })
  try {
    await waitBatchTerminal(tk, bid, 180_000)
    const sid = (await getDetail(tk, bid)).sessions[0].id
    const r = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/tool-calls`, { headers: authHeaders(tk) })
    expect(r.status).toBe(200)
    const { calls } = await r.json()
    const bashCalls = (calls as any[]).filter((c: any) => c.tool === 'bash')
    expect(bashCalls.length, `应有 bash 调用入账（calls=${_json(calls)}）`).toBeGreaterThanOrEqual(1)
    expect(bashCalls.some((c: any) => /sleep/.test(String(c.args)))).toBe(true)
  } finally { await cleanupBatch(tk, bid) }
})

test('action-checks/attach 运行中补挂期望：终态被核对', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  // 路由核实（ai_chat_batches.py:268-299 + agent_ledger.validate_checks:292）：
  //  - 入参为 {session_ids:[...], checks:[...]}——brief 草稿的 batch_seq 不是
  //    该端点入参；checks 按 validate_checks 口径：{name, check_type:'tool',
  //    tool, args_pattern, min_count}（'tool_assert'/'type' 字面量不存在）。
  //  - 终态断言：detail sessions 的 gate_passed/gate_failed 是**计数**
  //    （action_expectations 按 last_status passed/failed 的条数，
  //    batch_repo.py:372-377），不是布尔——brief 草稿的
  //    gate_status==='passed' 改为 gate_passed>=1 且 gate_failed===0。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })   // 长窗口内补挂
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 1, 120_000, 'running')
    const sid = (await getDetail(tk, bid)).sessions[0].id
    const r = await fetch(`${API}/ai/chat/batches/${bid}/action-checks/attach`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({
        session_ids: [sid],
        checks: [{ name: 'bash sleep 入账', check_type: 'tool',
                   tool: 'bash', args_pattern: 'sleep', min_count: 1 }],
      }),
    })
    expect(r.status).toBeLessThan(300)
    const body = await r.json()
    expect(body.registered, `应登记 1 条期望（body=${_json(body)}）`).toBe(1)
    const final = await waitBatchTerminal(tk, bid, 420_000)
    expect(final.sessions[0].gate_passed, '补挂的期望终态应被核对为 passed').toBeGreaterThanOrEqual(1)
    expect(final.sessions[0].gate_failed).toBe(0)
    expect(String(final.sessions[0].error_message || '')).not.toContain('action_gate')
  } finally { await cleanupBatch(tk, bid) }
})
