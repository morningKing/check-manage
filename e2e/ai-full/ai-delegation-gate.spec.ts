/**
 * 委派级门禁端到端（真实链路，2026-09-30）。
 *
 * 场景：本地 git 预置仓库定义 primary agent（e2e-primary，串行委派
 * e2e-sub 多次）+ subagent（e2e-sub，固定口径回答）；批任务带
 * `subagents: ['e2e-sub']` 的 verifier 期望 → 委派级门禁在 e2e-sub
 * 全部终态时提前判定（不等整个会话结束）。
 *
 * 覆盖：
 *  1) 达标：rubric 要求「子代理会话消息应有两次委派且第二条引用第一条
 *     回答」→ 判官读定向组材料判定通过 → 子任务 completed；
 *  2) 不过即停：rubric 要求「子代理会话消息必须包含 NO-SUCH-MARK-XYZ」
 *     （不可能满足），prompt 要求三次委派 → 第一次委派终态即判官拦截 →
 *     未开 gate_retry → 子任务立即 cancelled（不再继续第二、三次委派），
 *     general/e2e-sub 子代理会话只有一次委派任务（即停实锤）。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import {
  adminToken, authHeaders, uploadStaging, createBatch, getDetail,
  cleanupBatch, makeProvisionRepo, waitFor,
} from './batch-helpers'

const PRIMARY = 'e2e-primary'
const SUB = 'e2e-sub'

function makeAgentRepo(): string {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-dg-'))
  fs.mkdirSync(path.join(repo, 'agent'), { recursive: true })
  fs.writeFileSync(path.join(repo, 'agent', `${PRIMARY}.md`), `---
description: E2E 委派编排主代理
mode: primary
---
你是委派编排代理。严格按用户消息里指定的委派次数与内容执行，每次委派用 task 工具
（subagent_type 填 "${SUB}"）。上一次委派完全结束后才发起下一次（严格串行）。
全部委派完成后回复「DONE」。`, 'utf-8')
  fs.writeFileSync(path.join(repo, 'agent', `${SUB}.md`), `---
description: E2E 固定口径子代理
mode: subagent
---
你是子代理。直接用文本回答，不要调用任何工具。
收到「委派一」回答「ONE-ANSWER-OK」；其他任何输入回答「SUB-OK」。`, 'utf-8')
  const git = (args: string[]) =>
    execFileSync('git', ['-c', 'core.autocrlf=false', ...args],
      { cwd: repo, shell: false })
  git(['init', '-q'])
  git(['add', '.'])
  git(['-c', 'user.name=e2e', '-c', 'user.email=e2e@local', 'commit', '-qm', 'agents'])
  return repo
}

test.setTimeout(1_200_000)

test('委派级门禁：定向组达标 → completed', async () => {
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

test('委派级门禁：不过即停 → cancelled 且不再发起后续委派', async () => {
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
    // 子代理侧只发生了一次委派（停止发生在第一次委派终态判定后）
    const tc = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/batches/${bid}/children/${terminal.sessions[0].id}/tool-calls`,
      { headers: authHeaders(token) })).json()
    const taskCalls = (tc.calls || []).filter((c: any) => c.tool === 'task')
    expect(taskCalls.length, `委派次数 ${taskCalls.length} 应为 1（即停后不再继续）`)
      .toBe(1)
  } finally {
    if (bid) await cleanupBatch(token, bid)
    fs.rmSync(repo, { recursive: true, force: true })
  }
})
