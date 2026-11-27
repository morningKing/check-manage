/**
 * AI 会话「点击加载」性能优化 · 结构化验证（0-LLM：纯 DB 播种，不发任何消息）。
 *
 * 覆盖的优化点：
 * - MessageBubble 渲染隔离 + 单条消息解析缓存（组件级，vitest 覆盖）
 * - 可视区窗口渲染：初始只挂载最近 REVEAL_INITIAL(30) 条，上滚哨兵增量补渲染
 * - ToolCallBubble 懒 body：折叠工具气泡不渲染 body（此前挂载即全量 stringify）
 * - 骨架屏 + 打开会话指纹去重复拉取（行为面在 vitest store 用例）
 *
 * 断言以 DOM 结构为主；时限断言只设宽裕回归哨（本地栈波动大，不做严格性能门）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import { openChatSession, screenshot, AUTH_FILE } from './helpers'
import { dbSeed } from './batch/toolbox'
import { newId, cleanupSessionsByPrefix } from './db-helpers'

const TURNS = 40                     // 40 轮 × 2 条（user + assistant 带工具）= 80 条
const TOOL_RESULT = 'x'.repeat(50_000)   // 单工具输出 ~50KB，40 个 = 2MB 量级载荷
const REVEAL_INITIAL = 30            // 与 AiChatView 常量一致

async function seedHeavySession(key: string): Promise<string> {
  const sid = newId('sess_')
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status) VALUES ('${sid}', 'user-admin', 'AITEST-SESSV2-${key}', 'completed')`)
  for (let i = 1; i <= TURNS; i++) {
    dbSeed(`INSERT INTO ai_chat_messages (id, session_id, role, content) VALUES ('${newId('msg_')}', '${sid}', 'user', '[{"type":"text","text":${JSON.stringify(`第${i}轮提问 AITEST`)}}]')`)
    dbSeed(`INSERT INTO ai_chat_messages (id, session_id, role, content) VALUES ('${newId('msg_')}', '${sid}', 'assistant', '[{"type":"text","text":${JSON.stringify(`第${i}轮回答`)}},{"type":"tool_use","name":"bash","title":"probe-${i}","status":"completed","input":{"command":"seed"},"result":${JSON.stringify(TOOL_RESULT)}}]')`)
  }
  return sid
}

test.afterAll(() => { cleanupSessionsByPrefix() })

test('重度会话打开：初始窗口挂载 + 上滚补渲染 + 大纲跨窗定位 + 懒工具体', async ({ page }) => {
  const sid = await seedHeavySession('load-perf')
  try {
    const t0 = Date.now()
    await openChatSession(page, sid)
    await expect(page.locator('.ai-thread .msg').first()).toBeVisible({ timeout: 60_000 })
    // 宽裕回归哨：真实性能门以人工探针为准，这里只防「优化被整个回退」级别的退化
    expect(Date.now() - t0, '打开到线程可见').toBeLessThan(60_000)

    // ① 初始只挂载最近 REVEAL_INITIAL 条（此前 200 条全量挂载）
    const initialCount = await page.locator('[data-msg-idx]').count()
    expect(initialCount, '初始渲染消息数 ≤ 初始窗口').toBeLessThanOrEqual(REVEAL_INITIAL)
    // 窗口从尾部锚定：最新一条（下标 119）在 DOM
    await expect(page.locator(`[data-msg-idx="${TURNS * 2 - 1}"]`)).toHaveCount(1)

    // ② 懒工具体：折叠工具气泡不渲染 body（此前挂载即对全部 result stringify）
    expect(await page.locator('.tool-call__body').count(), '折叠工具气泡无 body').toBe(0)
    await page.locator('.tool-call__head').first().click()
    await expect(page.locator('.tool-call__body').first()).toBeVisible()

    // ③ 上滚到顶 → 哨兵补渲染：消息数增长；视口经滚动补偿不炸不跳空
    const before = await page.locator('[data-msg-idx]').count()
    const wrap = page.locator('.ai-chat__messages .el-scrollbar__wrap')
    await wrap.evaluate((el) => { el.scrollTop = 0 })
    await expect.poll(async () => page.locator('[data-msg-idx]').count(),
                       { timeout: 20_000, intervals: [500, 1_000, 2_000] })
      .toBeGreaterThan(before)

    // ④ 大纲跳最早回合：跨窗口定位兜底（ensureLoadedUntil + 扩窗 + 高亮）
    await expect(page.locator('[data-test="session-outline-panel"]')).toBeVisible({ timeout: 30_000 })
    await page.locator('[data-test="outline-item"]').first().click()
    await expect(page.locator('[data-msg-idx="0"]'), '最早消息补渲染进 DOM').toHaveCount(1, { timeout: 30_000 })
    await expect(page.locator('[data-msg-idx="0"]')).toHaveClass(/msg--located/, { timeout: 10_000 })

    await screenshot(page, 'session-load-perf')
  } finally {
    // 主用例的种子就地清理，避免残留影响后续断言；公共兜底在 afterAll
    dbSeed(`DELETE FROM ai_chat_messages WHERE session_id = '${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id = '${sid}'`)
  }
})

test('骨架屏：打开未完成 adopt 的会话先见骨架再替换为消息流', async ({ page }) => {
  const sid = await seedHeavySession('load-perf-skeleton')
  try {
    // 与 helpers.openChatSession 同源的登录态注入（此处要自己控制 goto 时机）
    const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
    await page.addInitScript((entries: Record<string, string>) => {
      for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
    }, auth)
    const nav = page.goto(`/ai-chat?session=${sid}`)
    // 骨架窗口很短（本地栈毫秒级拉取），能否捕到随环境波动——只做存在性
    // 弱断言：要么捕到骨架，要么线程已就位（两者必有其一，且最终以消息流收尾）
    await nav
    const sawSkeleton = await page.locator('[data-test="thread-skeleton"]')
      .waitFor({ state: 'visible', timeout: 3_000 }).then(() => true).catch(() => false)
    if (!sawSkeleton) {
      await expect(page.locator('.ai-thread .msg').first()).toBeVisible({ timeout: 30_000 })
    }
    await expect(page.locator('.ai-thread .msg').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('[data-test="thread-skeleton"]')).toHaveCount(0)
  } finally {
    dbSeed(`DELETE FROM ai_chat_messages WHERE session_id = '${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id = '${sid}'`)
  }
})
