/**
 * 子任务性能报告 E2E（2026-10-15）：真实批任务 → 批管理页「性能」入口 →
 * 报告分区（最慢调用 Top / 推理 Top / 失败 / 重复 / 子代理）+ 复制 Markdown。
 * 数据现场生成（read+write 的小批任务，真 LLM），断言不依赖历史数据。
 */
import { test, expect } from '@playwright/test'
import { adminTokenCached, tag } from './batch/toolbox'
import {
  uploadStaging, createBatch, cleanupBatch, waitBatchTerminal,
} from './batch/batch-helpers'
import { gotoWithAuth, screenshot } from './helpers'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })
test.setTimeout(600_000)

test('子任务性能报告：入口 / 分区 / 复制 Markdown', async ({ page }) => {
  const tk = await adminTokenCached()
  const name = tag('perf-report')
  const staged = await uploadStaging(tk, 'in.txt', 'hello perf\nsecond line\n',
                                     `e2e-perf-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '分两步完成：(1) 先用 read 工具读取 uploads/in.txt；'
      + '(2) 再用 write 工具在 outputs/out.md 写入该文件的内容。完成后回复「完成」。',
    files: [staged],
  })
  const bid = created.batchId || created.batch?.id
  try {
    await waitBatchTerminal(tk, bid, 480_000, 5000)

    // 批管理页 → 搜索到本批 → 详情
    await gotoWithAuth(page, '/admin/ai-execution?tab=batches')
    await page.getByPlaceholder('批任务名称').fill(name)
    const row = page.locator('tr', { hasText: name }).first()
    await expect(row).toBeVisible({ timeout: 20_000 })
    await row.getByRole('button', { name: '详情' }).click()

    // 子任务行 → 性能
    const childRow = page.locator('tr', { hasText: 'in.txt' }).first()
    await expect(childRow).toBeVisible({ timeout: 20_000 })
    await childRow.getByRole('button', { name: '性能' }).click()

    const drawer = page.locator('.el-drawer').filter({ hasText: '子任务性能报告' })
    await expect(drawer).toBeVisible({ timeout: 15_000 })
    await expect(drawer).toContainText('概要')

    // 最慢调用 Top：read/write 至少一行（真数据）
    const topRows = drawer.locator('[data-test="top-calls"] .el-table__body tr')
    await expect(topRows.first()).toBeVisible({ timeout: 15_000 })
    expect(await topRows.count()).toBeGreaterThanOrEqual(1)

    // 失败 / 重复分区存在（本任务应无失败/重复，显示空提示）
    await expect(drawer).toContainText('失败工具调用')
    await expect(drawer).toContainText('重复调用')
    await screenshot(page, 'subtask-perf-report')

    // 复制 Markdown → 剪贴板内容含报告骨架
    await drawer.locator('[data-test="copy-md"]').click()
    await expect(page.locator('.el-message').last())
      .toContainText('已复制', { timeout: 10_000 })
    const text = await page.evaluate(() => navigator.clipboard.readText())
    expect(text).toContain('# 子任务性能报告')
    expect(text).toContain('## 最慢工具调用 Top 10')
    expect(text).toContain('## 推理耗时 Top 10')
    expect(text).toContain('## 失败工具调用')
    expect(text).toContain('## 重复调用')
  } finally {
    await cleanupBatch(tk, bid)
  }
})
