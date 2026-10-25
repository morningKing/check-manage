/**
 * 插件开关全链路 E2E（plugin-architecture spec §8 验收）——真栈真库：
 * 管理开关 API 即时生效 → 菜单入口消失（menus API 过滤）→ 直达路由 404
 * （守卫 fail-closed）→ 后端 API 404 PLUGIN_DISABLED（蓝图门）→ se 文档
 * 数据保留（禁用不删数据）→ 重新启用全恢复。
 *
 * 确定性：全程无 LLM（se 文档 CRUD 是纯 DB/文件操作，不打开编辑器 WS 页）。
 * 清理纪律：finally 恢复 enabled=true + 删 AITEST 文档。
 */
import { test, expect } from '@playwright/test'
import { api, gotoWithAuth, screenshot } from './ai-full/helpers'
import { dbSeed } from './ai-full/batch/toolbox'

test.setTimeout(120_000)

test('插件开关全链路：smart-writing 禁用/启用（菜单/路由/API/数据保留）', async ({ page, request }) => {
  // 菜单名是 DB 数据（迁移种子行 menu-suggest-editor）——动态取，不硬编码
  const menuRows = dbSeed("SELECT name FROM menus WHERE path LIKE '/suggest-editor%' LIMIT 1")
  expect(menuRows.length, '侧栏菜单行应存在（迁移种子）').toBe(1)
  const menuName = String(menuRows[0][0])

  // 0. 前置恢复启用 + 种 AITEST 文档
  const reset = await api(request, 'put', '/plugins/smart-writing', { enabled: true })
  expect(reset.status).toBe(200)
  const created = await api(request, 'post', '/suggest-editor/documents',
    { title: 'AITEST-PLUGIN-E2E 文档' })
  expect(created.status, JSON.stringify(created.json)).toBe(201)
  const docId = created.json.document.id as string

  try {
    // 1. 基线：菜单入口可见
    await gotoWithAuth(page, '/home')
    const menuItem = page.locator('.el-menu-item', { hasText: menuName }).first()
    await expect(menuItem).toBeVisible({ timeout: 15_000 })

    // 2. 禁用 → 重新加载（菜单与插件 store 随 app init 重建）→ 入口消失
    const off = await api(request, 'put', '/plugins/smart-writing', { enabled: false })
    expect(off.status).toBe(200)
    await page.reload()
    await expect(page.locator('.el-menu-item', { hasText: menuName })).toHaveCount(0)

    // 3. 直达路由 → 守卫拦到 NotFound
    await page.goto('/suggest-editor')
    await expect(page.locator('.not-found')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.error-message')).toHaveText('页面不存在')
    await screenshot(page, 'plugin-toggle-off')

    // 4. 后端 API 门：404 PLUGIN_DISABLED
    const gated = await api(request, 'get', '/suggest-editor/documents')
    expect(gated.status).toBe(404)
    expect(gated.json.error).toEqual({ code: 'PLUGIN_DISABLED', plugin: 'smart-writing' })

    // 5. 数据保留：禁用不删 se 文档
    const kept = dbSeed(`SELECT count(*) FROM se_documents WHERE id = '${docId}'`)
    expect(Number(kept[0][0])).toBe(1)

    // 6. 重新启用 → 菜单恢复 + 列表加载 + 文档可见
    const on = await api(request, 'put', '/plugins/smart-writing', { enabled: true })
    expect(on.status).toBe(200)
    await page.reload()
    await expect(page.locator('.el-menu-item', { hasText: menuName }).first())
      .toBeVisible({ timeout: 15_000 })
    await gotoWithAuth(page, '/suggest-editor')
    await expect(page.getByText('AITEST-PLUGIN-E2E 文档').first())
      .toBeVisible({ timeout: 15_000 })
    await screenshot(page, 'plugin-toggle-on')
  } finally {
    await api(request, 'put', '/plugins/smart-writing', { enabled: true })
    await api(request, 'delete', `/suggest-editor/documents/${docId}`).catch(() => { /* 已删则忽略 */ })
  }
})
