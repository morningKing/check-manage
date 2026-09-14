import { test, expect } from '@playwright/test'

/**
 * Task 7（Langfuse Agent Observability）端到端验证。
 *
 * 环境约束与范围（对应计划 Step 1-4 的环境相关说明）：
 * - 本环境没有 Docker/自托管 Langfuse 栈，Playwright 跑在 LANGFUSE_ENABLED=false
 *   的默认部署上。因此这里确定性验证：
 *   (1) 禁用态「查看 Trace」不出现（不存在任何出站 Langfuse 依赖）；
 *   (2) Langfuse 关闭时对话/批任务链路照常可用（失败模式隔离的浏览器级证明，
 *       与 server/tests/test_langfuse_integration.py 的服务端隔离测试互补）。
 * - 启用态的正路径（点开链接落到自托管 URL）由后端路由测试
 *   （test_routes_ai_chat.py::test_get_messages_returns_trace_metadata_when_langfuse_is_enabled，
 *   断言完整 traceUrl 构造）+ TraceLink 组件测试 + 集成测试覆盖。若部署了
 *   LANGFUSE_E2E=1 + 启用态后端（见 spec 内 skip 分支的启动说明），正路径
 *   浏览器断言会自动启用。
 */

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })
}

test('Langfuse 禁用（默认）：不出现「查看 Trace」，对话链路照常可用', async ({ page }) => {
  test.setTimeout(180_000)
  await login(page)
  await page.goto('/ai-chat')

  // 输入区可用 = 会话已就绪
  const input = page.getByPlaceholder(/给 AI 助手发消息|输入消息/)
  await input.waitFor({ state: 'visible', timeout: 15_000 })

  // 禁用态：任何会话/任何时机都不渲染「查看 Trace」入口
  await expect(page.locator('.ai-chat__trace-action')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '查看 Trace' })).toHaveCount(0)

  // 发一轮真实对话：Langfuse 关闭时聊天必须完全不受影响（失败模式隔离）
  await input.fill('e2e-langfuse-disabled 冒烟：请回复“收到”两个字')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--assistant').first())
    .toBeVisible({ timeout: 120_000 })

  // 回合结束后依旧不出现 trace 入口（禁用态永远隐藏）
  await expect(page.locator('.ai-chat__trace-action')).toHaveCount(0)
})

test('Langfuse 启用态：回合后出现指向自托管地址的外链（需启用栈）', async ({ page }) => {
  // 环境门控：仅当 e2e 显式提供启用态后端时运行。
  // 启动方式（计划 Step 1 的替代实现，避免依赖 Docker）：
  //   后端 env: LANGFUSE_ENABLED=true LANGFUSE_HOST=<https 自托管地址>
  //             LANGFUSE_PROJECT_ID=<pid> LANGFUSE_PUBLIC_KEY=pk-...
  //             LANGFUSE_SECRET_KEY=sk-...（仅进程环境，绝不入库/入仓）
  //   并设 LANGFUSE_E2E_ENABLED=1 与 LANGFUSE_E2E_HOST=<同一自托管地址>
  if (process.env.LANGFUSE_E2E_ENABLED !== '1') {
    test.skip(true, '本环境无自托管 Langfuse 栈；正路径由路由级/组件级测试覆盖')
  }
  const host = process.env.LANGFUSE_E2E_HOST || ''
  test.expect(host, 'LANGFUSE_E2E_HOST must be set').toBeTruthy()

  await login(page)
  await page.goto('/ai-chat')
  const input = page.getByPlaceholder(/给 AI 助手发消息|输入消息/)
  await input.waitFor({ state: 'visible', timeout: 15_000 })

  await input.fill('e2e-langfuse-enabled 冒烟：请回复“收到”两个字')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--assistant').first())
    .toBeVisible({ timeout: 120_000 })

  // 启用 + 已采样 + 已配置 project → 回合后出现外链，指向自托管 host，
  // 且新标签页打开（target=_blank）、不泄漏任何密钥
  const link = page.getByRole('link', { name: '查看 Trace' }).first()
  await expect(link).toBeVisible({ timeout: 30_000 })
  const href = await link.getAttribute('href')
  expect(href).toBeTruthy()
  expect(href!).toContain(host.replace(/\/$/, ''))
  await expect(link).toHaveAttribute('target', '_blank')
  const pageContent = await page.content()
  expect(pageContent).not.toContain('sk-')
})
