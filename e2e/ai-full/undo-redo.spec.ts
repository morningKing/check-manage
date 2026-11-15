/**
 * AI 会话撤销/重做 @llm 真模型冒烟（undo-redo spec T15）。
 *
 * 预算口径（docs/ai-testing/README.md 约定）：本 spec 1 个用例、type: 'llm'
 * —— 真实 OpenCode 回合 ×2（首轮回复 + 重新生成的重试回合；/undo 与重做
 * 本身是 stash 搬回 + OC unrevert，不耗模型）。环境要求：dev 栈
 * （3002/5173）+ 真 OC :4096。
 *
 * 断言链（T12 审查裁定的可实跑形态）：
 * ① 点助手「重新生成」→ 确认 → revert-banner 出现；
 * ② 重试回合收敛后横幅消失（新回合提交即收口撤销窗口——undo-redo spec
 *    §2.4 E3/T9 设计行为：重新生成=撤销+重发，重发使撤销不可重做）；
 * ③ 独立撤销（/undo 本地命令，无重发跟随）→ 横幅出现且稳定 → 点 redo-btn
 *    重做 → 横幅消失 + 被撤消息恢复。重做的可点窗口只在独立撤销路径存在
 *    （T15 实测：重新生成路径横幅仅存活一个发送往返，且流式期间按钮禁用）。
 * 刻意不加「切走再切回横幅不出现」——独立撤销后横幅会重现，该预期是错的。
 */
import { test, expect } from '@playwright/test'
import { adminToken, openChatSession, gotoWithAuth } from './helpers'

test.setTimeout(420_000)   // 真模型回合放宽总预算（同 ai-session-core 口径）

test('AI 会话撤销冒烟：重新生成后出现重做横幅，重做恢复', async ({ page, request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // 2 个真实回合：首轮 + 重试
  const token = await adminToken(request)
  const create = await request.post('/api/ai/chat/sessions', {
    headers: { Authorization: `Bearer ${token}` },
    data: {},
  })
  const { id } = await create.json()
  // 服务端默认模型 opencode-go/deepseek-v4.1-flash 当日挂死（回合 0 token 无
  // 响应，OC 直连同款复现），冒烟改用 OC :4096 上可用的真模型（T15 实测 4s 出答）。
  const MODEL = 'opencode/minimax-m3.1-flash-free'
  await page.addInitScript(([sid, model]) => {
    localStorage.setItem(`check-manage:ai-chat:model:${sid}`, model!)
  }, [id, MODEL])
  await gotoWithAuth(page, '/ai-chat')
  await openChatSession(page, id)
  // 等视图真正解析完 ?session= 参数（侧栏出现 active 会话项）再操作：
  // shell 一挂载 waitForSelector 就过，但此刻 activeId 尚未解析，
  // 立刻 fill+Enter 会被初始化窗口吞掉（T15 实测：文本消失、无 POST）。
  await expect(page.locator('.session-item.active')).toBeVisible({ timeout: 30_000 })
  await page.locator('.composer-input textarea')
    .fill('请直接回复五个字符：OK-UD，不要写文件、不要运行任何命令。')
  // 发送走既有 ai-chat-smoke 同款按钮路径（比 Enter 键序更确定性）。
  await expect(page.getByRole('button', { name: '发送' })).toBeEnabled({ timeout: 10_000 })
  await page.getByRole('button', { name: '发送' }).click()

  // ① 等首轮回复收敛：助手操作条出现且重新生成可点（真模型放宽超时）
  const answer = page.locator('.msg__actions--assistant [title="重新生成"]').first()
  await expect(answer).toBeVisible({ timeout: 180_000 })
  await answer.click()                                       // click 自带 enabled 等待
  await page.getByRole('button', { name: '继续' }).click()   // ElMessageBox 确认

  // ② 重新生成路径：确认后横幅出现（撤销已生效）；重发被接受即收口，
  //    横幅随新回合提交消失（设计行为），回合收敛后必然不在。
  const banner = page.locator('[data-test="revert-banner"]')
  await expect(banner).toBeVisible({ timeout: 30_000 })
  const retried = page.locator('.msg__actions--assistant [title="重新生成"]').first()
  await expect(retried).toBeEnabled({ timeout: 180_000 })    // 重试回合收敛
  await expect(page.locator('[data-test="turn-failure"]')).toHaveCount(0)
  await expect(banner).toHaveCount(0)

  // ③ 独立撤销（/undo 本地命令，无重发跟随）→ 重做闭环
  await page.locator('.composer-input textarea').fill('/undo')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(banner).toBeVisible({ timeout: 15_000 })      // 独立撤销横幅稳定在
  await expect(retried).toHaveCount(0)                       // 助手回合已被撤
  await expect(page.locator('.composer-input textarea'))
    .toHaveValue(/请直接回复五个字符/, { timeout: 10_000 })   // 文本装回输入框
  await page.locator('[data-test="redo-btn"]').click()       // 重做（此刻可点）
  await expect(banner).toHaveCount(0)                        // 横幅消失
  await expect(retried).toHaveCount(1, { timeout: 15_000 })  // 被撤消息恢复
})
