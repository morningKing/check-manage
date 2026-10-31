/**
 * 会话导出 Markdown E2E（2026-10-30 第一档 Feature B）：真实链路——
 * 建会话 + 真实回复 → 更多菜单导出 → 下载文件内容断言。
 */
import { test, expect, type Page, type Locator } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'

test.setTimeout(420_000)

// 行操作区（置顶/更多）hover 才展开（.session-item__actions 靠 :hover 显示），
// 与 session-pin.spec/session-groups.spec 的 hoverClick 同一套：先 hover 再点，重试
// 规避 Vue 重渲染竞态。brief 原句直接 .click() 会因元素不可见 420s 超时。
async function hoverClickMore(page: Page, row: Locator): Promise<void> {
  const btn = row.locator('[data-test="session-more-btn"]')
  for (let i = 0; i < 6; i++) {
    await row.hover()
    try {
      await btn.click({ timeout: 2500 })
      return
    } catch { /* rerender race: re-hover and retry */ }
    await page.waitForTimeout(800)
  }
  await btn.click({ timeout: 5_000 })
}

test('会话导出：更多菜单 → 下载 md 含标题与正文', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const title = tag('export-md')
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title })
  await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请直接回复四个字符：EXPT，不要输出任何其他内容。',
  })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant') ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })

  await openChatSession(page, sid)
  // 每行都有各自的隐藏菜单 DOM（含置顶区在前），按唯一标题定位本用例的行
  const row = page.locator('.session-item:not(.pinned-item)', { hasText: title }).first()
  await row.waitFor({ state: 'visible', timeout: 15_000 })
  await hoverClickMore(page, row)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('[data-test="session-more-menu"] .el-dropdown-menu__item:visible',
      { hasText: '导出 Markdown' }).click(),
  ])
  const path = await download.path()
  const fs = await import('node:fs')
  const md = fs.readFileSync(path!, 'utf-8')
  expect(md).toContain(`# ${title}`)
  // 模型回复文本落在助手节内：助手节存在，且 'EXP'（EXPT 回复的前缀）出现在
  // 助手节标题之后——从助手节起找首个 EXP（用户 prompt 里也有「EXPT」字样，
  // 全文首个出现在用户节，不能拿它当回复证据），防回复文本丢失的假绿
  const aiIdx = md.indexOf('## 🤖 助手')
  expect(aiIdx).toBeGreaterThan(-1)
  expect(md.indexOf('EXP', aiIdx)).toBeGreaterThan(aiIdx)
  expect(md).toContain('## 🧑 用户')
})
