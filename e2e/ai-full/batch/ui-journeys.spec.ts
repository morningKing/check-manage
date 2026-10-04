/**
 * AI 批任务 UI 旅程域 spec（2026-10-04 从五个根目录 spec 收编 + SSE 补缺）。
 *
 * 收编映射（断言语义与源 spec 一致，机制换本目录 helpers）：
 *  - 用例 1「对话框建批端到端」← e2e/ai-chat-batch.spec.ts（已删）：
 *    对话框建批→批组展开→终态徽标→UI 删除。对话框流程换
 *    ui-helpers.openBatchDialog/createBatchViaDialog；标题删掉源里的
 *    「retry」幻影（用例从未重试）。
 *  - 用例 2「表单校验阻断」← e2e/ai-full/agent-action-gate.spec.ts 用例 1
 *    （门禁配置区块 + 不完整期望阻断提交，纯 UI 0 LLM）。选择器原样搬运；
 *    gate.spec.ts 已收编该文件的 API 域用例，本文件只复制这一例
 *    （agent-action-gate.spec.ts 的处置在 Task 14，不在本任务）。
 *  - 用例 3「批组按钮面」（新组装）：sleepBatch 驱动 running→paused→
 *    cancelled 态，断言暂停/中断/继续按钮显隐与生效；重试按钮
 *    （batch.failed 门控）在中断前不出现、中断后出现（cancelled 计入
 *    failed 聚合，批次无 cancelled 终态徽标）。
 *  - 用例 4「批内搜索」← e2e/ai-chat-batch-search.spec.ts（已删）：
 *    **自建批**（文件名带唯一关键词）消除源的 test.skip 环境耦合；断言
 *    范围标签/placeholder 切换、清除范围、命中批任务标注、空态文案、
 *    命中跳转打开子会话（selectSearchHit → batch-bar）。
 *  - 用例 5「技能注入」← e2e/batch-skill-check.spec.ts（已删）：
 *    改为 **provision_repo 自带 skill/**（makeProvisionRepo skills 形参），
 *    消除源对磁盘全局技能预置（trace-analyzer/writing-beats）的环境依赖。
 *    断言迁移见用例内注释——核心变化：源断言的「已注入全局技能」是
 *    全局技能路径的通知（batch_engine._inject_global_skills），provision
 *    路径注入的是**项目级技能**（整仓 clone 成 <ws>/.opencode/，成功
 *    静默、失败才有「预置仓库克隆失败」通知），故改为断言：
 *    ① SKILL.md 落盘工作区（确定性）② 助手答案列出该技能（模型可见）
 *    ③ 无克隆失败提示。
 *  - 用例 6「委派轨迹气泡」← e2e/ai-chat-subtask-trace.spec.ts 用例 3
 *    （批子会话委派，SubtaskBubble 全轨迹）。建批换 createBatchViaDialog，
 *    断言原样。
 *  - 用例 7/8「停止/暂停续跑消息保留」← e2e/ai-chat-stop-resume.spec.ts
 *    用例 1/2（已改名 ai-chat-session-control.spec.ts）：核心断言 =
 *    停止/暂停前的消息 id 续跑后原样保留 + 新增内容（在原工作上继续）。
 *    建批流程换 createBatchViaDialog。
 *  - 用例 9「SSE 实时消费」（新增，确定性）：API cancel → 列表级 SSE
 *    推送（store.subscribeListEvents，3s 防抖 fetchList）让徽标 9s 内落
 *    终态（<10s 列表轮询周期，LIST_POLL_MS——判据成立），先于轮询更新。
 *
 * LLM 预算标记（同 gate/lifecycle spec 约定，用例内 testInfo.annotations）：
 * - 用例 1/6/7/8：@llm（真 LLM，1-2 个子会话/例）；
 * - 用例 5：@llm（真 LLM 列技能清单）；
 * - 用例 3/4/9：@llm-light（sleepBatch/廉价 prompt，1 次轻量消耗/例）；
 * - 用例 2：0 LLM（纯 UI，提交被门禁校验阻断，不建批）。
 *
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）；UI 断言走本目录
 * ui-helpers（openBatchDialog/createBatchViaDialog/expandBatchGroup/
 * batchGroupBadgeClass，选择器已对线上 UI 验证）。
 */
import { test, expect, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import {
  API, authHeaders, adminToken, cleanupBatch, createBatch, getDetail,
  makeProvisionRepo, uploadStaging, waitBatchTerminal,
} from './batch-helpers'
import { adminTokenCached, sleepBatch, tag } from './toolbox'
import { gotoWithAuth, screenshot } from '../helpers'
import {
  batchGroupBadgeClass, createBatchViaDialog, expandBatchGroup, openBatchDialog,
} from './ui-helpers'

test.setTimeout(900_000)

/** 真登录态进 /ai-chat；等真实元素，绝不等 networkidle（SSE 常驻）。
 * vite 依赖重优化会触发整页重载，重进一次（选择器原样搬运自
 * agent-action-gate.spec.ts 的 gotoChat，已验证）。 */
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

/** 按批名从列表 API 找批 id；找不到返回 null。 */
async function findBatchIdByName(tk: string, name: string): Promise<string | null> {
  const r = await fetch(`${API}/ai/chat/batches?page=1&pageSize=50`, {
    headers: authHeaders(tk),
  })
  const list = await r.json()
  const found = (list.items as Array<{ id: string; name: string }>).find(b => b.name === name)
  return found?.id ?? null
}

/** 对话框建的批拿不到 id —— 失败路径兜底：按名查 id 后 stop=1 删除。 */
async function cleanupBatchByName(tk: string, name: string): Promise<void> {
  const bid = await findBatchIdByName(tk, name)
  if (bid) await cleanupBatch(tk, bid)
}

/** 子会话消息（内部端点直连 3002），返回原始 messages 数组。 */
async function childMessages(tk: string, cid: string): Promise<any[]> {
  const r = await fetch(`${API}/ai/chat/sessions/${cid}/messages`, {
    headers: authHeaders(tk),
  })
  expect(r.status, `GET messages ${cid} -> ${r.status}`).toBe(200)
  return (await r.json()).messages ?? []
}

/** 消息 id 列表（续跑保留断言用）。 */
function msgIds(messages: any[]): string[] {
  return messages.map(m => m.id)
}

// ---------------------------------------------------------------------------
// 用例 1：对话框建批端到端（← ai-chat-batch.spec.ts，标题去 retry 幻影）
// ---------------------------------------------------------------------------

test('对话框建批端到端：新建→分组展开→终态徽标→UI 删除', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 2 个真 LLM 子会话（prompt: echo hi）
  test.setTimeout(360_000)
  const tk = await adminTokenCached()
  const name = tag('dialog-e2e')
  await gotoChat(page)

  await openBatchDialog(page)
  await createBatchViaDialog(page, {
    name,
    prompt: 'echo hi',
    files: [
      { name: 'a.txt', content: 'A' },
      { name: 'b.txt', content: 'B' },
    ],
  })

  try {
    // 批组出现并展开：一个输入文件一个子会话（expandBatchGroup 自带 15s
    // 组可见等待 + 5×500ms 展开重试）
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.bg-child')).toHaveCount(2, { timeout: 10_000 })

    // 等批次收敛终态徽标（真 LLM，240s 预算，与源 waitForFunction 同判定）
    await page.waitForFunction((n) => {
      const groups = Array.from(document.querySelectorAll('.batch-group'))
      const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(n))
      const badge = g?.querySelector('.badge')
      return !!badge && ['completed', 'failed', 'partial'].some(
        s => badge.classList.contains(`badge--${s}`))
    }, name, { timeout: 240_000 })

    // UI 删除批次（确认框）→ 批组从侧栏消失
    await group.locator('[title="删除批次"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(page.locator('.batch-group', { hasText: name }))
      .toHaveCount(0, { timeout: 5_000 })
  } finally {
    await cleanupBatchByName(tk, name)   // UI 已删则查不到 id，兜底失败路径
  }
})

// ---------------------------------------------------------------------------
// 用例 2：表单校验阻断（← agent-action-gate.spec.ts 用例 1，选择器原样搬运，
// 0 LLM；该源文件的处置在 Task 14）
// ---------------------------------------------------------------------------

test('表单校验阻断：门禁区块不完整期望阻断提交（0 LLM）', async ({ page }) => {
  // 0 LLM：提交被门禁校验阻断，不建批、不产生子会话（无 llm 标注）
  const name = tag('gate-ui')
  await gotoChat(page)
  await openBatchDialog(page)
  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.locator('input[data-test="name"]').fill(name)
  await dialog.locator('textarea[data-test="prompt"]').fill('只回复:OK')
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('A') },
  ])
  await expect(dialog.locator('.files')).toContainText('a.txt', { timeout: 8_000 })

  // 门禁区块：启用后出现 AI 提炼按钮；加一行期望（data-test 在 label 上,
  // 原生 input 被 el-checkbox 隐藏,要点可见的样式块）——选择器原样搬运
  await dialog.locator('label[data-test="gate-enabled"] .el-checkbox__inner').click()
  await expect(dialog.locator('[data-test="gate-extract"]')).toBeVisible()
  await dialog.locator('[data-test="gate-add"]').click()
  await expect(dialog.locator('.gate-card')).toHaveCount(1)
  // 子代理定向输入可见可填（页面上直接指定该期望只对哪些子代理生效）
  const subInput = dialog.locator('.gate-card input[placeholder*="适用子代理"]')
  await expect(subInput).toBeVisible()
  await subInput.fill('general, explore')

  // 不完整的期望（空名称/空正则）必须阻断提交,且不产生批任务
  await dialog.locator('button[data-test="create-btn"]').click()
  await expect(page.locator('.el-message').first())
    .toContainText('动作门禁', { timeout: 5_000 })
  await expect(dialog).toBeVisible()
  await expect(page.locator('.batch-group', { hasText: name }))
    .toHaveCount(0)
})

// ---------------------------------------------------------------------------
// 用例 3：批组按钮面（新组装；sleepBatch 驱动 paused/cancelled 态）
// ---------------------------------------------------------------------------

test('批组按钮面：暂停/中断/继续显隐与生效，重试按钮不被误展示', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })   // sleepBatch：1 次 bash sleep 轻量消耗
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })
  const name = (await getDetail(tk, bid)).batch.name
  try {
    await gotoChat(page)
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()

    // 等子任务真正开跑（running 黄点）——暂停走「运行中协作式暂停」路径
    await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 90_000 })
    // 无失败计数 → 重试按钮（batch.failed 门控）不应出现
    await expect(group.locator('[title="重试失败"]')).toHaveCount(0)

    // --- 暂停全部（可继续）→ 生效：蓝点 + 已暂停徽标 + 继续入口出现 ---
    await group.locator('[title^="暂停全部"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(group.locator('.dot--running')).toHaveCount(0, { timeout: 60_000 })
    await expect(group.locator('.dot--paused').first()).toBeVisible({ timeout: 60_000 })
    await expect(group.locator('.badge--paused')).toBeVisible({ timeout: 30_000 })
    await expect(group.locator('[title^="继续运行"]')).toBeVisible({ timeout: 30_000 })
    // 后端视角：paused 计数 > 0 且不占 failed
    const pausedDetail = await getDetail(tk, bid)
    expect(pausedDetail.batch.paused).toBeGreaterThan(0)
    expect(pausedDetail.batch.failed).toBe(0)

    // --- 继续运行 → 生效：批次回到待运行/运行中，继续/重试入口收起 ---
    await group.locator('[title^="继续运行"]').click()
    await expect(group.locator('[title^="继续运行"]')).toHaveCount(0, { timeout: 60_000 })
    await expect(group.locator('.badge--paused')).toHaveCount(0, { timeout: 60_000 })

    // --- 中断全部（标记取消，之后可继续）→ 生效：子任务 cancelled，继续
    // 入口仍在（中断不是终局）。批次徽标没有 cancelled 终态——cancelled
    // 计入 failed 聚合（batch_repo.recompute_batch_status_tx），徽标落
    // badge--failed，同时点亮重试按钮（batch.failed 门控）
    await group.locator('[title^="中断全部"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(group.locator('.dot--cancelled').first()).toBeVisible({ timeout: 60_000 })
    await expect(group.locator('[title^="继续运行"]')).toBeVisible({ timeout: 30_000 })
    const cancelledDetail = await getDetail(tk, bid)
    expect(cancelledDetail.batch.cancelled).toBeGreaterThan(0)
    expect(await batchGroupBadgeClass(page, name)).toBe('badge--failed')
    // 重试按钮显隐的正向半边：失败计数 > 0 → 重试入口出现（全程唯一出现时机）
    await expect(group.locator('[title="重试失败"]')).toBeVisible({ timeout: 60_000 })
    await screenshot(page, 'ui-journeys-batch-buttons')
  } finally {
    await cleanupBatch(tk, bid)   // 非终态（cancelled）兜底 stop=1 删除
  }
})

// ---------------------------------------------------------------------------
// 用例 4：批内搜索（← ai-chat-batch-search.spec.ts；自建批消除环境耦合）
// ---------------------------------------------------------------------------

test('批内搜索：范围标签切换/命中标注/空态文案/命中跳转打开子会话', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })   // 廉价 prompt（只回复:OK）
  const tk = await adminTokenCached()
  const name = tag('search')
  // 文件名（即子会话标题/输入文件名）带唯一关键词 → 标题命中是确定性的
  const KEY = `月光检索词${Date.now()}`
  await gotoChat(page)
  await openBatchDialog(page)
  await createBatchViaDialog(page, {
    name,
    prompt: '只回复:OK，不要做别的。',
    files: [{ name: `${KEY}-报告.txt`, content: '批内搜索目标内容' }],
  })
  try {
    const group = page.locator('.batch-group', { hasText: name }).first()
    await group.waitFor({ state: 'visible', timeout: 15_000 })

    // 1) 点击「搜索本批任务」→ 范围标签出现 + placeholder 切换（源断言原样）
    await group.locator('[title="搜索本批任务"]').click()
    const scope = page.locator('.ai-sidebar__search-scope')
    await expect(scope).toContainText(name, { timeout: 5_000 })
    await expect(page.locator('.ai-sidebar__search input')).toHaveAttribute(
      'placeholder', new RegExp(name), { timeout: 5_000 })

    // 2) 清除范围 → 恢复普通会话搜索
    await scope.locator('.ai-sidebar__search-scope-x').click()
    await expect(page.locator('.ai-sidebar__search-scope')).toHaveCount(0)

    // 3) 重新进入范围搜索：唯一关键词 → 命中子会话必带「批任务：」标注
    await group.locator('[title="搜索本批任务"]').click()
    await page.locator('.ai-sidebar__search input').fill(KEY)
    const hits = page.locator('.session-item--hit')
    await expect(hits.first()).toBeVisible({ timeout: 10_000 })   // 300ms 防抖 + 请求
    const n = await hits.count()
    for (let i = 0; i < n; i++) {
      await expect(hits.nth(i).locator('.session-item__batchline').first())
        .toContainText('批任务：')
    }

    // 4) 空态文案区分范围模式（不可能命中的词）
    await page.locator('.ai-sidebar__search input').fill(`绝无命中词${KEY}XYZ`)
    await expect(page.getByText('本批任务中未找到匹配子会话'))
      .toBeVisible({ timeout: 10_000 })

    // 5) 命中跳转：点命中行 → selectSearchHit 展开批分组并打开子会话
    //    （主面板批任务栏出现「批任务 ID」标识；同栏还有「子任务 ID」span，
    //    同 class .batch-bar__id —— 按 title 过滤出批任务那一枚）
    await page.locator('.ai-sidebar__search input').fill(KEY)
    await expect(hits.first()).toBeVisible({ timeout: 10_000 })
    await hits.first().click()
    await expect(page.locator('.batch-bar__id[title="点击复制批任务 ID"]'))
      .toBeVisible({ timeout: 15_000 })
    await screenshot(page, 'ui-journeys-search-hit-open')
  } finally {
    await cleanupBatchByName(tk, name)
  }
})

// ---------------------------------------------------------------------------
// 用例 5：技能注入（← batch-skill-check.spec.ts；provision_repo 自带 skill/）
// ---------------------------------------------------------------------------

test('技能注入：预置仓库 skill/ 注入为项目技能并被模型列出', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 真 LLM 列技能清单（1 个子会话）
  test.setTimeout(600_000)
  const tk = await adminToken()
  const SKILL = 'e2e-prov-skill'
  const repo = makeProvisionRepo({}, {
    [SKILL]: '这是一个 E2E 验证用的项目级技能（预置仓库 skill/ 目录注入）。'
      + '当用户要求列出可用技能时，必须把本技能列入清单。',
  })
  const name = tag('skill-inject')
  let bid: string | null = null
  try {
    // provision_repo 仅 API 建批可带（对话框无该字段）——与源用对话框建批
    // 的差异点，见报告「技能注入改造」一节
    const file = await uploadStaging(tk, 'skill-probe.txt', 'probe', `sk-${Date.now()}`)
    const d = await createBatch(tk, {
      name,
      prompt: '请列出你当前环境中所有可用的 skill（技能），给出每个技能的名称和用途描述。'
        + '只输出技能清单，不要执行其他任务。',
      provision_repo: repo,
      files: [file],
    })
    bid = d.batch.id as string
    await waitBatchTerminal(tk, bid, 420_000)
    const detail = await getDetail(tk, bid)
    const s = detail.sessions[0]

    // 断言 1（确定性）：provision 注入落盘——预置仓库整仓 clone 成
    // <ws>/.opencode/，skill/<name>/SKILL.md 应存在于子会话工作区
    const skillMd = path.join(s.workspace_path, '.opencode', 'skill', SKILL, 'SKILL.md')
    expect(fs.existsSync(skillMd), `预置技能应落盘工作区: ${skillMd}`).toBe(true)

    // 断言 2：注入提示不含克隆失败（provision 成功是静默的，失败才有通知；
    // 与源「无预置仓库时不应出现克隆失败提示」断言同语义，且此处真带了仓库）
    const msgs = await childMessages(tk, s.id)
    const raw = JSON.stringify(msgs)
    expect(raw, '预置仓库克隆失败提示不应出现').not.toContain('预置仓库克隆失败')

    // 断言 3（模型可见）：助手回答列出该技能。
    // 【断言迁移说明】源 spec 断言系统通知「已注入全局技能」含
    // trace-analyzer/writing-beats——那是全局技能路径
    // （batch_engine._inject_global_skills，读中心存储 global-skills/）的
    // 通知，依赖环境磁盘预置。provision 路径注入的是**项目级技能**
    // （OpenCode 从 <ws>/.opencode/skill/ 发现），成功无任何通知——
    // 项目技能注入的可观测标记 = ①上落盘断言 + ②模型把它列进清单。
    const assistantText = msgs
      .filter((m: any) => m.role === 'assistant')
      .map((m: any) => JSON.stringify(m.content ?? m))
      .join('\n')
    expect(assistantText, `助手回答应列出预置技能 ${SKILL}`).toContain(SKILL)

    // UI 旅程：展开批组 → 打开子会话 → 对话流渲染出该技能
    await gotoChat(page)
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.bg-child')).toHaveCount(1, { timeout: 10_000 })
    await group.locator('.bg-child').first().click()
    await expect(page.locator('.msg').first()).toBeVisible({ timeout: 20_000 })
    await page.waitForTimeout(2_000)   // 留一次 2.5s 轮询兜底刷新（源同款）
    const pageConvo = (await page.locator('.msg').allInnerTexts()).join('\n')
    expect(pageConvo, '子会话对话流应渲染出预置技能名').toContain(SKILL)
    await screenshot(page, 'ui-journeys-skill-inject')
  } finally {
    if (bid) await cleanupBatch(tk, bid)
    fs.rmSync(repo, { recursive: true, force: true })
  }
})

// ---------------------------------------------------------------------------
// 用例 6：委派轨迹气泡（← ai-chat-subtask-trace.spec.ts 用例 3，断言原样）
// ---------------------------------------------------------------------------

test('批子会话委派：SubtaskBubble 渲染 general 代理并可展开全轨迹', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 真委托（父+子两次模型调用）
  test.setTimeout(420_000)
  const tk = await adminTokenCached()
  const name = tag('batch-subtask')
  await gotoChat(page)

  await openBatchDialog(page)
  await createBatchViaDialog(page, {
    name,
    prompt: '你必须使用 task 工具委托一个 general 子代理去完成：统计当前工作区 AGENTS.md 文件的行数。'
      + '不要自己读取或统计，必须由子代理执行并返回结果。',
    files: [{ name: 'subtask-probe.txt', content: 'probe' }],
  })

  try {
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.bg-child')).toHaveCount(1, { timeout: 10_000 })
    await group.locator('.bg-child').first().click()

    const bubble = page.locator('.subtask-bubble').first()
    await bubble.waitFor({ state: 'visible', timeout: 120_000 })
    await expect(bubble.locator('.subtask-bubble__agent')).toContainText('general')
    // 注：__task-id/__copy 是 feat/batch-session-parity 分支的 UI，main 上没有
    await expect(page.locator('.subtask-bubble--completed').first())
      .toBeVisible({ timeout: 120_000 })

    await bubble.locator('.subtask-bubble__head').click()
    const body = bubble.locator('.subtask-bubble__body')
    await expect(body).toBeVisible({ timeout: 10_000 })
    // 会话复用引入任务段边界：段首用户消息标注「本段任务输入」，段内为「委托输入」
    // （SubtaskBubble boundaryOf）——两种都是合法的用户输入标注
    await expect(body.locator('.subtask-bubble__role').first())
      .toContainText(/委托输入|本段任务输入/, { timeout: 60_000 })
    await expect(body.locator('.subtask-bubble__msg').first()).toBeVisible({ timeout: 60_000 })
    await screenshot(page, 'ui-journeys-batch-subtask-trace')
  } finally {
    await cleanupBatchByName(tk, name)
  }
})

// ---------------------------------------------------------------------------
// 用例 7：停止（中断）续跑消息保留（← ai-chat-stop-resume.spec.ts 用例 1）
// ---------------------------------------------------------------------------

test('停止续跑保留消息：中断全部 → 继续运行在原工作上继续', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 2 个真 LLM 子会话 + 续跑
  test.setTimeout(420_000)
  const tk = await adminTokenCached()
  const name = tag('stop-resume')
  await gotoChat(page)

  // 慢 prompt（≥300 字散文）留出停止窗口（源同款）
  await openBatchDialog(page)
  await createBatchViaDialog(page, {
    name,
    prompt: '请围绕「春天的田野」写一段不少于300字的中文散文，只输出散文正文。',
    files: [
      { name: 'a.txt', content: 'A' },
      { name: 'b.txt', content: 'B' },
    ],
  })
  try {
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.bg-child')).toHaveCount(2, { timeout: 10_000 })

    // 等一个子任务真正开跑再停——保证至少一个子任务停止前已有工作进度，
    // 继续时走「原 OpenCode 会话续跑」路径
    let sawRunning = false
    try {
      await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 60_000 })
      sawRunning = true
    } catch { /* 子任务极快完成时停止流程照常验证 */ }

    // 中断全部 → cancelled 蓝灰点 + 继续入口
    await group.locator('[title^="中断全部"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(group.locator('[title^="继续运行"]')).toBeVisible({ timeout: 60_000 })
    await expect(group.locator('.dot--running')).toHaveCount(0, { timeout: 60_000 })
    await expect(group.locator('.dot--cancelled').first()).toBeVisible({ timeout: 30_000 })

    // 后端视角：cancelled 计数 > 0（继续按钮的判据）
    const bid = await findBatchIdByName(tk, name)
    expect(bid, '批任务应存在').toBeTruthy()
    const detail = await getDetail(tk, bid!)
    expect(detail.batch.cancelled).toBeGreaterThan(0)
    const childIds: string[] = detail.sessions.map((s: { id: string }) => s.id)

    // 续跑前记录已有消息 id —— 续跑后这些行必须原样保留（历史未清）
    const preIds: Record<string, string[]> = {}
    for (const cid of childIds) {
      preIds[cid] = msgIds(await childMessages(tk, cid))
    }

    // 继续运行 → 终态徽标
    await group.locator('[title^="继续运行"]').click()
    await page.waitForFunction((n) => {
      const groups = Array.from(document.querySelectorAll('.batch-group'))
      const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(n))
      const badge = g?.querySelector('.badge')
      return !!badge && ['badge--completed', 'badge--partial'].some(
        c => badge.classList.contains(c))
    }, name, { timeout: 360_000 })
    await expect(group.locator('.dot--completed')).toHaveCount(2, { timeout: 60_000 })

    // 停止前已开跑的子任务：续跑后旧消息仍在、且新增了内容——证明是在
    // 原工作上继续，而不是清空重跑（重新执行会先删光消息）
    if (sawRunning) {
      let verified = false
      for (const cid of childIds) {
        if (!preIds[cid].length) continue
        const nowIds = msgIds(await childMessages(tk, cid))
        if (preIds[cid].every(id => nowIds.includes(id)) && nowIds.length > preIds[cid].length) {
          verified = true
          break
        }
      }
      expect(verified,
        '续跑应在保留停止前消息历史的基础上新增内容（原工作继续）').toBeTruthy()
    }

    // UI 删除批次
    await group.locator('[title="删除批次"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(page.locator('.batch-group', { hasText: name }))
      .toHaveCount(0, { timeout: 10_000 })
  } finally {
    await cleanupBatchByName(tk, name)
  }
})

// ---------------------------------------------------------------------------
// 用例 8：暂停续跑消息保留（← ai-chat-stop-resume.spec.ts 用例 2）
// ---------------------------------------------------------------------------

test('暂停续跑保留消息：暂停全部 → 继续运行保留暂停前历史（paused 不占失败）', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 2 个真 LLM 子会话 + 续跑
  test.setTimeout(420_000)
  const tk = await adminTokenCached()
  const name = tag('pause-resume')
  await gotoChat(page)

  await openBatchDialog(page)
  await createBatchViaDialog(page, {
    name,
    prompt: '请围绕「秋天的山谷」写一段不少于300字的中文散文，只输出散文正文。',
    files: [
      { name: 'a.txt', content: 'A' },
      { name: 'b.txt', content: 'B' },
    ],
  })
  try {
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    await expect(group.locator('.bg-child')).toHaveCount(2, { timeout: 10_000 })

    // 等子任务真正开跑再暂停（走「运行中协作式暂停」路径）
    await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 60_000 })

    await group.locator('[title^="暂停全部"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    // worker 把运行中的回合 abort 并落成 paused（蓝点），批次徽标变「已暂停」
    await expect(group.locator('.dot--running')).toHaveCount(0, { timeout: 60_000 })
    await expect(group.locator('.dot--paused').first()).toBeVisible({ timeout: 60_000 })
    await expect(group.locator('.badge--paused')).toBeVisible({ timeout: 30_000 })

    // 后端视角：paused 计数 > 0，且暂停不占 failed 计数
    const bid = await findBatchIdByName(tk, name)
    expect(bid, '批任务应存在').toBeTruthy()
    const detail = await getDetail(tk, bid!)
    expect(detail.batch.paused).toBeGreaterThan(0)
    expect(detail.batch.failed).toBe(0)
    const childIds: string[] = detail.sessions.map((s: { id: string }) => s.id)

    // 暂停期间已有部分消息历史（被中断的回合），记录下来用于续跑对比
    const preIds: Record<string, string[]> = {}
    for (const cid of childIds) {
      preIds[cid] = msgIds(await childMessages(tk, cid))
    }

    // 继续运行 → 终态徽标
    await group.locator('[title^="继续运行"]').click()
    await page.waitForFunction((n) => {
      const groups = Array.from(document.querySelectorAll('.batch-group'))
      const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(n))
      const badge = g?.querySelector('.badge')
      return !!badge && ['badge--completed', 'badge--partial'].some(
        c => badge.classList.contains(c))
    }, name, { timeout: 360_000 })
    await expect(group.locator('.dot--completed')).toHaveCount(2, { timeout: 60_000 })
    const after = await getDetail(tk, bid!)
    expect(after.batch.failed).toBe(0)   // 全程没有失败计数

    // 续跑在原历史上进行：暂停前的消息行原样保留、其上新增内容
    let verified = false
    for (const cid of childIds) {
      if (!preIds[cid].length) continue
      const nowIds = msgIds(await childMessages(tk, cid))
      if (preIds[cid].every(id => nowIds.includes(id)) && nowIds.length > preIds[cid].length) {
        verified = true
        break
      }
    }
    expect(verified, '续跑应保留暂停前的消息历史并新增内容').toBeTruthy()

    // UI 删除批次
    await group.locator('[title="删除批次"]').click()
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await expect(page.locator('.batch-group', { hasText: name }))
      .toHaveCount(0, { timeout: 10_000 })
  } finally {
    await cleanupBatchByName(tk, name)
  }
})

// ---------------------------------------------------------------------------
// 用例 9：SSE 实时消费（新增，确定性）——列表级 SSE 先于 10s 轮询更新徽标
// ---------------------------------------------------------------------------

test('SSE 实时消费：API 触发子任务取消，UI 徽标先于 10s 轮询更新', async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'llm-light' })   // sleepBatch：1 次 bash sleep 轻量消耗
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })
  const name = (await getDetail(tk, bid)).batch.name
  try {
    await gotoChat(page)
    await expandBatchGroup(page, name)
    const group = page.locator('.batch-group', { hasText: name }).first()
    // 等子任务开跑（sleep 120s 的长窗口从这里起算）
    await group.locator('.dot--running').first().waitFor({ state: 'visible', timeout: 90_000 })

    // 列表级 SSE 订阅在 startListPolling 的首个 tick（10s）后才建立
    // （aiChatBatches.startListPolling：tick 内 fetchList → subscribeListEvents，
    // 挂载时只有 fetchList 没有 subscribe）——先喂满一个轮询周期，确保
    // cancel 发生时 SSE 订阅已带本批 id，9s 窗口量的是「SSE 推送→徽标」。
    await page.waitForTimeout(11_000)

    const t0 = Date.now()
    const res = await fetch(`${API}/ai/chat/batches/${bid}/cancel`, {
      method: 'POST', headers: authHeaders(tk),
    })
    expect(res.status, 'API cancel 应成功').toBeLessThan(300)
    // SSE push 到达 → 3s 防抖 fetchList → 徽标 9s 内落终态。批次无
    // cancelled 终态（cancelled 计入 failed 聚合，见用例 3 注），故按
    // .badge 元素的终态修饰类判定（组 className/文本不含类名，brief 草稿
    // 的 /cancelled|failed/ 对 g.className+textContent 永不匹配，已修正）。
    // 窗口 9s：链路含变量段（worker cancel 去注册 ~4.5s），给负载留余量，
    // 且仍严格小于 10s 列表轮询周期——「靠轮询必然 >10s」，SSE 判据不变。
    await page.waitForFunction((n) => {
      const g = [...document.querySelectorAll('.batch-group')]
        .find(el => el.querySelector('.bg-name')?.textContent?.includes(n))
      const badge = g?.querySelector('.badge')
      return !!badge && ['badge--cancelled', 'badge--failed', 'badge--partial'].some(
        c => badge.classList.contains(c))
    }, name, { timeout: 9_000 })
    console.log(`SSE 更新延迟 ${Date.now() - t0}ms`)
    await screenshot(page, 'ui-journeys-sse')
  } finally {
    await cleanupBatch(tk, bid)   // cancelled 非终态批次，stop=1 兜底删除
  }
})
