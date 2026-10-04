/**
 * verifier 判官——subagent 定向组真实链路（2026-09-30 新增粒度）。
 *
 * 场景：prompt 要求默认 agent 用 task 工具对内置 subagent `general` 连续
 * 委派两次（第二次基于第一次的回答，串行——并行委派时锚点/消息尚未落库，
 * 属设计内边界）；verifier 期望带 subagents: ['general'] → 定向组材料 =
 * general 名下子代理会话消息 + 其轨迹。
 *
 * 覆盖：
 *  1) 正向：rubric 要求「指定子代理的会话消息中应有两次委派任务且第二条
 *     引用第一条的回答」→ 判官读定向组材料判定 → completed（gate passed）；
 *  2) 负向 + agent 标注：rubric 要求「子代理会话消息必须包含
 *     SUBAGENT-NO-MARK-XYZ」（不可能满足）→ failed，error_message 含
 *     「[agent=general]」与「判官未通过」——定向组失败文案的 agent 前缀。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import {
  adminToken, authHeaders, uploadStaging, createBatch, getDetail,
  cleanupBatch, BATCH_TERMINAL,
} from './batch/batch-helpers'

const API = 'http://127.0.0.1:3002'
const _json = (v: unknown) => JSON.stringify(v)

test.setTimeout(1_200_000)

interface RunOpts { rubric: string; name: string }

async function runBatch(token: string, opts: RunOpts): Promise<{ status: string; errMsg: string; sid: string; fits: Array<{ defName: string; status: string }> }> {
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
          `http://127.0.0.1:3002/ai/chat/sessions/${sid}/skill-fit`,
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

test('定向组：subagents=[general] 判官读子代理材料核对 → completed', async () => {
  const token = await adminToken()
  const r = await runBatch(token, {
    name: 'ok',
    rubric: '指定子代理的会话消息中应有两次委派任务（两条 user 消息），'
      + '且第二条 user 消息内容以「基于」开头并引用了第一条 assistant 回答。请核对子代理会话消息。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('定向组：负向 → 委派级即停 cancelled，判官失败记入拟合摘要', async () => {
  // 2026-10-01 委派级门禁上线后的新语义：名单内子代理终态即判定 failed
  // 且未开 gate_retry → 立即 cancel（不再跑完全程），终态为 cancelled。
  // 判官拦截证据改由 owner 拟合摘要（GET /sessions/<sid>/skill-fit）断言。
  const token = await adminToken()
  const r = await runBatch(token, {
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
