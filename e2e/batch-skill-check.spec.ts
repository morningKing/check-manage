import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

// 验证目标：批任务子会话是否注入并使用「AI 全局技能管理」中的技能。
// 做法：创建批任务，提示词要求"列出当前环境中所有可用的 skill"，
// 完成后打开子会话，抓取对话内容断言：
//  1) 出现「已注入全局技能」系统提示（注入发生）
//  2) 助手列出的技能里包含技能管理中已启用的 trace-analyzer
// 技能管理当前启用：trace-analyzer（磁盘存在）、writing-beats（DB 有记录但磁盘目录缺失，
// inject_global_skills 会跳过 —— 若助手只列 trace-analyzer 恰好印证注入来自磁盘目录）。
test('batch task lists available skills (global skill injection check)', async ({ page }) => {
  test.setTimeout(420_000)
  const batchName = `skill-check-${Date.now()}`
  const artifactDir = path.join(process.cwd(), 'test-results', 'batch-skill-check')
  fs.mkdirSync(artifactDir, { recursive: true })

  // 登录（同 ai-chat-smoke 模式）
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 10_000 })

  // 不等 networkidle —— SSE 长连接会让网络永不空闲
  await page.goto('/ai-chat')

  // 新版侧边栏：批任务分区头（.ai-sidebar__section-head）内含「新建」按钮
  const createBatchBtn = page.locator('.ai-sidebar__section-head', { hasText: '批任务' })
    .locator('button', { hasText: '新建' })
  await createBatchBtn.waitFor({ state: 'visible', timeout: 15_000 })
  await createBatchBtn.click()

  const dialog = page.getByRole('dialog', { name: '新建批任务' })
  await dialog.waitFor({ state: 'visible', timeout: 5_000 })

  await dialog.locator('input[data-test="name"]').fill(batchName)
  await dialog.locator('textarea[data-test="prompt"]').fill(
    '请列出你当前环境中所有可用的 skill（技能），给出每个技能的名称和用途描述。' +
    '只输出技能清单，不要执行其他任务。')

  // 批任务要求至少 1 个输入文件
  await dialog.locator('input[type="file"]').setInputFiles([
    { name: 'skill-probe.txt', mimeType: 'text/plain', buffer: Buffer.from('probe') },
  ])
  await expect(dialog.locator('.files')).toContainText('skill-probe.txt', { timeout: 8_000 })

  const createBtn = dialog.locator('button[data-test="create-btn"]')
  await expect(createBtn).toBeEnabled({ timeout: 8_000 })
  await createBtn.click()

  const group = page.locator('.batch-group', { hasText: batchName }).first()
  await group.waitFor({ state: 'visible', timeout: 10_000 })

  // 等批次进入终态（模型真实调用，预留 6 分钟）
  await page.waitForFunction((name) => {
    const groups = Array.from(document.querySelectorAll('.batch-group'))
    const g = groups.find(el => el.querySelector('.bg-name')?.textContent?.includes(name))
    const badge = g?.querySelector('.badge')
    return !!badge && ['completed', 'failed', 'partial'].some(
      s => badge.classList.contains(`badge--${s}`))
  }, batchName, { timeout: 360_000 })
  const statusText = await group.locator('.badge').innerText()

  // 展开批次并打开子会话
  const head = group.locator('.batch-group__head')
  for (let i = 0; i < 5 && (await group.locator('.bg-child').count()) === 0; i++) {
    await head.click()
    await page.waitForTimeout(500)
  }
  await expect(group.locator('.bg-child')).toHaveCount(1, { timeout: 10_000 })
  await group.locator('.bg-child').first().click()

  // 等子会话消息加载
  await expect(page.locator('.msg').first()).toBeVisible({ timeout: 20_000 })
  // 等助手侧消息（含 notice / 回答）出现
  await expect(page.locator('.msg--assistant, .msg--system').first())
    .toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(2_000) // 留一次 2.5s 轮询兜底刷新

  // 抓取完整对话
  const msgs = await page.locator('.msg').allInnerTexts()
  const convo = msgs.join('\n\n===== (message) =====\n\n')
  fs.writeFileSync(path.join(artifactDir, 'conversation.txt'),
    `批次: ${batchName}\n状态: ${statusText}\n\n${convo}`, 'utf-8')
  await page.screenshot({ path: path.join(artifactDir, 'batch-child-view.png'), fullPage: true })

  console.log('=== 批次状态:', statusText)
  console.log('=== 对话中是否出现「已注入全局技能」:', convo.includes('已注入全局技能'))
  console.log('=== 对话中是否提到 trace-analyzer:', convo.includes('trace-analyzer'))
  console.log('=== 对话中是否提到 writing-beats:', convo.includes('writing-beats'))
  console.log('=== 对话全文已保存到 test-results/batch-skill-check/conversation.txt')

  // 断言 1：注入提示存在（worker 侧 _persist_provision_notice），且包含技能管理中
  // 两个已启用的技能（root 统一后，批任务与聊天会话读同一个 global-skills 目录）
  const notice = msgs.find(m => m.includes('已注入全局技能')) || ''
  expect(notice, '子会话应有「已注入全局技能」提示').toContain('已注入全局技能')
  expect(notice, '注入提示应包含 trace-analyzer').toContain('trace-analyzer')
  expect(notice, '注入提示应包含 writing-beats').toContain('writing-beats')
  // 本批次未配置预置仓库：注入提示不应再误带「克隆失败」前缀
  expect(convo, '无预置仓库时不应出现克隆失败提示').not.toContain('预置仓库克隆失败')
  // 断言 2：助手清单里应列出技能（不能只在注入提示里出现）
  const assistantTexts = (await page.locator('.msg--assistant').allInnerTexts()).join('\n')
  expect(assistantTexts, '助手回答应列出 trace-analyzer').toContain('trace-analyzer')
  console.log('=== 助手回答是否列出 writing-beats:', assistantTexts.includes('writing-beats'))
})
