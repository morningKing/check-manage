/**
 * 运行日志查看器 E2E（2026-10-14 spec §6 真实链路）：真页面 + 真文件 + 真轮询。
 * 标记行走 fs 直写 server/ai-chat.log——后端 logging 以 append 打开，
 * 外部追加对已打开句柄可见（Python 日志句柄同文件追加）。
 * 轮转场景不做 E2E（Windows 重命名被占日志文件不可靠，单测覆盖，spec §6）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gotoWithAuth, screenshot } from './helpers'

const DIR = path.dirname(fileURLToPath(import.meta.url))
const LOG = path.join(DIR, '../../server/ai-chat.log')

function appendMarker(text: string) {
  fs.appendFileSync(LOG, `\n${text}\n`, 'utf-8')
}

test('运行日志页：跟尾出现 / 暂停停刷 / 继续补拉 / 筛选计数', async ({ page }) => {
  test.setTimeout(180_000)
  const tk = `E2E-LOGVIEW-${Date.now()}`
  appendMarker(`${tk}-A`)

  await gotoWithAuth(page, '/admin/logs')
  const pane = page.locator('[data-test="log-pane"]')
  await expect(pane).toBeVisible({ timeout: 30_000 })
  await expect(pane).toContainText(`${tk}-A`, { timeout: 15_000 })
  await screenshot(page, 'admin-log-viewer-live')

  // 暂停：新行不出现；继续：补拉出现
  await page.locator('[data-test="pause-btn"]').click()
  appendMarker(`${tk}-B`)
  await page.waitForTimeout(4_000)
  await expect(pane).not.toContainText(`${tk}-B`)
  await page.locator('[data-test="pause-btn"]').click()
  await expect(pane).toContainText(`${tk}-B`, { timeout: 15_000 })

  // 筛选：命中显示、无匹配计 0
  await page.locator('[data-test="filter-input"] input').fill(tk)
  await expect(pane).toContainText(`${tk}-A`)
  await expect(page.locator('.log-viewer__status')).toContainText('命中 2 /')
  await page.locator('[data-test="filter-input"] input').fill('zzz-not-exist-zzz')
  await expect(page.locator('.log-viewer__status')).toContainText('命中 0')
  await page.locator('[data-test="filter-input"] input').fill('')

  // 文件切换：chat 缓冲清空 + oc-serve.log 页签选中并真实走一轮请求
  await page.locator('[data-test="file-serve"]').click()
  await expect(pane).toBeVisible()
  // 缓冲已重置：仅 chat 文件有的本用例标记行不得再出现（内容无关的切换证据）
  await expect(pane).not.toContainText(tk)
  // 页签选中态：ElRadioButton 根 <label> 选中时带 is-active（attrs 透传到该 label）
  await expect(page.locator('[data-test="file-serve"]')).toHaveClass(/is-active/)
  await expect(page.locator('.log-viewer__status')).toContainText(/实时|等待文件|连接中断/)
})
