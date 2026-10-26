/**
 * 外部接管（人工接管公开页）· 真实链路 E2E —— 真后端 + 真库 + 真 worker。
 *
 * 链路：OpenAPI 签发接管链接（X-API-Key → /v1/ai-batches/<bid>/sessions/<sid>/
 * takeover-link）→ 免登录公开页 /takeover/session?t=…（3s 轮询）→ 接管发送
 * （202 入队，paused 子任务 repark 回 pending）→ 真实 worker 认领消费 →
 * fail-fast 收口 → 控制权交还外部系统（spec D3 repark 回 paused）。
 *
 * 确定性纪律（同 failFastBatch，整链路 0 LLM）：
 * - 种子批 agent='e2e-no-such-agent'：worker 认领后 _check_agent 秒级 fail-fast，
 *   不派发 OpenCode、不烧任何模型调用；
 * - 认领查询不按批状态过滤（batch_engine._claim_pending_sessions 只认子会话
 *   status='pending'），接管发送（continue_child repark）置 pending 后子任务必被认领；
 * - 认领后的落点：种子子会话无 opencode_session_id → 走新回合分支 →
 *   workspace 准备后 _check_agent 即失败 → _mark_failed 先经 try_repark_transition
 *   （continue_repark=true 命中）→ 子任务回 paused、done/failed 计数不动、
 *   批重算回 paused。轮询断言以此「消费签名」为准：
 *   fencing_token+1（认领）、workspace_path 落库（fail-fast 前置）、
 *   continue_repark 已被消费、终态 paused（容忍 failed：repark CAS 未命中的
 *   旧路径），而不是固定等 failed——D3 语义下接管轮失败不进终态。
 *   （continue_prompt 在该分支不清空：它只在 is_continue 分支被读取消费——
 *   实测 2026-10-25，不作为消费判据。）
 * - 注意：fail-fast 发生在 _persist_user_prompt 之前，接管消息不落
 *   ai_chat_messages——故「发送后消息出现在线程」断言不成立（真实 agent 的
 *   正常链路才落库），此处断言 202 受理 + 消费签名 + 计数不动（0 LLM）。
 *
 * 清理纪律：AITEST 前缀自造值；tokens/messages/sessions/batch 全显式删除
 * （batch_takeover_tokens 刻意无外键，必须按 session_id 手工清）；API Key 用后即删。
 *
 * LLM 预算标记：用例 1-3 为 0 LLM（failFast 构造）；用例 4（接管中断）
 * 为 @llm-light —— sleepBatch 真实长任务开 running 窗口（1 次 sleep 回合）
 * + 中断后的纠正续跑（1 次轻量回合），同 e2e/ai-full/batch/control.spec 约定。
 */
import { test, expect } from '@playwright/test'
import {
  api, openApi, createApiKey, deleteApiKey, screenshot, restartBackend,
} from './ai-full/helpers'
import { newId, cleanupSessionsByPrefix } from './ai-full/db-helpers'
import { dbSeed, sleepBatch, adminTokenCached } from './ai-full/batch/toolbox'
import { cleanupBatch, getDetail } from './ai-full/batch/batch-helpers'

test.setTimeout(180_000)

const KEY_TAG = `tk-${process.pid}-${Date.now()}`
const BATCH_ID = `AITEST-TK-${Date.now()}`
const SID = newId('sess_')
const SESSION_TITLE = '接管E2E子任务'
const USER_MARKER = 'TK-E2E-USER-MARKER'
const ASSISTANT_MARKER = 'TK-E2E-ASSISTANT-MARKER'
const TAKEOVER_PROMPT = '人工补充指令 e2e'

let KEY = ''
let KEY_ID = ''
let PATH = ''   // 主用例签发的公开页路径（负例复用对照）

/** 消息 content jsonb：文本必须经 JSON.stringify 转义（jsonb 拒收裸换行） */
const content = (text: string) => `[{"type":"text","text":${JSON.stringify(text)}}]`

test.beforeAll(async ({ request }) => {
  KEY = await createApiKey(request, KEY_TAG)
  // api_keys.id 是 ai_chat_batches.api_key_id 的真实外键，且 takeover-link 的
  // 绑定校验按 api_key_id 圈批——批种子必须挂这把新 key 的 id
  const list = await api(request, 'GET', '/apiKeys')
  const row = (list.json ?? []).find((k: any) => k.name === `AITEST-${KEY_TAG}`)
  if (!row?.id) throw new Error(`api key row not found: AITEST-${KEY_TAG}`)
  KEY_ID = row.id
  dbSeed(`
    INSERT INTO ai_chat_batches (id, user_id, name, prompt, agent, status, total, api_key_id)
    VALUES ('${BATCH_ID}', 'user-admin', '${BATCH_ID}', 'e2e', 'e2e-no-such-agent', 'running', 1, '${KEY_ID}');
    INSERT INTO ai_chat_sessions (id, user_id, title, status, batch_id, batch_seq)
    VALUES ('${SID}', 'user-admin', '${SESSION_TITLE}', 'paused', '${BATCH_ID}', 1);
    INSERT INTO ai_chat_messages (id, session_id, role, content) VALUES
      ('${newId('msg_')}', '${SID}', 'user', '${content(`${USER_MARKER}: please summarize the input.`)}'),
      ('${newId('msg_')}', '${SID}', 'assistant', '${content(`${ASSISTANT_MARKER}: previous answer text.`)}');
  `)
})

test.afterAll(async ({ request }) => {
  // batch_takeover_tokens 无外键，随会话清理必须显式删
  dbSeed(`DELETE FROM batch_takeover_tokens WHERE session_id='${SID}'`)
  dbSeed(`DELETE FROM ai_chat_messages WHERE session_id='${SID}'`)
  dbSeed(`DELETE FROM ai_chat_sessions WHERE batch_id='${BATCH_ID}'`)
  dbSeed(`DELETE FROM ai_chat_batches WHERE id='${BATCH_ID}'`)
  await deleteApiKey(request, KEY_TAG)
  cleanupSessionsByPrefix()   // 兜底（本套件种子不走 SESSV2 前缀，常态为空操作）
})

test('接管真实链路：签发→公开页免登录→接管发送→worker fail-fast 消费→repark 交还',
     async ({ page, request }) => {
  // 1. OpenAPI 签发接管链接（X-API-Key，非 admin JWT）
  const issued = await openApi(request, KEY, 'POST',
    `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link`, { ttlHours: 1 })
  expect(issued.status, JSON.stringify(issued.json)).toBe(201)
  expect(issued.json.token).toMatch(/^tk_/)
  expect(issued.json.path).toBe(`/takeover/session?t=${issued.json.token}`)
  expect(typeof issued.json.expiresAt).toBe('string')
  PATH = issued.json.path

  // 2. 公开页直开（不带任何注入登录态——本身就是免登录访问的证明）
  await page.goto(PATH)
  await expect(page.locator('.takeover-topbar__title')).toHaveText(SESSION_TITLE)
  await expect(page.locator('.takeover-topbar__batch')).toHaveText(BATCH_ID)
  await expect(page.locator('.takeover-badge')).toHaveText('已暂停（可接管）')
  // 种子历史两条可见（assistant 文本经 MarkdownView 渲染）
  await expect(page.locator('.msg--user').getByText(USER_MARKER)).toBeVisible()
  await expect(page.locator('.msg--assistant').getByText(ASSISTANT_MARKER)).toBeVisible()

  // 3. 发送接管指令 → 输入清空、无 409 内联警告
  const input = page.locator('.takeover-composer__input')
  await input.fill(TAKEOVER_PROMPT)
  await page.locator('.takeover-composer__send').click()
  await expect(input).toHaveValue('')
  await expect(page.locator('[data-test="conflict-warning"]')).toHaveCount(0)

  // 4. 轮询 worker 真实消费签名（认领 + fail-fast + repark 收口）。
  //    认领事件驱动（notify）+ 2s 轮询，通常数秒内完成；60s 上限留足余量。
  await expect.poll(async () => {
    const rows = dbSeed(`SELECT status, continue_repark, fencing_token,
                                workspace_path IS NOT NULL
                           FROM ai_chat_sessions WHERE id='${SID}'`)
    const [st, repark, ftk, hasWs] = rows[0] ?? []
    const settled = st === 'paused' || st === 'failed'
    if (ftk >= 1 && hasWs && !repark && settled) return 'consumed'
    return `waiting:${JSON.stringify(rows[0] ?? null)}`
  }, { timeout: 60_000, intervals: [2_000] }).toBe('consumed')

  // D3 交还外部系统：done/failed 计数不动（0 LLM 烧钱证据）、批回 paused
  const detail = await openApi(request, KEY, 'GET', `/v1/ai-batches/${BATCH_ID}`)
  expect(detail.status).toBe(200)
  expect(detail.json.status).toBe('paused')
  expect(detail.json.done).toBe(0)
  expect(detail.json.failed).toBe(0)
  expect(detail.json.total).toBe(1)
  expect(detail.json.paused).toBe(true)

  // 页面 3s 轮询收敛后徽标回到「已暂停（可接管）」——控制权交还
  await expect(page.locator('.takeover-badge'))
    .toHaveText('已暂停（可接管）', { timeout: 15_000 })
  await screenshot(page, 'external-takeover-live')
})

test('负例：伪造令牌错误卡；新签发→吊销→原链接刷新失效', async ({ page, request }) => {
  // 1) 篡改令牌 → 整页错误卡，不崩溃
  await page.goto('/takeover/session?t=tk_bad')
  const card = page.locator('[data-test="error-card"]')
  await expect(card).toBeVisible()
  await expect(card).toContainText('链接无效或已过期')

  // 2) 新签发一把链接（主用例令牌之外的可吊销样本）
  const issued = await openApi(request, KEY, 'POST',
    `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link`, { ttlHours: 1 })
  expect(issued.status, JSON.stringify(issued.json)).toBe(201)
  const freshPath = issued.json.path

  // tokenId 不在签发响应里（只存哈希）——按 created_at 取最新未吊销行
  const rows = dbSeed(`SELECT id FROM batch_takeover_tokens
                        WHERE session_id='${SID}' AND revoked_at IS NULL
                        ORDER BY created_at DESC LIMIT 1`)
  expect(rows.length).toBe(1)
  const tokenId = rows[0][0]

  // 三元组不匹配的吊销 → 404；正确吊销 → 200
  const bad = await openApi(request, KEY, 'POST',
    `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link/revoke`,
    { tokenId: 'tkt_fake' })
  expect(bad.status).toBe(404)
  const rev = await openApi(request, KEY, 'POST',
    `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link/revoke`, { tokenId })
  expect(rev.status).toBe(200)
  expect(rev.json.revoked).toBe(true)

  // 3) 刚才还有效的链接，吊销后刷新 → 同一张错误卡
  await page.goto(freshPath)
  const card2 = page.locator('[data-test="error-card"]')
  await expect(card2).toBeVisible()
  await expect(card2).toContainText('链接无效或已过期')
  await screenshot(page, 'external-takeover-error')
})

test('TTL 部署级可配置：TAKEOVER_TTL_MAX_HOURS / TAKEOVER_TTL_DEFAULT_HOURS 真实重启生效', async ({ request }) => {
  // 真实链路证明（非单测 monkeypatch）：带 env 覆盖重启后端 → 签发响应的
  // expiresAt 反映配置——显式 100h 被钳到 max=48h；未传 ttlHours 用
  // default=3h（两值不同才能区分「默认生效」与「上限钳制」两条路径）。
  const hoursFromNow = (iso: unknown) =>
    (new Date(String(iso)).getTime() - Date.now()) / 3_600_000
  try {
    await restartBackend({ TAKEOVER_TTL_MAX_HOURS: '48', TAKEOVER_TTL_DEFAULT_HOURS: '3' })
    const clamped = await openApi(request, KEY, 'POST',
      `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link`, { ttlHours: 100 })
    expect(clamped.status, JSON.stringify(clamped.json)).toBe(201)
    expect(hoursFromNow(clamped.json.expiresAt)).toBeGreaterThan(47.9)
    expect(hoursFromNow(clamped.json.expiresAt)).toBeLessThanOrEqual(48.1)

    const byDefault = await openApi(request, KEY, 'POST',
      `/v1/ai-batches/${BATCH_ID}/sessions/${SID}/takeover-link`, {})
    expect(byDefault.status, JSON.stringify(byDefault.json)).toBe(201)
    expect(hoursFromNow(byDefault.json.expiresAt)).toBeGreaterThan(2.9)
    expect(hoursFromNow(byDefault.json.expiresAt)).toBeLessThanOrEqual(3.1)
  } finally {
    await restartBackend()   // 无参恢复默认 env（不留下 48/3 的本机覆盖）
  }
})

test('接管中断真实链路：执行中两段确认中断→worker 协作式落 cancelled→纠正消息续跑',
     async ({ page, request }) => {
  test.setTimeout(600_000)
  // @llm-light：sleepBatch 真实长任务（bash sleep 120s）构造稳定 running 窗口
  // ——seeded running 行无人认领，cancel_requested 不会被任何 worker 消费，
  // 必须让真实 worker 拥有租约才能验证协作式中断全链路。
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })
  let sid = ''
  try {
    // takeover-link 签发按 api_key_id 圈批——内部 API 建的批补挂本套件 key
    dbSeed(`UPDATE ai_chat_batches SET api_key_id='${KEY_ID}' WHERE id='${bid}'`)
    sid = (await getDetail(tk, bid)).sessions[0].id as string

    // 等真实 worker 认领进入 running（sleep 窗口打开）
    await expect.poll(async () => {
      const rows = dbSeed(`SELECT status FROM ai_chat_sessions WHERE id='${sid}'`)
      return rows[0]?.[0] === 'running' ? 'running' : `waiting:${rows[0]?.[0]}`
    }, { timeout: 120_000, intervals: [2_000] }).toBe('running')

    const issued = await openApi(request, KEY, 'POST',
      `/v1/ai-batches/${bid}/sessions/${sid}/takeover-link`, { ttlHours: 1 })
    expect(issued.status, JSON.stringify(issued.json)).toBe(201)

    // 公开页：执行中徽标 + 中断按钮可见
    await page.goto(issued.json.path)
    await expect(page.locator('.takeover-badge')).toHaveText('执行中', { timeout: 60_000 })
    const stop = page.locator('[data-test="stop-btn"]')
    await expect(stop).toHaveText('中断任务')

    // 两段确认：第一击只武装，第二击才发请求
    await stop.click()
    await expect(stop).toHaveText('再点一次确认中断')
    await stop.click()
    await expect(page.locator('[data-test="cancel-notice"]')).toBeVisible()

    // 202 落到真实标志位：cancel_requested 置真或 worker 已收口 cancelled
    await expect.poll(async () => {
      const rows = dbSeed(`SELECT status, cancel_requested
                             FROM ai_chat_sessions WHERE id='${sid}'`)
      const [st, flag] = rows[0] ?? []
      return (flag === true || st === 'cancelled') ? 'flagged' : `waiting:${st}/${flag}`
    }, { timeout: 30_000, intervals: [1_000] }).toBe('flagged')

    // worker 协作式中止 → 落 cancelled → 3s 轮询刷徽标、输入解锁
    await expect(page.locator('.takeover-badge')).toHaveText('已取消', { timeout: 90_000 })
    await screenshot(page, 'external-takeover-interrupt')

    // 纠正闭环：cancelled 状态下消息直接可发（repark=false 同会话续跑）——
    // 真实 agent 路径落用户消息 + 子任务离开 cancelled 重新执行
    const corrective = 'TK-E2E-CORRECTIVE-MARKER: redo with a summary table.'
    const input = page.locator('.takeover-composer__input')
    await input.fill(corrective)
    await page.locator('.takeover-composer__send').click()
    await expect(input).toHaveValue('')
    await expect(page.locator('[data-test="conflict-warning"]')).toHaveCount(0)
    await expect.poll(async () => {
      const st = dbSeed(`SELECT status FROM ai_chat_sessions WHERE id='${sid}'`)[0]?.[0]
      const msg = dbSeed(`SELECT count(*) FROM ai_chat_messages
                           WHERE session_id='${sid}' AND role='user'
                             AND content::text LIKE '%TK-E2E-CORRECTIVE-MARKER%'`)[0]?.[0]
      return (['pending', 'running'].includes(st) && Number(msg) >= 1)
        ? 'rerun' : `waiting:${st}/${msg}`
    }, { timeout: 90_000, intervals: [2_000] }).toBe('rerun')
  } finally {
    dbSeed(`DELETE FROM batch_takeover_tokens WHERE session_id='${sid}'`)
    await cleanupBatch(tk, bid)
  }
})
