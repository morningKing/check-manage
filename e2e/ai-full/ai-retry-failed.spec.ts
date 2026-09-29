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
 * 断言直连后端 3002（同 ai-reexecute.spec.ts 的理由）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

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

test.setTimeout(600_000)

test('批任务重试失败：清工作区残留/输入保留/计数回滚重计/新轮自动派发', async () => {
  const token = await adminToken2()
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  // 1) staging + 建批：不存在的 agent → 派发时 fail-fast，子任务确定性 failed
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('retry-failed-e2e')], { type: 'text/plain' }), 'in.txt')
  form.append('upload_session_id', `rt-${Date.now()}`)
  const up = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })
  expect(up.status).toBe(201)
  const create = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST',
    headers: HDRS,
    body: JSON.stringify({
      name: `AITEST-rt-e2e-${Date.now()}`,
      prompt: '任意内容——本用例的子任务在 agent 校验即失败，prompt 不会到达模型。',
      agent: `e2e-no-such-agent-${Date.now()}`,
      files: [await up.json()],
    }),
  })
  expect(create.status).toBe(201)
  const d = await create.json()
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string

  const getDetail = async () =>
    await (await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })).json()

  // 2) 等第一轮终态：未知 agent fail-fast，数秒即 failed
  const waitTerminal = async (): Promise<any> => {
    const deadline = Date.now() + 180_000
    while (Date.now() < deadline) {
      const dd = await getDetail()
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) return dd
      await new Promise(rr => setTimeout(rr, 2000))
    }
    throw new Error('子任务未在时限内达到终态（第一轮）')
  }
  const round1 = await waitTerminal()
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

  // 跨轮消息基线：fail-fast 轮会落一条合法的每轮 notice（全局技能注入成功
  // 提示）——记录它，重试后应清零、第二轮再落一条，总数不跨轮累积
  const msgs1 = await (await fetch(
    `${API}/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
  const round1MsgCount = (msgs1.messages ?? []).length

  // 4) 重试失败 → 200，且恰好重排这 1 个 failed 子任务
  const retry = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, {
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

  // 6) worker 自动拾取 pending 子任务并派发第二轮（再次 fail-fast 收敛）
  const round2 = await waitTerminal()
  expect(round2.sessions[0].status).toBe('failed')
  // 计数回滚后重新计入：failed 归零再 +1；批次状态重新收敛终态
  expect(round2.batch.failed).toBe(1)
  expect(round2.sessions[0].error_message).toContain('不存在')

  // 7) 无跨轮消息残留：第二轮后消息数 == 第一轮基线（旧实现不清消息 → 2 倍）
  const msgs2 = await (await fetch(
    `${API}/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
  expect((msgs2.messages ?? []).length).toBe(round1MsgCount)

  // 清理测试批
  await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
  })
})
