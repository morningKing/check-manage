/**
 * 记忆管理页（/admin/ai-memory）冒烟：配置读取 + 条目真链路（写入→搜索→删除）。
 *
 * 全程 0 LLM：verbatim 写入不经提炼（mem0 只做嵌入），嵌入走本地 Ollama、
 * 检索走本地 Chroma——不依赖外部 LLM 可用性。前提：部署 ai_settings 已启用
 * mem0（开发库默认开、provider=ollama）。mem0 数据目录按用户隔离，写入落在
 * admin 名下，用例末尾经 UI 删除链路清理。
 *
 * 顺带钉住迁移事实：AI 配置→模型与密钥页不再有 mem0 配置段（2026-11-23 迁往本页）。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth } from './helpers'

test.setTimeout(180_000)

const VERBATIM_TEXT = `E2E 记忆冒烟 ${Date.now()}：偏好表格形式的输出`

test('记忆管理页：配置读取/条目写入-搜索-删除真链路/模型页已无 mem0 段', async ({ page }) => {
  // ① 配置页签：dev mem0 已启用 → 开关为开、状态描述可见
  await gotoWithAuth(page, '/admin/ai-memory')
  await expect(page.locator('.memory-admin')).toBeVisible()
  await expect(page.locator('.el-tabs__item').filter({ hasText: '配置' })).toHaveClass(/is-active/)
  const switchInput = page.locator('[data-test="mem0-switch"] input')
  await expect(switchInput).toBeChecked({ timeout: 15_000 })
  await expect(page.getByText('数据目录')).toBeVisible()

  // ② 测试台：写入 verbatim 记忆（本地嵌入，0 LLM）
  await page.locator('.el-tabs__item').filter({ hasText: '测试台' }).click()
  const testUser = page.locator('[data-test="test-user"]')
  await testUser.click()
  await testUser.locator('input').fill('admin')
  // remote select 首个候选即 admin；:visible 过滤掉前序 select 的隐藏 popper
  const option = page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
    { hasText: 'admin' }).first()
  await expect(option).toBeVisible({ timeout: 15_000 })
  await option.click()

  await page.locator('[data-test="test-add-text"]').fill(VERBATIM_TEXT)
  // 开启「原样保存」——绕开 LLM 提炼，保证 0 LLM 且文本原样可回查
  const addForm = page.locator('.el-form', { hasText: '原样保存' }).last()
  await addForm.locator('.el-switch').click()
  await page.locator('[data-test="test-add-btn"]').click()
  await expect(page.locator('.el-message', { hasText: '已写入' }).first())
    .toBeVisible({ timeout: 30_000 })

  // 写入后自动回查：命中列表含原文，注入提示词原文块可见
  await expect(page.locator('.el-table__row', { hasText: VERBATIM_TEXT }).first())
    .toBeVisible({ timeout: 30_000 })
  const rendered = page.locator('[data-test="test-rendered"]')
  await expect(rendered).toContainText('长期记忆')
  await expect(rendered).toContainText(VERBATIM_TEXT)

  // ③ 记忆条目页签：同一用户能列出该条并经 UI 删除（真删 mem0）
  await page.locator('.el-tabs__item').filter({ hasText: '记忆条目' }).click()
  const entriesUser = page.locator('[data-test="entries-user"]')
  await entriesUser.click()
  await entriesUser.locator('input').fill('admin')
  const option2 = page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
    { hasText: 'admin' }).first()
  await expect(option2).toBeVisible({ timeout: 15_000 })
  await option2.click()
  await page.locator('[data-test="entries-load"]').click()

  const row = page.locator('.el-table__row', { hasText: VERBATIM_TEXT }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.locator('[data-test="entry-delete"]').click()
  const box = page.locator('.el-message-box')
  await expect(box).toBeVisible({ timeout: 10_000 })
  await expect(box).toContainText('确定删除')
  await box.locator('.el-button--primary').click()
  await expect(page.locator('.el-message', { hasText: '已删除' }).first())
    .toBeVisible({ timeout: 15_000 })
  // 删除后条目表重载即应清空——行定位器必须限定在条目表内（测试台页签的
  // 自动搜索结果表在 DOM 里保留着同文本行，全页定位器会误命中隐藏行）
  const rowInEntries = page.locator('[data-test="entries-table"] .el-table__row',
    { hasText: VERBATIM_TEXT })
  await expect(rowInEntries).toHaveCount(0, { timeout: 30_000 })

  // ④ 迁移事实：AI 配置→模型与密钥页不再有 mem0 配置段
  await gotoWithAuth(page, '/admin/ai-settings')
  await expect(page.locator('body')).not.toContainText('长期记忆 (mem0)')
})
