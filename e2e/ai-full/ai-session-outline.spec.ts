/**
 * 会话大纲（spec 2026-10-25-session-outline）：右栏全量用户输入、跨分页精准
 * 定位、滚动跟随高亮、搜索命中定位。真实链路：seed → dev 栈页面行为。
 */
import { expect, test } from '@playwright/test'
import { openChatSession } from './helpers'
import { seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'

const OUTLINE_ITEM = '[data-test="outline-item"]'

test.describe.serial('会话大纲', () => {
  let sid: string

  test.beforeAll(async () => {
    // 260 条消息（130 轮用户输入×2）：初始窗口 200 条装不下 → 必须跨分页
    const msgs: { role: 'user' | 'assistant'; text: string }[] = []
    for (let i = 1; i <= 130; i++) {
      msgs.push({ role: 'user', text: `AITEST 大纲第${i}轮用户输入` })
      msgs.push({ role: 'assistant', text: `第${i}轮回复：处理完毕，共三步。` })
    }
    // status 必须 active/closed——常规搜索按此过滤（seedPlainSession 默认
    // completed 会被搜索排除，TC-04 因此取不到命中）
    sid = await seedPlainSession({ key: 'outline', status: 'active', messages: msgs })
  })

  test.afterAll(() => { cleanupSessionsByPrefix() })

  test('TC-OUTLINE-01 大纲全量显示 + 折叠往返', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    expect(turns).toHaveLength(130)
    await expect(page.locator(OUTLINE_ITEM)).toHaveCount(130)
    // 消息窗口只加载最新 200 条 → 「加载更早消息」仍在
    await expect(page.locator('.ai-chat__load-older')).toBeVisible()
    // 折叠往返
    await page.locator('[data-test="outline-collapse"]').click()
    await expect(page.locator('[data-test="session-outline-panel"]')).toBeHidden()
    await page.locator('[data-test="outline-open"]').click()
    await expect(page.locator(OUTLINE_ITEM).first()).toBeVisible()
  })

  test('TC-OUTLINE-02 点击最旧回合：自动加载更早并精准定位', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    const oldest = turns[0]
    await page.locator(OUTLINE_ITEM).first().click()
    await expect(page.locator(`[data-msg-id="${oldest.id}"]`)).toHaveClass(/msg--located/)
    // 历史已全部加载 → 「加载更早消息」消失
    await expect(page.locator('.ai-chat__load-older')).toBeHidden()
  })

  test('TC-OUTLINE-03 滚动跟随：滚到底部大纲高亮最后一轮', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    const last = turns[turns.length - 1]
    const wrap = page.locator('.ai-chat__messages .el-scrollbar__wrap')
    await wrap.evaluate(el => { el.scrollTop = el.scrollHeight })
    await expect(page.locator(`[data-turn-id="${last.id}"]`)).toHaveClass(/is-active/)
  })

  test('TC-OUTLINE-04 搜索内容命中：打开会话并定位到命中消息', async ({ page }) => {
    const searchResp = page.waitForResponse(r => r.url().includes('/sessions/search'))
    await openChatSession(page, sid)
    await page.locator('.ai-sidebar__search input').fill('大纲第3轮用户输入')
    const { sessions } = await (await searchResp).json()
    const hit = (sessions as any[]).find(s => s.id === sid)
    expect(hit?.messageId).toBeTruthy()
    // 命中行点击 → selectSearchHit → 定位（行模板 .session-item--hit，点击
    // 处理器在行容器上，标题经 hl() v-html 渲染，用行容器 + hasText 定位）
    const row = page.locator('.session-item--hit').filter({ hasText: 'AITEST-SESSV2-outline' })
    await row.first().click()
    await expect(page.locator(`[data-msg-id="${hit.messageId}"]`)).toHaveClass(/msg--located/)
  })
})
