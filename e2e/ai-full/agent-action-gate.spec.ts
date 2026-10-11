/**
 * 动作门禁过程检查(checkpoint)与策略库 E2E——真 LLM(2026-10-11 spec §11)。
 *
 * 文件沿革:本文件名曾承载"动作账本与到位门禁"套件,2026-10-04 收编进
 * batch/gate.spec.ts 后原文件删除(cf3ee34);本文件按 gate-checkpoint-policy
 * 特性 Task 13 重建,承载 checkpoint 过程检查与策略库复用的真 LLM 场景。
 * 既有 deny/终态核对/verifier/gate_retry/徽标场景在 batch/gate.spec.ts,不重复。
 *
 * 场景(设计 §11 E2E 1-5;⑥ agent 定向降级为单测覆盖,见文末 skip 桩):
 * 1. before 闭环:checkpoint(before) 拦首次 write → 注入核对提示词 → 模型读
 *    知识库 → 重写 → 终态 passed(「门禁 ✓」徽标)+ 账本出现 read docs/kb;
 * 2. 预算防冗余:max_checks=1,第二次写不再被拦(期望行 fired_count=1,
 *    报告重写与副本写入照常完成);
 * 3. 双保险:checkpoint 提示词指向不可满足的保险库文件 → 终态核对 fail →
 *    gate_retry 修正轮 → 断言「门禁参与判定」(GATE_RETRY attempt +
 *    retry_count≥1;终态 completed(修正轮补齐 assert)或 failed+action_gate
 *    前缀都算场景有效);
 * 4. after 非阻断:写完在工具结果尾部追加检查提示词(append_result 形态,
 *    消息导出含标记)→ 模型复读并补写标记 → 工作区文件含标记 + 终态 passed;
 * 5. 策略复用:API 建策略 → 两批引用各自登记期望行(期望行即快照)→ 解绑
 *    (PATCH gatePolicyId=null)后 append 新派发 → 不再登记(旧子任务历史行保留)。
 *
 * 运行前置:插件文件须含 after 钩子(skillopt.PLUGIN_JS)——后端启动时重写
 * 插件文件,OC serve 需 POST /ai/opencode/restart 重载(环境一次性准备,
 * 不入用例;dev 栈冷启时 app.py 自带重写,OC serve 随后由平台拉起亦加载新件)。
 *
 * LLM 预算:1-4 各 1 个真实子会话;5 为 3 个轻会话(直接回复 OK)。
 * 断言直连后端 3002(理由见 batch/batch-helpers.ts 头注释);期望行断言经
 * toolbox.dbSeed(db_exec.py 桥)只读查询——期望行由引擎登记,用例不播种;
 * 删批级联清理期望行(FK),故 DB 断言必须在 cleanupBatch 之前。
 */
import { test, expect, type Page } from '@playwright/test'
import {
  API, authHeaders, uploadStaging, createBatch,
  cleanupBatch, waitBatchTerminal,
} from './batch/batch-helpers'
import {
  adminTokenCached, dbSeed, tag, childWorkspace, readWorkspaceFile,
} from './batch/toolbox'
import { gotoWithAuth, screenshot } from './helpers'
import { expandBatchGroup } from './batch/ui-helpers'

test.setTimeout(1_200_000)

const _json = (v: unknown) => JSON.stringify(v)

/** 知识库输入(三条要点,供模型整理进 docs/kb/summary.md 与报告) */
const KB_INPUT = [
  'E2E 知识库输入文件,包含三条要点:',
  '要点一:检查点规则在工具调用前后注入核对提示词。',
  '要点二:预算 max_checks 限制注入次数,防止重复检查。',
  '要点三:终态断言以服务端动作账本为唯一证据来源。',
].join('\n')

/** checkpoint(before) 规则:拦 outputs/report.md 的写入,终态断言 read 知识库。
 * args_pattern 锚定文件名(report\.md),既不误伤 docs/kb/summary.md 的知识库
 * 写入,也不匹配 report-copy.md(副本);assert_spec 模式容忍 / 与 \ 分隔符。 */
function beforeRule(name: string, prompt: string) {
  return {
    name, check_type: 'tool', mode: 'checkpoint',
    tool: 'write', args_pattern: 'report\\.md',
    timing: 'before', max_checks: 1,
    prompt,
    assert_spec: { tool: 'read', args_pattern: 'docs[/\\\\]kb', min_count: 1 },
  }
}

/** 真登录态进 /ai-chat;等真实元素,绝不等 networkidle(SSE 常驻)。
 * 与 batch/gate.spec.ts 的 gotoChat 同款(vite 冷编译整页重载时重进一次)。 */
async function gotoChat(page: Page): Promise<void> {
  await gotoWithAuth(page, '/ai-chat')
  const sidebar = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
  for (let i = 0; i < 3; i++) {
    try {
      await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
      return
    } catch {
      await gotoWithAuth(page, '/ai-chat')
    }
  }
  await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
}

// ---------------------------------------------------------------------------
// 场景 1:checkpoint(before) 闭环
// ---------------------------------------------------------------------------

test('before 闭环:checkpoint 拦截首写→读知识库→重写通过(门禁 ✓ 徽标)',
     async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 1 个真实子会话(两步写入)
  const tk = await adminTokenCached()
  const name = tag('gate-cp-before')
  const staged = await uploadStaging(tk, 'in.txt', KB_INPUT, `e2e-cp1-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '分两步完成,全部用 write 工具(不要用 bash 写文件):'
      + '(1) 先创建知识库文件 docs/kb/summary.md,内容为 uploads/in.txt 的三条要点,每行一条;'
      + '(2) 再创建报告文件 outputs/report.md,复述这三条要点。',
    files: [staged],
    action_checks: [beforeRule('报告写入前核对',
      '写 outputs/report.md 之前,必须先用 read 工具完整读取 docs/kb/summary.md,'
      + '并核对你将写入的要点与该文件一致;未读取该文件前禁止写入 outputs/report.md。')],
  })
  const bid = created.batchId || created.batch?.id
  try {
    const final = await waitBatchTerminal(tk, bid, 600_000, 5000)
    expect(final.batch.status).toBe('completed')
    const child = final.sessions[0]
    expect(child.status).toBe('completed')
    expect(child.gate_passed, 'checkpoint assert_spec(读知识库)应核对为 passed')
      .toBeGreaterThanOrEqual(1)
    expect(child.gate_failed).toBe(0)
    expect(String(child.error_message || '')).not.toContain('action_gate')

    // 拦截发生:期望行预算被消耗(before 注入一次)
    const fired = dbSeed(`SELECT fired_count, max_checks FROM action_expectations
      WHERE scope_id = '${child.id}' AND mode = 'checkpoint'`)
    expect(fired.length, '应登记 1 条 checkpoint 期望行').toBe(1)
    expect(Number(fired[0][0]), 'before 注入应消耗预算 fired_count≥1')
      .toBeGreaterThanOrEqual(1)

    // 闭环实锤:账本出现对知识库的 completed read(assert_spec 的证据来源)
    const tc = await (await fetch(
      `${API}/ai/chat/batches/${bid}/children/${child.id}/tool-calls`,
      { headers: authHeaders(tk) })).json()
    const kbReads = (tc.calls || []).filter((c: any) =>
      c.tool === 'read' && /docs[/\\]kb/.test(String(c.args)))
    expect(kbReads.length,
      `账本应有 read docs/kb(calls=${_json(tc.calls).slice(0, 400)})`)
      .toBeGreaterThanOrEqual(1)
    expect(kbReads.some((c: any) => c.state === 'completed')).toBe(true)

    // UI:子任务行「门禁 ✓」徽标(gate_passed 计数驱动,UI 断言保留)
    await gotoChat(page)
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.gate-badge--pass')).toContainText('门禁 ✓',
      { timeout: 60_000 })
    await screenshot(page, 'gate-checkpoint-before-pass')
  } finally {
    await cleanupBatch(tk, bid)
  }
})

// ---------------------------------------------------------------------------
// 场景 2:预算防冗余(max_checks=1)
// ---------------------------------------------------------------------------

test('预算防冗余:max_checks=1 第二次写不再被拦(fired_count=1)',
     async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 1 个真实子会话(三步写入)
  const tk = await adminTokenCached()
  const name = tag('gate-cp-budget')
  const staged = await uploadStaging(tk, 'in.txt', KB_INPUT, `e2e-cp2-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '严格按顺序完成三步,全部用 write 工具(不要用 bash 写文件):'
      + '(1) 创建 docs/kb/summary.md,内容为 uploads/in.txt 的三条要点;'
      + '(2) 创建 outputs/report.md,复述这三条要点;'
      + '(3) 把与 outputs/report.md 完全相同的内容再写一份到 outputs/report-copy.md。',
    files: [staged],
    action_checks: [beforeRule('报告写入前核对·预算',
      '写 outputs/report.md 之前,必须先用 read 工具完整读取 docs/kb/summary.md;'
      + '未读取该文件前禁止写入 outputs/report.md。')],
  })
  const bid = created.batchId || created.batch?.id
  try {
    const final = await waitBatchTerminal(tk, bid, 600_000, 5000)
    expect(final.batch.status).toBe('completed')
    const child = final.sessions[0]
    expect(child.status).toBe('completed')
    expect(child.gate_passed).toBeGreaterThanOrEqual(1)

    // 预算核心断言:只有首写被拦(fired_count=1);重写与副本写不再被拦不消耗
    const fired = dbSeed(`SELECT fired_count, max_checks FROM action_expectations
      WHERE scope_id = '${child.id}' AND name = '报告写入前核对·预算'`)
    expect(fired.length, '应登记 1 条期望行').toBe(1)
    expect(Number(fired[0][0]), '预算 1 次:只有首写注入').toBe(1)
    expect(Number(fired[0][1])).toBe(1)

    // 第二次写不再被拦的实锤:报告重写与副本写入都照常完成(非 error 状态)
    const tc = await (await fetch(
      `${API}/ai/chat/batches/${bid}/children/${child.id}/tool-calls`,
      { headers: authHeaders(tk) })).json()
    const writes = (tc.calls || []).filter((c: any) => c.tool === 'write')
    expect(writes.some((c: any) =>
      /report\.md/.test(String(c.args)) && c.state === 'completed'),
      'outputs/report.md 应有完成的写入(拦截后重写放行)').toBe(true)
    expect(writes.some((c: any) =>
      /report-copy\.md/.test(String(c.args)) && c.state === 'completed'),
      '副本写入应照常完成(预算耗尽放行)').toBe(true)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

// ---------------------------------------------------------------------------
// 场景 3:双保险(终态兜底 + gate_retry)
// ---------------------------------------------------------------------------

test('双保险:提示词不可满足→终态核对 fail→gate_retry 修正(门禁参与判定)',
     async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 1 个真实子会话(至多两轮)
  const tk = await adminTokenCached()
  const name = tag('gate-cp-retry')
  const vault = `vault-e2e-${Date.now()}`   // 不存在的保险库目录(按次生成防撞)
  const staged = await uploadStaging(tk, 'in.txt', KB_INPUT, `e2e-cp3-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '把 uploads/in.txt 的三条要点用 write 工具写入 outputs/report.md。',
    files: [staged],
    gate_retry: true,
    action_checks: [{
      name: '保险库核对·不可满足', check_type: 'tool', mode: 'checkpoint',
      tool: 'write', args_pattern: 'report\\.md',
      timing: 'before', max_checks: 1,
      prompt: `写 outputs/report.md 之前,必须先用 read 工具读取 docs/${vault}/lock.md。`
        + '该文件由系统保险库管理,当前不存在且禁止以任何手段创建(创建也会失败);'
        + '若读取失败,说明本次任务被禁止写入报告——不要创建任何文件,'
        + '不要写入 outputs/report.md,直接在回复中说明原因并结束任务。',
      assert_spec: { tool: 'read', args_pattern: vault, min_count: 1 },
    }],
  })
  const bid = created.batchId || created.batch?.id
  try {
    const final = await waitBatchTerminal(tk, bid, 720_000, 5000)
    const child = final.sessions[0]

    // 门禁参与判定的实锤:终态核对不过 → GATE_RETRY 定向修复轮(retry 预算 1 次)
    const att = await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`,
      { headers: authHeaders(tk) })).json()
    expect((att.attempts || []).some((a: any) => a.error_code === 'GATE_RETRY'),
      `attempt 链应含 GATE_RETRY(${_json(att.attempts)?.slice(0, 300)})`).toBe(true)
    const rc = dbSeed(`SELECT retry_count FROM ai_chat_sessions WHERE id = '${child.id}'`)
    expect(Number(rc[0][0]), '修正轮应消耗 retry_count≥1').toBeGreaterThanOrEqual(1)

    // 韧性断言(断言「门禁参与判定」本身,不锁定模型最终是否补齐):
    // completed = 修正轮后 assert 被满足(终态核对放行);
    // failed    = 两轮都不过(终态兜底拦截,带 action_gate 明细)。
    expect(['completed', 'failed']).toContain(final.batch.status)
    if (final.batch.status === 'completed') {
      expect(child.status).toBe('completed')
      expect(child.gate_passed).toBeGreaterThanOrEqual(1)
    } else {
      expect(child.status).toBe('failed')
      expect(String(child.error_message || '')).toContain('action_gate')
    }
  } finally {
    await cleanupBatch(tk, bid)
  }
})

// ---------------------------------------------------------------------------
// 场景 4:checkpoint(after) 非阻断追加
// ---------------------------------------------------------------------------

test('after 非阻断:写完追加检查提示词→模型复读补正→终态 passed',
     async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 1 个真实子会话
  const tk = await adminTokenCached()
  const name = tag('gate-cp-after')
  const MARK = 'REPORT-COMPLETE-MARK-B4'   // 只在注入提示词里揭示,首写必缺
  const staged = await uploadStaging(tk, 'in.txt', KB_INPUT, `e2e-cp4-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '阅读 uploads/in.txt,用 write 工具把其中的三条要点写入 outputs/report.md。',
    files: [staged],
    action_checks: [{
      name: '写后自查·补全标记', check_type: 'tool', mode: 'checkpoint',
      tool: 'write', args_pattern: 'report\\.md',
      timing: 'after', max_checks: 1,
      prompt: '[E2E-GATE-MARK-B4] 你刚刚完成了一次对 outputs/report.md 的写入。'
        + `请立即用 read 工具重新读取 outputs/report.md,核对其中是否包含字符串 ${MARK};`
        + `若缺失,必须立即用 write 工具把 ${MARK} 补写进该文件并重新保存,然后才能结束任务。`,
      assert_spec: { tool: 'read', args_pattern: 'report\\.md', min_count: 1 },
    }],
  })
  const bid = created.batchId || created.batch?.id
  try {
    const final = await waitBatchTerminal(tk, bid, 600_000, 5000)
    expect(final.batch.status).toBe('completed')
    const child = final.sessions[0]
    expect(child.status).toBe('completed')
    expect(child.gate_passed, '复读自查应作为 assert 证据 passed').toBeGreaterThanOrEqual(1)

    // 注入发生(append_result 形态):消息导出里 write 工具结果尾部带检查提示词
    const ms = await (await fetch(`${API}/ai/chat/sessions/${child.id}/messages`,
      { headers: authHeaders(tk) })).json()
    expect(_json(ms), '消息导出应包含 after 注入的检查提示词标记')
      .toContain('E2E-GATE-MARK-B4')

    // 模型补正:工作区文件最终包含注入要求补写的标记(删批前读取)
    const ws = childWorkspace(final)
    expect(readWorkspaceFile(ws, 'outputs/report.md'),
      '模型应按注入提示词补写标记').toContain(MARK)
  } finally {
    await cleanupBatch(tk, bid)
  }
})

// ---------------------------------------------------------------------------
// 场景 5:策略库复用(建策略 → 两批引用 → 解绑 → 新派发不登记)
// ---------------------------------------------------------------------------

test('策略复用:两批引用登记期望行;解绑后 append 新派发不再登记',
     async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 3 个轻会话(直接回复 OK)
  const tk = await adminTokenCached()
  const pname = `AITEST 策略复用 ${Date.now()}`
  const pr = await fetch(`${API}/ai/chat/gate-policies`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({
      name: pname,
      description: 'Task 13 E2E 策略复用场景',
      // 无 assert_spec:轻会话不写报告,checkpoint 行不参与终态核对(不失败)
      checks: [{
        name: '策略写入核对', check_type: 'tool', mode: 'checkpoint',
        tool: 'write', args_pattern: 'report\\.md', timing: 'before',
        max_checks: 2,
        prompt: '写报告前必须先读取 docs/kb/summary.md 并核对要点。',
      }],
    }),
  })
  expect(pr.status).toBe(201)
  const pol = await pr.json()

  const mkBatch = async (k: string) => {
    const staged = await uploadStaging(tk, 'in.txt', 'ok', `e2e-pol-${k}-${Date.now()}`)
    const d = await createBatch(tk, {
      name: tag(`gate-pol-${k}`),
      prompt: '直接回复:OK。不要读取任何文件,不要执行任何命令,不要写任何文件。',
      files: [staged],
      gatePolicyId: pol.id,
    })
    return d.batchId || d.batch?.id
  }

  let bidA: string | null = null
  let bidB: string | null = null
  try {
    bidA = await mkBatch('a')
    bidB = await mkBatch('b')
    const fA = await waitBatchTerminal(tk, bidA, 480_000)
    const fB = await waitBatchTerminal(tk, bidB, 480_000)
    expect(fA.sessions[0].status).toBe('completed')
    expect(fB.sessions[0].status).toBe('completed')

    // 两批引用同一策略 → 各自子任务登记策略侧期望行(期望行即快照)
    for (const [k, d] of [['A', fA], ['B', fB]] as const) {
      const sid = d.sessions[0].id
      const rows = dbSeed(`SELECT name, mode, timing, max_checks, source
        FROM action_expectations WHERE scope_id = '${sid}'`)
      expect(rows.length, `批${k}子任务应登记策略期望行`).toBe(1)
      expect(rows[0][0]).toBe('策略写入核对')
      expect(rows[0][1]).toBe('checkpoint')
      expect(rows[0][2]).toBe('before')
      expect(Number(rows[0][3])).toBe(2)
    }

    // 解绑(哨兵 null;终态子任务历史行按设计保留)→ append 新派发:
    // 派发时合并集已无策略 → 新子任务不登记
    const un = await fetch(`${API}/ai/chat/batches/${bidB}`, {
      method: 'PATCH', headers: authHeaders(tk),
      body: JSON.stringify({ gatePolicyId: null }),
    })
    expect(un.status, await un.text()).toBeLessThan(300)
    const up = await uploadStaging(tk, 'extra.txt', '追加输入\n', `e2e-pol-c-${Date.now()}`)
    const ap = await fetch(`${API}/ai/chat/batches/${bidB}/append`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ files: [up] }),
    })
    expect(ap.status, await ap.text()).toBeLessThan(300)
    const fB2 = await waitBatchTerminal(tk, bidB, 480_000)
    expect(fB2.sessions.length, 'append 后应有 2 个子任务').toBe(2)
    const newChild = fB2.sessions.find((s: any) => s.id !== fB.sessions[0].id)
    expect(newChild.status).toBe('completed')
    const newRows = dbSeed(
      `SELECT count(*) FROM action_expectations WHERE scope_id = '${newChild.id}'`)
    expect(Number(newRows[0][0]), '解绑后新派发的子任务不应登记策略期望').toBe(0)
    const oldRows = dbSeed(
      `SELECT count(*) FROM action_expectations WHERE scope_id = '${fB.sessions[0].id}'`)
    expect(Number(oldRows[0][0]), '旧子任务的历史期望行保留(审计不重写)').toBe(1)
  } finally {
    for (const b of [bidA, bidB]) if (b) await cleanupBatch(tk, b)
    // 策略清理:两批均已终态,DELETE 放行(非终态引用才 409)
    await fetch(`${API}/ai/chat/gate-policies/${pol.id}`, {
      method: 'DELETE', headers: { Authorization: `Bearer ${tk}` },
    })
  }
})

// ---------------------------------------------------------------------------
// 场景 6:agent 定向(E2E 暂缓,单测覆盖)
// ---------------------------------------------------------------------------

test('agent 定向:checkpoint subagents 名单(unit 层已覆盖,E2E 暂缓)', async () => {
  // 设计 §11 场景 6:checkpoint 带 subagents 名单——名单内子代理动作被拦/
  // 计入 assert,名单外子代理与根会话放行。真实链路里委派目标由模型自选
  // subagent_type,名单内子代理是否被委派并触发目标工具不可控(需预置仓库
  // +多轮委派编排,单场景 LLM 成本高且易 flake);注入与终态断言的定向口径
  // 已由 server/tests 单测覆盖(test_agent_ledger:checkpoint subagents 定向
  // 注入/名单外放行不耗预算/终态断言只认名单内动作)。
  // Task 13 务实取舍:E2E 跳过,unit 层兜底。
  test.skip(true, '定向场景的子代理命名在真实链路不可控,单测已覆盖(test_agent_ledger)')
})
