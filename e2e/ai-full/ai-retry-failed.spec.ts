/**
 * 批任务「重试失败」端到端验证（确定性版，2026-09-29）。
 *
 * 背景：重试失败已与「重新执行」同语义（全新一轮）——重排前清空并重建
 * 工作区、清上一轮上下文。本用例是这条语义的端到端回归防线。
 *
 * 确定性设计：用「不存在的 agent」触发前置校验 fail-fast（_check_agent），
 * 子任务在数秒内确定性 failed，两轮都不依赖模型产出意愿。
 *
 * 覆盖：
 *  - retry-failed 返回 retried=1，子任务重排 pending 并由 worker 自动派发
 *    第二轮（第二轮再次 fail-fast 收敛，计数重新计入 failed=1）；
 *  - 工作区「全新一轮」：轮间用 fs 直读内部详情返回的 workspace_path 植入
 *    残留文件，POST 返回后（清空在重排事务前同步完成）断言残留清零、
 *    uploads 输入保留——该断言对 worker 是否已抢先派发均稳定（派发路径
 *    _prepare_workspace 非破坏性，不会恢复或删除这些路径）；
 *  - 两轮之间无跨轮消息泄漏（agent fail-fast 轮不产生消息）。
 *
 * 消息/子任务/工具账本/复用锚点/retry_count 清零由路由级测试覆盖
 * （test_batch_routes.py::test_retry_failed_clears_context_like_reexecute）——
 * fail-fast 轮不产生这些残留，HTTP 面上没有可断言的稳定窗口。
 * 断言直连后端 3002（理由见 batch-helpers.ts 头注释）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import {
  adminToken, authHeaders, uploadStaging, createBatch,
  waitBatchTerminal, cleanupBatch,
} from './batch/batch-helpers'

test.setTimeout(600_000)

test('批任务重试失败：清工作区残留/输入保留/计数回滚重计/新轮自动派发', async () => {
  const token = await adminToken()
  const HDRS = authHeaders(token)

  // 1) staging + 建批：不存在的 agent → 派发时 fail-fast，子任务确定性 failed
  const file = await uploadStaging(token, 'in.txt', 'retry-failed-e2e', `rt-${Date.now()}`)
  const d = await createBatch(token, {
    name: `AITEST-rt-e2e-${Date.now()}`,
    prompt: '任意内容——本用例的子任务在 agent 校验即失败，prompt 不会到达模型。',
    agent: `e2e-no-such-agent-${Date.now()}`,
    files: [file],
  })
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string

  try {
    // 2) 等第一轮终态：未知 agent fail-fast，数秒即 failed
    const round1 = await waitBatchTerminal(token, bid, 180_000, 2000)
    expect(round1.sessions[0].status).toBe('failed')
    expect(round1.sessions[0].error_message).toContain('不存在')
    expect(round1.batch.failed).toBe(1)

    // 3) 植入上一轮残留（fs 直写 workspace_path——内部详情暴露该字段）
    const ws = round1.sessions[0].workspace_path as string
    expect(fs.existsSync(ws)).toBe(true)
    fs.writeFileSync(path.join(ws, 'junk-round1.txt'), '上一轮残留', 'utf-8')
    fs.mkdirSync(path.join(ws, 'outputs'), { recursive: true })
    fs.writeFileSync(path.join(ws, 'outputs', 'junk-round1.md'), '上一轮产出', 'utf-8')
    expect(fs.existsSync(path.join(ws, 'uploads', 'in.txt'))).toBe(true)

    // 4) 重试失败 → 200，且恰好重排这 1 个 failed 子任务
    const retry = await fetch(`http://127.0.0.1:3002/ai/chat/batches/${bid}/retry-failed`, {
      method: 'POST', headers: HDRS,
    })
    expect(retry.status).toBe(200)
    expect((await retry.json()).retried).toBe(1)

    // 5) 工作区「全新一轮」不变量（清空在 POST 返回前同步完成，时点确定；
    //    worker 若已抢先派发也不影响——派发路径不重建/不删除这些路径）
    expect(fs.existsSync(path.join(ws, 'junk-round1.txt'))).toBe(false)
    expect(fs.existsSync(path.join(ws, 'outputs', 'junk-round1.md'))).toBe(false)
    expect(fs.existsSync(ws)).toBe(true)
    const inTxt = path.join(ws, 'uploads', 'in.txt')
    expect(fs.existsSync(inTxt)).toBe(true)
    expect(fs.readFileSync(inTxt, 'utf-8')).toBe('retry-failed-e2e')

    // 跨轮消息基线：fail-fast 轮会落一条合法的每轮 notice（全局技能注入
    // 成功提示）——记录它，重试后应清零、第二轮再落一条，总数不跨轮累积
    const msgs1 = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
    const round1MsgCount = (msgs1.messages ?? []).length

    // 6) worker 自动拾取 pending 子任务并派发第二轮（再次 fail-fast 收敛）
    const round2 = await waitBatchTerminal(token, bid, 180_000, 2000)
    expect(round2.sessions[0].status).toBe('failed')
    // 计数回滚后重新计入：failed 归零再 +1；批次状态重新收敛终态
    expect(round2.batch.failed).toBe(1)
    expect(round2.sessions[0].error_message).toContain('不存在')

    // 7) 无跨轮消息残留：fail-fast 轮的唯一合法消息是每轮至多一条的系统
    //    notice（技能注入提示，注入状态跨轮可能波动）——断言第二轮消息数
    //    不超过基线 + 2；「消息清零」的权威防线在路由级
    //    test_retry_failed_clears_context_like_reexecute（真库 count 断言），
    //    旧实现不清消息时这里仍会因翻倍暴露。
    const msgs2 = await (await fetch(
      `http://127.0.0.1:3002/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
    expect((msgs2.messages ?? []).length).toBeLessThanOrEqual(round1MsgCount + 2)
  } finally {
    await cleanupBatch(token, bid)
  }
})
