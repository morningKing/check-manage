/**
 * 动作门禁 verifier 判官 端到端（真实链路，2026-09-29）。
 * 覆盖三个方向：
 *  1) 达标：prompt「把 uploads/in.txt 的原文完整复述一遍」，verifier rubric
 *     要求「最终回复必须包含输入文件的原文内容」→ completed 且无 action_gate 失败；
 *  2) 不达标：同 prompt，rubric 要求「最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ」
 *     （正常任务不可能满足）→ failed，error_message 含「action_gate」与「判官未通过」；
 *  3) gate_retry 闭环：prompt「只复述 uploads/in.txt 的原文，不要写任何文件」，
 *     rubric 要求「工作区 outputs/report.md 必须存在且内容包含输入原文」（判官需
 *     主动取证——read/list 工作区）→ 第一轮 gate failed → 批级 gate_retry 开启时
 *     带理由续跑，第二轮模型补写文件 → 判官取证通过 → completed，发生过 GATE_RETRY。
 *
 * 断言来源注记：GET /ai/chat/batches/<id> 的 sessions[] 不暴露 retry_count
 * （detail SELECT 只出 id/status/…/gate_failed/gate_passed），故用例 3 的
 * 「发生过判官拦截后的修复续跑」改由 attempts 端点断言——gate retry 重排前
 * 会把最新 running attempt 收口为 status='recovering'、error_code='GATE_RETRY'
 * （batch_engine._close_attempt_for_requeue），GET /<batch_id>/attempts 可观测。
 * 断言语义不变。
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import {
  adminToken, authHeaders, uploadStaging, createBatch,
  getDetail, cleanupBatch, BATCH_TERMINAL,
} from './batch-helpers'

test.setTimeout(1_200_000)

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

async function runBatch(token: string, opts: RunOpts): Promise<RunResult> {
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
        const att = await (await fetch(`http://127.0.0.1:3002/ai/chat/batches/${bid}/attempts`, { headers: HDRS })).json()
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

test('verifier 门禁：达标 → completed 无失败', async () => {
  const token = await adminToken()
  const r = await runBatch(token, {
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含输入文件 uploads/in.txt 的原文内容(VERIFIER-E2E-INPUT-42)。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('verifier 门禁：不达标 → failed 且 error_message 带判官理由', async () => {
  const token = await adminToken()
  const r = await runBatch(token, {
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ(正常任务不可能满足,用于验证判官拦截)。',
  })
  expect(r.status).toBe('failed')
  expect(r.errMsg).toContain('action_gate')
  expect(r.errMsg).toContain('判官未通过')
})

test('verifier 门禁：gate_retry 闭环——判官理由驱动修复续跑后通过', async () => {
  const token = await adminToken()
  const r = await runBatch(token, {
    prompt: '只复述 uploads/in.txt 的原文,不要写任何文件。',
    rubric: '工作区 outputs/report.md 必须存在,且其内容包含输入原文(VERIFIER-E2E-INPUT-42)。请打开工作区核对文件。',
    gateRetry: true,
  })
  expect(r.gateRetried).toBe(true)      // 第一轮判官拦截 → GATE_RETRY 定向修复续跑
  expect(r.status).toBe('completed')    // 第二轮补写文件 → 判官取证通过
})
