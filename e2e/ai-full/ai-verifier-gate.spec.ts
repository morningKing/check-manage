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
 *     带理由续跑，第二轮模型补写文件 → 判官取证通过 → completed，retry_count ≥ 1。
 *
 * 断言来源注记：GET /ai/chat/batches/<id> 的 sessions[] 不暴露 retry_count
 * （detail SELECT 只出 id/status/…/gate_failed/gate_passed），故用例 3 的
 * 「发生过判官拦截后的修复续跑」改由 attempts 端点断言——gate retry 重排前
 * 会把最新 running attempt 收口为 status='recovering'、error_code='GATE_RETRY'
 * （batch_engine._close_attempt_for_requeue），GET /<batch_id>/attempts 可观测。
 * 断言语义不变。
 * 断言直连后端 3002（同 ai-retry-failed.spec.ts 的理由）。
 */
import { test, expect } from '@playwright/test'

const API = 'http://127.0.0.1:3002'

async function adminToken2(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  return (await r.json()).token
}

test.setTimeout(1_200_000)

interface RunOpts {
  name: string
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
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('VERIFIER-E2E-INPUT-42')], { type: 'text/plain' }), 'in.txt')
  form.append('upload_session_id', `vf-${Date.now()}`)
  const up = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form,
  })
  expect(up.status).toBe(201)
  const create = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: HDRS,
    body: JSON.stringify({
      name: `AITEST-vf-e2e-${Date.now()}`,
      prompt: opts.prompt,
      gate_retry: opts.gateRetry ?? false,
      action_checks: [{ name: '语义核对', check_type: 'verifier', rubric: opts.rubric }],
      files: [await up.json()],
    }),
  })
  expect(create.status).toBe(201)
  const d = await create.json()
  const bid = d.batch.id as string
  try {
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await (await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })).json()
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) {
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
    await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
      method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
    })
  }
}

test('verifier 门禁：达标 → completed 无失败', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'ok',
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含输入文件 uploads/in.txt 的原文内容(VERIFIER-E2E-INPUT-42)。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('verifier 门禁：不达标 → failed 且 error_message 带判官理由', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'bad',
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ(正常任务不可能满足,用于验证判官拦截)。',
  })
  expect(r.status).toBe('failed')
  expect(r.errMsg).toContain('action_gate')
  expect(r.errMsg).toContain('判官未通过')
})

test('verifier 门禁：gate_retry 闭环——判官理由驱动修复续跑后通过', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'retry',
    prompt: '只复述 uploads/in.txt 的原文,不要写任何文件。',
    rubric: '工作区 outputs/report.md 必须存在,且其内容包含输入原文(VERIFIER-E2E-INPUT-42)。请打开工作区核对文件。',
    gateRetry: true,
  })
  expect(r.gateRetried).toBe(true)      // 第一轮判官拦截 → GATE_RETRY 定向修复续跑
  expect(r.status).toBe('completed')    // 第二轮补写文件 → 判官取证通过
})
