/**
 * 权限授权气泡（external_directory ask）真实链路 E2E —— 普通交互会话。
 *
 * 背景（2026-10-14 生产实测）：OpenCode 的 permission.asked 经后端代理以
 * **命名 SSE 帧**下发；前端 createEventStream 的命名监听表漏了 permission.*，
 * 帧到达浏览器后被静默丢弃（onmessage 只收无名帧）。后果：回合被授权卡住，
 * 气泡在整个挂起期间都不出现，只有切走再切回/刷新（openSession 的 REST
 * 重水化）才补得出来——用户视角是「有时不出现、有时很慢、像卡死」。
 *
 * 本用例锁死修复后的行为：气泡必须在**不切换会话、不刷新页面**的前提下，
 * 跟随实时事件自行出现（时间上贴近询问本身），且三键应答能消除气泡。
 * 触发方式：让模型用 read 工具读取工作区外的真实文件（C:\Windows\...，
 * 实测 read 该路径必然触发 external_directory=ask）。
 *
 * 注意：本用例依赖真模型真的发起读取（提示词已强约束「必须调用 read，
 * 不要凭记忆回答」）。模型改答也必须失败——那意味着读没发生，触发不成立。
 */
import { test, expect } from '@playwright/test'
import { api, screenshot } from './helpers'

test.setTimeout(300_000)

test('普通会话：工作区外读取触发授权气泡，无需切换会话即可应答',
     async ({ page, request }) => {
  // 建独立会话（不污染其他用例；该会话内的拒绝记忆也不会跨会话）
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  expect(created.status, '创建会话').toBeLessThan(300)
  const sid = created.json.id

  // 真实 UI 登录（不依赖 e2e/.auth 的过期风险），再直开该会话
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForTimeout(2000)
  await page.goto(`/ai-chat?session=${sid}`)
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 60_000 })

  // 从 UI 发提示（与用户操作同路径），确保 SSE 订阅先于询问建立
  await input.fill(
    '必须真的调用 read 工具读取文件 C:\\Windows\\System32\\drivers\\etc\\hosts '
    + '的前 3 行，不要凭记忆回答，然后把读到的内容原样贴出来。')
  await page.getByRole('button', { name: '发送' }).click()

  // 关键断言：气泡自行出现（不切换会话、不刷新）。
  // 修复前：帧被丢，永远等不到 → 在此超时失败（实测 200s+ 无气泡）。
  const card = page.locator('[data-test="permission-card"]')
  await expect(card).toBeVisible({ timeout: 180_000 })
  await expect(card).toContainText('external_directory')
  await expect(card).toContainText('hosts')
  console.log('permission card text:', (await card.innerText()).replace(/\s+/g, ' '))
  await screenshot(page, 'permission-ask-card')

  // 应答「本次允许」→ 气泡消失（乐观清除 + permission.replied echo 双保险）
  await page.locator('[data-test="perm-once"]').click()
  await expect(card).toBeHidden({ timeout: 30_000 })
  await screenshot(page, 'permission-ask-answered')
})
