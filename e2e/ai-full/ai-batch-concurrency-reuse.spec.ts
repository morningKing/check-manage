/**
 * 批任务「并发拉满 × 自定义 primary agent × subagent 会话复用」端到端验证。
 *
 * 场景（2026-09-29）：
 *  - 测试自建本地 git 预置仓库，内含自定义 agent 定义：
 *      agent/e2e-primary.md（mode: primary）——指令固定：两次串行用 task 工具
 *      委派 e2e-sub（第 2 次 prompt 依赖第 1 次的回答原文，杜绝并行委派——
 *      并行发起时第 2 次的注入查询会跑在第 1 次锚点登记之前，属设计内边界），
 *      把两次回答写入 outputs/result.txt；
 *      agent/e2e-sub.md（mode: subagent）——固定口径回答，不调工具。
 *    预置仓库经批任务 provision_repo 注入子任务工作区（派发前 clone 进
 *    .opencode/，OpenCode 即可发现这两个 agent）。
 *  - 批任务 6 个子任务 × worker 3 个并发槽（MAX_CONCURRENT=3）→ 必然满载
 *    且有排队，覆盖并发拉满下的全量执行。
 *  - 批任务开启 subagent_reuse: ['e2e-sub']：OC 插件强制层把第 2+ 次委派
 *    钉回同一子代理会话（task_id 注入 + after 回调登记锚点）。
 *
 * 断言分三层：
 *  1. 并发观测：轮询详情，running 峰值 === 3（槽位满载）且出现 pending 排队；
 *  2. 全量收敛：6/6 子任务 completed，批计数与批次状态正确；
 *  3. 复用实锤（逐子任务）：
 *     a. tool-calls 账本含 ≥2 次 task 委派（args 带 subagent_type=e2e-sub）；
 *     b. 内部 /reuse 权威查询（token 取自已部署插件文件）：锚点已登记且
 *        taskId 为 ses_ 前缀的 OpenCode 子会话；
 *     c. 该钉住子会话的消息面含「报告一号」「报告二号」两条 user 消息、
 *        任务段（turn_segments）≥2 —— 第二次委派确实续跑了同一子会话。
 *
 * 断言直连后端 3002（同 ai-reexecute.spec.ts 的理由）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const API = 'http://127.0.0.1:3002'
const N = 6          // 子任务数：> MAX_CONCURRENT(3)，保证满载 + 排队
const PRIMARY = 'e2e-primary'
const SUB = 'e2e-sub'

async function adminToken2(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  return (await r.json()).token
}

/** 本地 git 预置仓库：自定义 primary agent + subagent（OpenCode agent md）。 */
function makeProvisionRepo(): string {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-prov-'))
  fs.mkdirSync(path.join(repo, 'agent'), { recursive: true })
  fs.writeFileSync(path.join(repo, 'agent', `${PRIMARY}.md`), `---
description: E2E 批任务主代理：两次委派 ${SUB} 并汇总结果
mode: primary
---
你是批任务子任务的主代理。严格按以下步骤执行，不要跳步、不要减少委派次数。
两次委派必须串行：第 2 步的 prompt 依赖第 1 步的回答，拿到第 1 步结果之前不得发起第 2 次委派，也不得把两次委派放在同一条消息里并行调用：
1. 用 task 工具委派 subagent（subagent_type 填 "${SUB}"），prompt 为「报告一号」，等待并记住其回答。
2. 用 task 工具再次委派 subagent（subagent_type 仍填 "${SUB}"），prompt 为「二号确认：<第 1 步回答原文>」（把第 1 步的回答原文原样拼接进去）。
3. 用 write 工具把两次回答原文各占一行写入 outputs/result.txt。
4. 最后回复「DONE」。
`, 'utf-8')
  fs.writeFileSync(path.join(repo, 'agent', `${SUB}.md`), `---
description: E2E 子代理：固定口径回答
mode: subagent
---
你是子代理。直接用文本回答，不要调用任何工具。
收到「报告一号」回答「一号OK」；收到以「二号确认：」开头的输入回答「二号OK」；其他任何输入回答「SUB-OK」。
`, 'utf-8')
  const g = (args: string[]) =>
    execFileSync('git', ['-c', 'core.autocrlf=false', ...args], { cwd: repo })
  g(['init', '-q'])
  g(['add', '.'])
  g(['-c', 'user.name=e2e', '-c', 'user.email=e2e@local', 'commit', '-qm', 'agents'])
  return repo
}

test.setTimeout(1_200_000)

test('批任务并发拉满：6 子任务全量执行 + 自定义 agent 委派 subagent + 会话复用', async () => {
  const token = await adminToken2()
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const repo = makeProvisionRepo()
  let bid: string | null = null
  try {
    // 1) staging ×6 + 建批（自定义 agent + 预置仓库 + 子代理复用名单）
    const files: any[] = []
    for (let i = 0; i < N; i++) {
      const form = new FormData()
      form.append('file', new Blob([Buffer.from(`child-${i}`)], { type: 'text/plain' }), `in-${i}.txt`)
      form.append('upload_session_id', `cc-${Date.now()}`)
      const up = await fetch(`${API}/ai/chat/batches/staging/upload`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form,
      })
      expect(up.status).toBe(201)
      files.push(await up.json())
    }
    const create = await fetch(`${API}/ai/chat/batches`, {
      method: 'POST', headers: HDRS,
      body: JSON.stringify({
        name: `AITEST-cc-e2e-${Date.now()}`,
        prompt: '按 agent 指令执行。',
        agent: PRIMARY,
        provision_repo: repo,
        subagent_reuse: [SUB],
        files,
      }),
    })
    expect(create.status).toBe(201)
    const d = await create.json()
    bid = d.batch.id as string
    expect(d.sessions.length).toBe(N)

    const getDetail = async () =>
      await (await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })).json()

    // 2) 并发拉满观测 + 等待全量收敛（同一轮询循环）
    let maxRunning = 0
    let sawQueued = false
    let terminal: any = null
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await getDetail()
      const counts: Record<string, number> = {}
      for (const s of dd.sessions) counts[s.status] = (counts[s.status] || 0) + 1
      maxRunning = Math.max(maxRunning, counts.running || 0)
      if ((counts.running || 0) > 0 && (counts.pending || 0) > 0) sawQueued = true
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) { terminal = dd; break }
      await new Promise(rr => setTimeout(rr, 1500))
    }
    expect(terminal, '批次未在时限内收敛到终态').toBeTruthy()
    // 并发拉满：3 槽位全部占用过，且出现过排队中的 pending 子任务
    expect(maxRunning, `running 峰值 ${maxRunning}`).toBe(3)
    expect(sawQueued, '未观测到 pending 排队（槽位未打满即收敛？）').toBe(true)

    // 3) 全量执行：6/6 completed，计数与批次状态正确
    expect(terminal.batch.total).toBe(N)
    expect(terminal.batch.done).toBe(N)
    for (const s of terminal.sessions) expect(s.status).toBe('completed')

    // 4) 复用断言前置：内部端点地址/token 从已部署插件文件提取（与插件
    //    同源——插件能用的查询，测试就能用）
    const pluginSrc = fs.readFileSync(path.join(
      os.homedir(), '.config', 'opencode', 'plugin', 'baize-subagent-reuse.js'), 'utf-8')
    const endpoint = /ENDPOINT = process\.env\.BAIZE_SUBAGENT_REUSE_URL \|\| '([^']*)'/.exec(pluginSrc)?.[1]
    const itoken = /TOKEN = process\.env\.BAIZE_INTERNAL_TOKEN \|\| '([^']*)'/.exec(pluginSrc)?.[1]
    expect(endpoint, '插件文件未嵌入复用端点').toBeTruthy()
    expect(itoken, '插件文件未嵌入内部 token').toBeTruthy()

    // 5) 逐子任务：委派发生 + 复用锚点 + 同一子会话承载两段委派
    for (const s of terminal.sessions) {
      const sid = s.id as string
      // 5a) 账本：主代理确实委派了 ≥2 次，且都指向 e2e-sub
      const tc = await (await fetch(
        `${API}/ai/chat/batches/${bid}/children/${sid}/tool-calls`, { headers: HDRS })).json()
      const taskCalls = (tc.calls || []).filter((c: any) => c.tool === 'task')
      expect(taskCalls.length, `子任务 ${sid} 的委派次数 ${taskCalls.length} < 2`)
        .toBeGreaterThanOrEqual(2)
      for (const c of taskCalls) expect(c.args as string).toContain(SUB)

      // 5b) 复用锚点（权威状态）：after 回调已登记，taskId 为 OC 子会话 id
      const reuse = await (await fetch(
        `${endpoint}/reuse?session=${encodeURIComponent(s.opencode_session_id)}&agent=${SUB}`,
        { headers: { 'x-internal-token': itoken as string } })).json()
      expect(reuse.enabled, `子任务 ${sid} 复用未生效`).toBe(true)
      expect(String(reuse.taskId)).toMatch(/^ses_/)

      // 5c) 复用实锤：两次委派落在同一个子会话里（两条 user 消息 + 两个任务段）
      const stm = await (await fetch(
        `${API}/ai/chat/sessions/${sid}/subtasks/${reuse.taskId}/messages`,
        { headers: HDRS })).json()
      expect(stm.error, JSON.stringify(stm)).toBeUndefined()
      expect(stm.subtask.segments.length, '钉住子会话任务段 < 2（第二次委派未续跑同一会话）')
        .toBeGreaterThanOrEqual(2)
      const userTexts = JSON.stringify(stm.messages.filter((m: any) => m.role === 'user'))
      expect(userTexts).toContain('报告一号')
      expect(userTexts).toContain('二号确认：')
    }
  } finally {
    if (bid) {
      await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
    }
    fs.rmSync(repo, { recursive: true, force: true })
  }
})
