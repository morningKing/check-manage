/**
 * 批子会话中途打开的 SSE 实时渲染（2026-10-08 subagent 委托区块不渲染修复）。
 *
 * 真实链路（真 LLM 批任务）：prompt 要求模型先输出一句话、再用 task 工具委托
 * 子代理做慢任务。用例在子会话 running 中途从 UI 打开——修复前该时点已错过
 * 本回合的 message.updated（OpenCode 事件不回放），part 事件全被前端门禁
 * 丢弃，委托气泡要等 session.idle 收敛后才可见；修复后订阅起点的快照爆发
 * 把进行中回合收编为流式目标，气泡在回合仍在跑时实时出现。
 *
 * 判别性断言：.subtask-bubble 出现的同一时刻，批组徽标仍是 badge--running
 * （若气泡只在终态后出现，即复现缺陷）。附带断言助手文本在 running 期间
 * 可见（token 级流式存活的证据）。
 */
import { test, expect } from '@playwright/test'
import {
  adminToken, createBatch, uploadStaging, getDetail, waitFor, cleanupBatch,
} from './batch-helpers'
import { gotoWithAuth, tag, SHOT_DIR } from '../helpers'
import { expandBatchGroup, batchGroupBadgeClass } from './ui-helpers'
import fs from 'node:fs'

test.setTimeout(300_000)

test('批子会话运行中打开：subagent 委托气泡实时渲染（不再等回合结束）', async ({ page }) => {
  const tk = await adminToken()
  const name = tag('midrun-sse')
  const uploadSessionId = `e2e-midrun-${Date.now()}`
  const staged = await uploadStaging(
    tk, 'task.txt', '子代理任务清单：从 1 数到 25。\n', uploadSessionId)
  const created = await createBatch(tk, {
    name,
    prompt: [
      '先只输出一行：「开始处理」。',
      '然后必须用 task 工具委托子代理（subagent_type=general-purpose）执行慢任务：',
      '读取 uploads/task.txt，从 1 数到 25，每行一个数字，数完才结束。',
      '不要自己直接完成计数。子代理结束后用一句话总结。',
    ].join('\n'),
    files: [staged],
  })
  const batchId = created.batchId || created.batch?.id
  expect(batchId).toBeTruthy()

  try {
    // ── UI：打开 /ai-chat，展开批组，等子会话出现并进入 running ──
    await gotoWithAuth(page, '/ai-chat')
    await expandBatchGroup(page, name)
    const child = page.locator('.batch-group', {
      has: page.locator('.bg-name', { hasText: name }),
    }).first().locator('.bg-child').first()
    await child.waitFor({ state: 'visible', timeout: 30_000 })
    // 等 worker 认领派发（pending → running），子会话状态点变 running
    await waitFor(async () =>
      (await child.locator('.dot').getAttribute('class'))?.includes('dot--running')
      || null, 60_000, '子会话进入 running')

    // 诊断：记录会话事件流的连接情况（EventSource 的 GET /events）
    page.on('request', (r) => {
      if (r.url().includes('/events')) console.log(`[sse] GET ${r.url().slice(-60)}`)
    })
    page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 300)))

    // 中途打开子会话（错过回合 message.updated 的缺陷场景）。点击目标固定用
    // 行首文件名 span：刚进入 running 时 preview 文本还是空的，行中心会恰好
    // 落在「中断此任务」按钮的图标上（实测命中 svg path），点了也不开 会话。
    // 点击后校验会话真的打开（对话区出现任务指令），未打开重试（批详情 5s
    // 轮询重渲染吞点击的既有竞态兜底）。
    const promptMark = '先只输出一行'
    const fileTag = child.locator('.bg-child__file')
    for (let i = 0; ; i++) {
      await fileTag.click()
      try {
        await expect(page.getByText(promptMark, { exact: false }).first())
          .toBeVisible({ timeout: 3_000 })
        break
      } catch {
        if (i >= 7) throw new Error('点击子会话后对话区未出现任务指令（selectBatchChild 未生效/点击被重渲染吞掉）')
      }
    }
    console.log('[open] 子会话已打开（对话区出现任务指令）')

    // ── 判别性断言：委托气泡出现时回合仍在跑 ──
    // （每秒采样：气泡可见 && 批组徽标仍 badge--running → 命中；
    //   徽标先终态而气泡从未在 running 期间出现 → 复现缺陷，失败）
    const seenRunningWithBubble = await (async () => {
      const deadline = Date.now() + 150_000
      while (Date.now() < deadline) {
        if (await page.locator('.subtask-bubble').count() > 0) {
          const badge = await batchGroupBadgeClass(page, name)
          if (badge === 'badge--running') return true
          // 气泡与终态同帧到达：无法证明「运行中渲染」，视为未命中
          return false
        }
        await page.waitForTimeout(1_000)
      }
      return false
    })()
    expect(seenRunningWithBubble, '委托气泡应在批组仍 running 时出现（中途挂流实时渲染）')
      .toBe(true)
    await expect(page.locator('.subtask-bubble__agent').first())
      .not.toHaveText('', { timeout: 5_000 })

    // running 期间助手文本可见（token 级流式存活的证据）
    await expect(page.getByText('开始处理').first()).toBeVisible({ timeout: 10_000 })
    await page.screenshot({
      path: `${SHOT_DIR}/batch-midrun-sse-live.png`, fullPage: true,
    })

    // ── 收敛：批次到终态（顺带保证清理前无悬挂 worker 写入）──
    const detail = await waitFor(async () => {
      const d = await getDetail(tk, batchId)
      return ['completed', 'partial', 'failed'].includes(d.batch?.status) ? d : null
    }, 240_000, '批次收敛终态')
    fs.mkdirSync(SHOT_DIR, { recursive: true })
    expect(['completed', 'partial']).toContain(detail.batch.status)
  } finally {
    await cleanupBatch(tk, batchId)
  }
})
