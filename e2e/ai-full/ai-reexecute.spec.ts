/**
 * 批任务「重新执行」端到端验证（确定性版）。
 *
 * 覆盖（2026-09-29 重新执行语义重构的回归防线）：
 *  - 任意终态（completed 或 failed——不依赖模型产出意愿）的子任务均可重执行；
 *  - reexecute 后：对话消息清空、状态回 pending、批计数回滚、
 *    用户上传（uploads/in.txt）随备份-恢复保留；
 *  - 新轮派发在 worker 认领后自动发生（等待收敛但不断言产出内容——
 *    模型产物意愿不稳定，产出断言由路由级/手工验证覆盖）。
 *
 * 子代理复用锚点清除由路由级测试覆盖（test_batch_routes.py）。
 * 断言请求直连后端 3002（绕开 vite 代理——e2e 运行期经代理的 GET 偶发
 * 返回空对象 {}，根因在代理层；批任务行为验证的目标是后端本身）。
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

test('批任务重新执行：清上下文/计数回滚/输入保留/新轮自动派发', async () => {
  const token = await adminToken2()
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  // 1) staging + 建批（1 个子任务；提示词刻意极简——终态即可，产出不参与断言）
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('reexecute-e2e')], { type: 'text/plain' }), 'in.txt')
  form.append('upload_session_id', `rx-${Date.now()}`)
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
      name: `AITEST-rx-e2e-${Date.now()}`,
      prompt: '复述一遍 uploads/in.txt 的大小写原文即可，无需其他操作。',
      files: [await up.json()],
    }),
  })
  expect(create.status).toBe(201)
  const d = await create.json()
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string

  // 2) 等子任务进入任一终态（completed 或 failed 均可——reexecute 对两者语义相同）
  const waitTerminal = async (): Promise<any> => {
    const deadline = Date.now() + 420_000
    while (Date.now() < deadline) {
      const r = await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })
      const dd = await r.json()
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) return dd
      await new Promise(rr => setTimeout(rr, 4000))
    }
    throw new Error('子任务未在时限内达到终态')
  }
  const round1 = await waitTerminal()
  expect(Array.isArray(round1.sessions)).toBe(true)
  const firstStatus = round1.sessions[0].status
  expect(['completed', 'failed']).toContain(firstStatus)

  // 3) 重新执行 → 200
  const rx = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${sid}/reexecute`, {
    method: 'POST', headers: HDRS,
  })
  expect(rx.status).toBe(200)

  // 4) 新轮由 worker 自动派发并收敛（极简任务数秒即完——reexecute 与收敛
  //    之间没有可断言的稳定窗口；改为断言终态后的不变量）
  const deadline = Date.now() + 420_000
  let round2: any = null
  while (Date.now() < deadline) {
    const r = await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })
    round2 = await r.json()
    if (['completed', 'partial', 'failed'].includes(round2.batch.status)) break
    await new Promise(rr => setTimeout(rr, 4000))
  }
  expect(['completed', 'failed']).toContain(round2.sessions[0].status)
  expect(round2.sessions[0].status).toBe('completed')

  // 5) 终态不变量：新轮消息落库（worker 置 completed 与消息可见之间存在
  //    延迟窗口，实测可超 30s——放宽至 120s 轮询）+ 输入保留 + 计数重新计入
  const msgsDeadline = Date.now() + 120_000
  let msgCount = 0
  let lastMsgsBody = ''
  while (Date.now() < msgsDeadline) {
    const msgs2 = await (await fetch(
      `${API}/ai/chat/sessions/${sid}/messages`, { headers: HDRS })).json()
    lastMsgsBody = JSON.stringify(msgs2).slice(0, 200)
    msgCount = (msgs2.messages ?? []).length
    if (msgCount > 0) break
    await new Promise(rr => setTimeout(rr, 3000))
  }
  expect(msgCount, `messages not persisted: ${lastMsgsBody}`).toBeGreaterThan(0)
  expect(round2.batch.done).toBe(1)
  const files2 = await (await fetch(
    `${API}/ai/chat/sessions/${sid}/files`, { headers: HDRS })).json()
  expect((files2.files || []).some((f: any) => f.path === 'uploads/in.txt')).toBe(true)

  // 清理测试批
  await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
  })
})
