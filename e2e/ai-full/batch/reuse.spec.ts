/**
 * AI 批任务 reuse 域 spec（2026-10-04 从 ai-batch-concurrency-reuse.spec.ts +
 * ai-subagent-reuse.spec.ts 去重合并 —— 两源断言面 ~70% 重叠）。
 *
 * 覆盖（3 用例；用例 3 = 源 subagent-reuse 全部两个 test 的整体迁移）：
 *  用例 1 L1 并发观测（源 concurrency-reuse 的并发部分，去 provision/reuse 断言）：
 *    6 子 × 默认并发 3（MAX_CONCURRENT=3）→ 轮询观测 running 峰值===3 且
 *    running>0 时 pending 排队出现过 → 6/6 收敛（total/done=6、逐子 completed）。
 *  用例 2 L1 复用权威锚点（源 concurrency-reuse 的复用部分，权威端点版）：
 *    makeProvisionRepo 注入自定义 primary（e2e-primary：两次串行 task 委派
 *    e2e-sub，第 2 次 prompt 依赖第 1 次回答原文，杜绝并行委派——并行发起时
 *    第 2 次的注入查询会跑在第 1 次锚点登记之前，属设计内边界）+ 自定义
 *    subagent（e2e-sub：固定口径回答）→ subagent_reuse:['e2e-sub'] → 逐子断言：
 *     a. tool-calls 账本 ≥2 次 task 委派且 args 含 e2e-sub；
 *     b. 已部署插件内嵌 /reuse 内部端点锚点（enabled=true 且 taskId 为
 *        ses_ 前缀的 OpenCode 子会话）；
 *     c. 钉住子会话 turn_segments ≥2（user 消息含「报告一号」「二号确认：」
 *        ——第二次委派确实续跑了同一子会话）。
 *  用例 3 L3 UI 徽标（源 subagent-reuse 全部）：
 *    a. subagent_reuse:['general'] → 模型两次委派续跑同一子会话（气泡
 *       subtaskId 一致）+ 「已复用·2 段」徽标 + 展开分段头截图；模型偶发
 *       空回合/不委派（OC 刚重启后首回合异常）→ 自动清批重试兜底（源语义）。
 *    b. 创建对话框：reuse-select 勾 general 随创建保存（UI 链路）。
 *
 * 相对源 spec 的合并决策（断言零丢弃，逐条理由见 task-7 报告）：
 *  - 用例 1 的子任务长任务构造由「provision_repo 自定义 agent 双委派」改为
 *    toolbox.sleepBatch（sleep 45s 稳定 running 窗口）：并发槽观测不需要真实
 *    委派链路，45s 窗口 ≫ 2s 轮询间隔，杜绝 running 峰值漏采（源构造的窗口
 *    长度取决于 LLM 回合时长，是原 spec 最脆环节）；agent 省缺 = 默认主 Agent
 *    （general 是 subagent 不能作主 Agent，见 toolbox.sleepBatch 注释）。
 *  - 「6/6 收敛 + total/done 计数」归用例 1；用例 2 保留「逐子 completed」
 *    前置（账本/锚点/任务段断言在 failed 子任务上无意义）——两者观测对象
 *    不同（满载排队的收敛 vs 权威锚点的可断言前提），非重复断言面。
 *  - 用例 2 保留源 N=6（全量执行下的复用覆盖）；收敛等待改 waitBatchTerminal
 *    （抛错语义即可——本用例没有清批重试路径）。
 *  - UI 断言（用例 3）逐字保留源断言；登录改 gotoWithAuth（batch 域约定，
 *    注入共享 localStorage 登录态，替代源内联表单登录）；截图改
 *    helpers.screenshot（viewport 截图，先 scrollIntoViewIfNeeded 气泡），
 *    截图文件名沿用源 subagent-reuse-bubble。
 *  - 用例 3a 的收敛轮询保留源内联循环（不用 waitBatchTerminal）：源语义是
 *    「超时未收敛同样走清批重试」，waitBatchTerminal 的抛错会跳过重试。
 *  - 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 *
 * LLM 预算标记（同 lifecycle.spec.ts 约定，标注在头部）：
 * - 用例 1：@llm —— 6 个 sleep 子会话（各 1 次轻量 bash sleep 回合），~3 分钟；
 * - 用例 2：@llm —— 6 子 ×（primary 2 轮 + e2e-sub 2 轮），预算 ~15 分钟；
 * - 用例 3a：@llm —— 1 子 ×（general 委派 ×2 + 汇总），清批重试 ≤3 批，~15 分钟；
 * - 用例 3b：@llm-light —— 批建立后 15s 内 stop 删除，至多 1 次轻量轮。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  API, BATCH_TERMINAL, authHeaders, cleanupBatch, countByStatus, createBatch,
  getDetail, makeProvisionRepo, uploadStaging, waitBatchTerminal,
  type AgentDef,
} from './batch-helpers'
import { adminTokenCached, sleepBatch, tag } from './toolbox'
import { gotoWithAuth, screenshot } from '../helpers'
import { openBatchDialog } from './ui-helpers'

// 迁移两源 spec 的较大者（concurrency-reuse 1_200_000；subagent-reuse 900_000
// 被覆盖，其内部等待自带 420s deadline 语义不变）。对话框用例另行显式给时限。
test.setTimeout(1_200_000)

test('L1 并发观测：6 子任务 × 并发 3 槽满载 + pending 排队 + 6/6 收敛', async () => {
  const tk = await adminTokenCached()
  // sleep 45s 稳定 running 窗口：第 1 波 3 个子任务占满槽位 ~1 分钟，
  // 第 2 波 3 个排队——轮询间隔 2s 下 running 峰值/pending 排队必然可采
  const bid = await sleepBatch(tk, { children: 6, sleepSec: 45 })
  try {
    let maxRunning = 0
    let sawQueued = false
    let terminal: any = null
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await getDetail(tk, bid)
      const counts = countByStatus(dd)
      maxRunning = Math.max(maxRunning, counts.running || 0)
      if ((counts.running || 0) > 0 && (counts.pending || 0) > 0) sawQueued = true
      if (BATCH_TERMINAL.includes(dd.batch?.status)) { terminal = dd; break }
      await new Promise(rr => setTimeout(rr, 2000))
    }
    expect(terminal, '批次未在时限内收敛到终态').toBeTruthy()
    // 并发拉满：3 槽位全部占用过，且出现过排队中的 pending 子任务
    expect(maxRunning, `running 峰值 ${maxRunning}`).toBe(3)
    expect(sawQueued, '未观测到 pending 排队（槽位未打满即收敛？）').toBe(true)

    // 全量执行：6/6 completed，计数与批次状态正确
    expect(terminal.batch.total).toBe(6)
    expect(terminal.batch.done).toBe(6)
    for (const s of terminal.sessions) expect(s.status).toBe('completed')
  } finally {
    await cleanupBatch(tk, bid)   // 失败路径兜底清理（已删则 404 被忽略）
  }
})

const N = 6          // 子任务数：> MAX_CONCURRENT(3)，复用断言覆盖两波执行
const PRIMARY = 'e2e-primary'
const SUB = 'e2e-sub'

// 自定义 agent 定义（内容逐字迁移自源 spec 的 makeProvisionRepo；description
// 行由 helper 统一生成——description 是 OC agent 列表元数据，不进指令面）
const AGENTS: Record<string, AgentDef> = {
  [PRIMARY]: {
    mode: 'primary',
    body: `你是批任务子任务的主代理。严格按以下步骤执行，不要跳步、不要减少委派次数。
两次委派必须串行：第 2 步的 prompt 依赖第 1 步的回答，拿到第 1 步结果之前不得发起第 2 次委派，也不得把两次委派放在同一条消息里并行调用：
1. 用 task 工具委派 subagent（subagent_type 填 "${SUB}"），prompt 为「报告一号」，等待并记住其回答。
2. 用 task 工具再次委派 subagent（subagent_type 仍填 "${SUB}"），prompt 为「二号确认：<第 1 步回答原文>」（把第 1 步的回答原文原样拼接进去）。
3. 用 write 工具把两次回答原文各占一行写入 outputs/result.txt。
4. 最后回复「DONE」。`,
  },
  [SUB]: {
    mode: 'subagent',
    body: `你是子代理。直接用文本回答，不要调用任何工具。
收到「报告一号」回答「一号OK」；收到以「二号确认：」开头的输入回答「二号OK」；其他任何输入回答「SUB-OK」。`,
  },
}

test('L1 复用权威锚点：自定义 primary 双委派 e2e-sub + /reuse 内部端点 + 任务段 ≥2', async () => {
  const tk = await adminTokenCached()
  const HDRS = authHeaders(tk)
  const repo = makeProvisionRepo(AGENTS)
  let bid: string | null = null
  try {
    // 1) staging ×6 + 建批（自定义 agent + 预置仓库 + 子代理复用名单）
    const uploadSession = tag('upl')
    const files = []
    for (let i = 0; i < N; i++) {
      files.push(await uploadStaging(tk, `in-${i}.txt`, `child-${i}\n`, uploadSession))
    }
    const d = await createBatch(tk, {
      name: tag('reuse-anchor'),
      prompt: '按 agent 指令执行。',
      agent: PRIMARY,
      provision_repo: repo,
      subagent_reuse: [SUB],
      files,
    })
    bid = d.batch.id as string
    expect(d.sessions.length).toBe(N)

    // 2) 等全量收敛（并发槽观测断言归用例 1，这里只等终态）
    const terminal = await waitBatchTerminal(tk, bid, 900_000, 2000)
    // 前置：逐子 completed（下面的账本/锚点/任务段断言在 failed 子任务上无意义）
    for (const s of terminal.sessions) expect(s.status).toBe('completed')

    // 3) 内部端点地址/token 从已部署插件文件提取（与插件同源——插件能用的
    //    查询，测试就能用）
    const pluginSrc = fs.readFileSync(path.join(
      os.homedir(), '.config', 'opencode', 'plugin', 'baize-subagent-reuse.js'), 'utf-8')
    const endpoint =
      /ENDPOINT = process\.env\.BAIZE_SUBAGENT_REUSE_URL \|\| '([^']*)'/.exec(pluginSrc)?.[1]
    const itoken = /TOKEN = process\.env\.BAIZE_INTERNAL_TOKEN \|\| '([^']*)'/.exec(pluginSrc)?.[1]
    expect(endpoint, '插件文件未嵌入复用端点').toBeTruthy()
    expect(itoken, '插件文件未嵌入内部 token').toBeTruthy()

    // 4) 逐子任务：委派发生 + 复用锚点 + 同一子会话承载两段委派
    for (const s of terminal.sessions) {
      const sid = s.id as string
      // 4a) 账本：主代理确实委派了 ≥2 次，且都指向 e2e-sub
      const tc = await (await fetch(
        `${API}/ai/chat/batches/${bid}/children/${sid}/tool-calls`, { headers: HDRS })).json()
      const taskCalls = (tc.calls || []).filter((c: any) => c.tool === 'task')
      expect(taskCalls.length, `子任务 ${sid} 的委派次数 ${taskCalls.length} < 2`)
        .toBeGreaterThanOrEqual(2)
      for (const c of taskCalls) expect(c.args as string).toContain(SUB)

      // 4b) 复用锚点（权威状态）：after 回调已登记，taskId 为 OC 子会话 id
      const reuse = await (await fetch(
        `${endpoint}/reuse?session=${encodeURIComponent(s.opencode_session_id)}&agent=${SUB}`,
        { headers: { 'x-internal-token': itoken as string } })).json()
      expect(reuse.enabled, `子任务 ${sid} 复用未生效`).toBe(true)
      expect(String(reuse.taskId)).toMatch(/^ses_/)

      // 4c) 复用实锤：两次委派落在同一个子会话里（两条 user 消息 + 两个任务段）
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
    if (bid) await cleanupBatch(tk, bid)
    fs.rmSync(repo, { recursive: true, force: true })
  }
})

test('L3 UI 徽标：两次委派同会话 + 「已复用·2 段」徽标 + 分段头截图', async ({ page }) => {
  const tk = await adminTokenCached()
  const HDRS = authHeaders(tk)

  let subtaskIds: string[] = []
  let childId = ''
  let batchId = ''
  let segments: Array<{ ord: number; turn: number; label: string; firstMsgId: string }> = []

  try {
    for (let attempt = 0; attempt < 3 && subtaskIds.length < 2; attempt++) {
      // 上一批没有委派就清掉再试（模型偶发空回合/不委派 → 清批重试兜底）
      if (batchId) await cleanupBatch(tk, batchId)
      const staged = await uploadStaging(tk, 'input.txt', '两次委派任务的材料', tag('upl'))
      const detail = await createBatch(tk, {
        name: tag('sru'),
        prompt: '请分两次用 task 工具委派给 general 子代理执行（两次是连续的两轮任务）：'
              + '第一轮让 general 计算 37 乘 43 等于多少；'
              + '第二轮让 general 基于第一轮的得数再除以 7 等于多少，并给出最终整数结果。'
              + '两次委派完成后，汇报最终答案。',
        files: [staged],
        subagent_reuse: ['general'],
      })
      expect(detail.batch.subagent_reuse).toEqual(['general'])
      batchId = detail.batch.id as string
      childId = detail.sessions[0].id as string

      // 等批任务收敛（源内联轮询保留：超时未收敛同样走清批重试路径，
      // 不用 waitBatchTerminal 的抛错语义）
      const deadline = Date.now() + 420_000
      let status = ''
      while (Date.now() < deadline) {
        status = (await getDetail(tk, batchId)).batch?.status
        if (BATCH_TERMINAL.includes(status)) break
        await new Promise(rr => setTimeout(rr, 4000))
      }
      console.log(`[reuse] attempt ${attempt}: batch status=${status}`)

      // 从子会话消息提取 subtask_use 气泡
      const msgs = await (await fetch(
        `${API}/ai/chat/sessions/${childId}/messages`, { headers: HDRS })).json()
      subtaskIds = []
      for (const m of (msgs.messages ?? [])) {
        for (const p of (m.content ?? [])) {
          if (p.type === 'subtask_use' && p.subtaskId) subtaskIds.push(p.subtaskId)
        }
      }
      console.log(`[reuse] attempt ${attempt}: bubbles=${JSON.stringify([...new Set(subtaskIds)])}`)

      if (subtaskIds.length >= 2) {
        const reusedId = subtaskIds[0]
        const subRes = await fetch(
          `${API}/ai/chat/sessions/${childId}/subtasks/${reusedId}/messages`, { headers: HDRS })
        expect(subRes.status).toBe(200)
        segments = (await subRes.json()).subtask.segments ?? []
      }
    }

    // 核心断言：两次委派复用同一子会话 + 任务段 ≥2
    expect(subtaskIds.length, '模型应产生两次委派气泡').toBeGreaterThanOrEqual(2)
    expect(new Set(subtaskIds).size, '两次委派必须复用同一子会话').toBe(1)
    expect(segments.length, '复用会话应有 ≥2 个任务段').toBeGreaterThanOrEqual(2)
    expect(segments[0].label).toBeTruthy()

    // UI：打开子会话 → 气泡头 task_id 标识 + 复用徽标 → 展开看分段头
    await gotoWithAuth(page, `/ai-chat?session=${childId}`)
    const bubble = page.locator('.subtask-bubble').first()
    await bubble.waitFor({ state: 'visible', timeout: 30_000 })
    await expect(bubble.locator('.subtask-bubble__taskid')).toBeVisible()
    await expect(bubble.locator('.subtask-bubble__reuse')).toContainText('已复用·2 段')
    await bubble.click()   // 展开
    await expect(bubble.locator('.subtask-bubble__segment').first())
      .toBeVisible({ timeout: 15_000 })
    await expect(bubble.locator('.subtask-bubble__segment').nth(1)).toBeVisible()
    await bubble.scrollIntoViewIfNeeded()   // helpers.screenshot 是 viewport 截图
    await screenshot(page, 'subagent-reuse-bubble')
  } finally {
    if (batchId) await cleanupBatch(tk, batchId)
  }
})

test('创建对话框：子代理会话复用配置可保存（UI 链路）', async ({ page }) => {
  test.setTimeout(180_000)
  await gotoWithAuth(page, '/ai-chat')

  await openBatchDialog(page)
  const dialog = page.getByRole('dialog', { name: '新建批任务' })

  const name = tag('sru-ui')
  await dialog.locator('input[data-test="name"]').fill(name)
  await dialog.locator('textarea[data-test="prompt"]').fill('自验证：复用配置保存')

  // 多选下拉：展开 → 勾选 general
  const select = dialog.locator('[data-test="reuse-select"]')
  await select.click()
  const option = page.locator('.el-select-dropdown__item', { hasText: 'general' }).first()
  await option.waitFor({ state: 'visible', timeout: 10_000 })
  await option.click()
  await page.keyboard.press('Escape')

  // 需要至少一个文件（内部创建约束）
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })

  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  // API 断言：批次 subagent_reuse 已保存
  const tk = await adminTokenCached()
  let saved: string[] | null = null
  let hitId: string | null = null
  try {
    const deadline = Date.now() + 15_000
    while (Date.now() < deadline) {
      const listRes = await fetch(`${API}/ai/chat/batches?page=1&pageSize=5`, {
        headers: authHeaders(tk),
      })
      const hit = ((await listRes.json()).items ?? []).find((b: any) => b.name === name)
      if (hit) {
        hitId = hit.id as string
        saved = (await getDetail(tk, hitId)).batch.subagent_reuse ?? null
        break
      }
      await new Promise(rr => setTimeout(rr, 1000))
    }
    expect(saved, '复用配置应随创建保存').toEqual(['general'])
  } finally {
    if (hitId) await cleanupBatch(tk, hitId)   // 建立即删，至多 1 次轻量轮
  }
})
