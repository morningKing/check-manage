/**
 * AI Harness P2 编排真实链路 E2E（ai-harness spec §12.3-P2）。
 *
 * P0/P1 批任务用例已于 2026-10-04 全部收编/去重（Task 14 收尾，逐块去向）：
 *  - P0-1 发送门禁 409 BATCH_SESSION_CONTROLLED：API 半边 →
 *    batch/control.spec.ts 用例 9（sleepBatch 确定性 running 窗口）；
 *    UI 半边（batch-bar + composer 禁用 + 状态条文案）→
 *    batch/ui-journeys.spec.ts 用例 10；
 *  - P1 内部事件流 events + afterSeq 增量 → batch/lifecycle.spec.ts 用例 6
 *    （fail-fast 确定性构造，先行收编）；
 *  - P1 对外 events（X-API-Key，camelCase eventId/eventSeq 契约）→
 *    batch/openapi.spec.ts 用例 3 的事件流断言段（本任务并入全生命周期用例）；
 *  - P0 终态子会话经批通道 continue（历史保留）→
 *    batch/retry-reexecute.spec.ts 用例 5（确定性回声构造，先行收编）；
 *  - P0 对外删除治理（BATCH_NOT_TERMINAL 409 / stop=true drain）→
 *    batch/openapi.spec.ts 用例 4（2026-10-04 收编）；
 *  - P1 命令幂等（Idempotency-Key）→ batch/control.spec.ts 用例 4（同日收编）。
 * 本文件仅保留非批域的 P2 编排 DAG 用例。
 *
 * P2 编排：
 *  - 3 节点 DAG（抽取 → 审批 → 汇总）真实跑通：waiting_approval → approve → completed。
 *
 * 注意：/ai-chat 页有常驻 SSE，禁止 networkidle；request.fetch 字符串 body 必须
 * 显式 Content-Type。真实 OpenCode 回合，预算充足（ai-full 允许长任务）。
 */
import { test, expect } from '@playwright/test'
import { api, SHOT_DIR } from './helpers'

const TAG = `harness-${Date.now()}`

/** 真实表单登录（/ai-chat 有常驻 SSE，
 *  严禁等待 networkidle——等真实元素）。 */
async function gotoChat(page: import('@playwright/test').Page,
                        suffix = ''): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.getByRole('button', { name: /登\s*录/ })
      .waitFor({ state: 'hidden', timeout: 15_000 })
  } catch { /* 已是登录态 */ }
  await page.goto(`/ai-chat${suffix}`)
  const sidebar = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
  for (let i = 0; i < 3; i++) {
    try {
      await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
      return
    } catch {
      await page.goto(`/ai-chat${suffix}`)
    }
  }
  await sidebar.waitFor({ state: 'visible', timeout: 60_000 })
}

test.describe.configure({ mode: 'serial' })

// 真实 OpenCode 回合（含审批等待）——ai-full 允许长任务
test.setTimeout(600_000)

test('P2：3 节点 DAG（抽取→审批→汇总）真实跑通', async ({ request, page }) => {
  // 1) 发布定义
  const defName = `AITEST-${TAG}-dag`
  const pub = await api(request, 'POST', '/ai/xxx-ignore', undefined).catch(() => null)
  void pub
  // 通过 /ai/orchestrations 发布（JWT 内部域；/v1/ai-orchestrations 留给对外 API Key 契约）
  const pubRes = await api(request, 'POST', '/ai/orchestrations/definitions', {
    name: defName,
    nodes: [
      { id: 'extract', kind: 'agent', prompt_template: '请从这句话抽取数字并以「数字: N」结尾：{{input.text}}' },
      { id: 'gate', kind: 'approval', approval: { requested_roles: ['admin'], risk_level: 'high' }, name: '发布前审批' },
      { id: 'summarize', kind: 'agent', prompt_template: '上游抽取结果：{{steps.extract}}。请用一句话确认收到。' },
    ],
    edges: [
      { source: 'extract', target: 'gate', kind: 'advance' },
      { source: 'gate', target: 'summarize', kind: 'advance' },
    ],
  })
  expect(pubRes.status).toBe(201)
  const defId = pubRes.json.id

  // 2) 创建 run
  const runRes = await api(request, 'POST', '/ai/orchestrations/runs', {
    definitionId: defId,
    input: { text: '订单金额是 66 元' },
  })
  expect(runRes.status).toBe(201)
  const runId = runRes.json.id

  // 3) extract 真实执行 → 到达审批点
  const deadline = Date.now() + 300_000
  let approvalId = ''
  let run: any = null
  while (Date.now() < deadline) {
    const r = await api(request, 'GET', `/ai/orchestrations/runs/${runId}`)
    run = r.json
    if (run?.status === 'waiting_approval') {
      const aps = await api(request, 'GET', '/v1/ai-approvals')
      const mine = (aps.json?.approvals ?? []).find(
        (a: any) => a.runId === runId && a.status === 'pending')
      if (mine) { approvalId = mine.id; break }
    }
    await new Promise(r2 => setTimeout(r2, 3000))
  }
  expect(approvalId, 'run 应到达 waiting_approval 且产生审批请求').toBeTruthy()
  const shotRun = await api(request, 'GET', `/ai/orchestrations/runs/${runId}`)
  console.log('等待审批时 run 状态:', JSON.stringify(shotRun.json?.status))

  // 4) 审批通过 → summarize 执行 → run completed
  const appr = await api(request, 'POST',
                         `/v1/ai-approvals/${approvalId}/approve`, { comment: 'e2e 通过' })
  expect(appr.status).toBe(200)
  while (Date.now() < deadline) {
    const r = await api(request, 'GET', `/ai/orchestrations/runs/${runId}`)
    run = r.json
    if (['completed', 'partial', 'failed'].includes(run?.status)) break
    await new Promise(r2 => setTimeout(r2, 3000))
  }
  expect(run?.status).toBe('completed')
  const steps = Object.fromEntries((run.steps ?? []).map((s: any) => [s.node_id, s]))
  expect(steps.extract.status).toBe('succeeded')
  expect(steps.gate.status).toBe('succeeded')
  expect(steps.summarize.status).toBe('succeeded')

  // 5) run 事件时间线可重建（事实源 ai_batch_events）
  const evs = await api(request, 'GET', `/ai/orchestrations/runs/${runId}/events`)
  expect(evs.status).toBe(200)
  const types = (evs.json?.events ?? []).map((e: any) => e.event_type)
  expect(types).toContain('run.created')
  expect(types).toContain('step.finished')
  await gotoChat(page)
  await page.screenshot({ path: `${SHOT_DIR}/harness-p2-dag-completed.png`, fullPage: false })
})
