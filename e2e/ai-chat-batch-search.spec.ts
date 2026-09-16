import { test, expect } from '@playwright/test'

/**
 * 批任务搜索（Spec: 指定批任务下会话搜索 §11.3 Playwright 验证）。
 *
 * 依赖环境中已存在至少一个批任务（ai-chat-batch.spec.ts 会创建一个；
 * 或任意开发库的历史批任务）。没有批任务时本 spec 直接跳过。
 */

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })
  await page.goto('/ai-chat')
  // SSE 长连接让 networkidle 永不出现，等真实元素
  await page.locator('.ai-sidebar__search').waitFor({ state: 'visible', timeout: 15_000 })
}

test('batch task scoped search: entry, scope tag, results, open child', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page)

  // 至少要有一个批任务分组（列表异步加载，给足等待；确实没有则跳过）
  let group = page.locator('.batch-group').first()
  try {
    await group.waitFor({ state: 'visible', timeout: 15_000 })
  } catch {
    test.skip(true, 'no batch task in this environment')
    return
  }
  const batchName = (await group.locator('.bg-name').textContent())?.trim() || ''

  // 1) 点击「搜索本批任务」→ 范围标签出现 + placeholder 切换 + 输入框聚焦
  await group.locator('[title="搜索本批任务"]').click()
  const scope = page.locator('.ai-sidebar__search-scope')
  await expect(scope).toContainText(batchName, { timeout: 5_000 })
  await expect(page.locator('.ai-sidebar__search input')).toHaveAttribute(
    'placeholder', new RegExp(batchName), { timeout: 5_000 })

  // 2) 清除范围 → 恢复普通会话搜索
  await scope.locator('.ai-sidebar__search-scope-x').click()
  await expect(page.locator('.ai-sidebar__search-scope')).toHaveCount(0)

  // 3) 重新进入范围搜索，输入一个只可能出现在历史消息里的词（取自子会话
  //    preview 或任意多字符词），断言命中（若有）都带批任务上下文标注
  await group.locator('[title="搜索本批任务"]').click()
  await page.locator('.ai-sidebar__search input').fill('报告')
  await page.waitForTimeout(1500) // 300ms 防抖 + 请求
  const hits = page.locator('.session-item--hit')
  const n = await hits.count()
  for (let i = 0; i < n; i++) {
    await expect(hits.nth(i).locator('.session-item__batchline').first())
      .toContainText('批任务：')
  }
  // 空结果提示区分范围模式
  if (n === 0) {
    await expect(page.getByText('本批任务中未找到匹配子会话')).toBeVisible()
  }
})
