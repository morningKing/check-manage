/**
 * AI 会话 @ 联想服务端文件搜索 E2E —— 真实链路：真实前端 + 真实后端
 * GET /ai/chat/sessions/:id/files/search 端点 + 真实磁盘工作区。
 *
 * 根因背景：list_session_files 整棵跳过嵌套 git 仓库（改动归「变更文件」面板），
 * 而 AGENTS.md 引导 agent 把代码 clone 到工作区子目录 —— 工作区主要内容
 * （克隆代码树）因此从不出现在 @ 联想里。修复走服务端搜索端点（含嵌套仓库）。
 *
 * 种子纪律：AITEST 前缀 + finally cleanupSessionsByPrefix + 临时工作区删除。
 * 证据截图：e2e/screenshots/ai-full/mention-search-*.png。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { AUTH_FILE, openChatSession, screenshot } from '../ai-full/helpers'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from '../ai-full/db-helpers'

test.setTimeout(120_000)

/** 共享登录态过期兜底：登录页出现则 UI 登录并写回（同 plantuml spec）。 */
async function ensureLogin(page: import('@playwright/test').Page) {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  if (!(await userInput.isVisible({ timeout: 4_000 }).catch(() => false))) return
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /AI 助手/ }).waitFor({ timeout: 20_000 })
  const entries = await page.evaluate(() => {
    const out: Record<string, string> = {}
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)!
      out[k] = localStorage.getItem(k)!
    }
    return out
  })
  fs.writeFileSync(AUTH_FILE, JSON.stringify(entries))
}

/** 造一个「AGENTS.md 引导的标准工作区」：uploads 输入 + 克隆进来的代码仓库
 *  （自带 .git 目录）+ outputs/。返回 {wsPath, uploadName, repoFiles}。 */
function makeWorkspace(): { ws: string } {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'aitest-mention-ws-'))
  fs.mkdirSync(path.join(ws, 'uploads'), { recursive: true })
  fs.writeFileSync(path.join(ws, 'uploads', 'e2e-input.txt'), 'uploaded input', 'utf-8')
  fs.mkdirSync(path.join(ws, 'outputs'), { recursive: true })
  // 克隆仓库：.git 目录存在 ⇒ list_session_files 整棵跳过；搜索端点必须触及
  fs.mkdirSync(path.join(ws, 'cloned-repo', '.git'), { recursive: true })
  fs.mkdirSync(path.join(ws, 'cloned-repo', 'src'), { recursive: true })
  fs.writeFileSync(path.join(ws, 'cloned-repo', 'src', 'alpha.py'), 'x = 1', 'utf-8')
  fs.writeFileSync(path.join(ws, 'cloned-repo', 'README.md'), '# cloned', 'utf-8')
  return { ws }
}

async function paletteItemTexts(page: import('@playwright/test').Page): Promise<string[]> {
  return page.locator('.command-palette .palette-item').allInnerTexts()
}

/** 等会话真正打开（welcome 空态消失）。openChatSession 只等页面骨架 ——
 *  ?session= 直开是异步的，若在其落地前输入，AiChatView 的 activeId watch
 *  会清空输入框（切会话草稿不跨会话共享），palette 断言就会闪空。 */
async function waitForSessionOpen(page: import('@playwright/test').Page): Promise<void> {
  await page.locator('.ai-chat__welcome').waitFor({ state: 'detached', timeout: 30_000 })
}

test('@ 联想可搜到克隆仓库文件并采纳插入', async ({ page }) => {
  const { ws } = makeWorkspace()
  const sid = await seedPlainSession({
    key: newId('mention-'),
    workspacePath: ws,
    messages: [{ role: 'user', text: '帮我看看 cloned-repo 的代码' }],
  })
  try {
    await ensureLogin(page)
    await openChatSession(page, sid)
    const composer = page.locator('.composer-input')
    await expect(composer).toBeVisible({ timeout: 30_000 })
    await waitForSessionOpen(page)

    // 裸 @ 唤起：本地过滤即时显示（上传文件），防抖后服务端搜索整组替换，
    // 克隆仓库文件必须出现在候选里 —— 修复前这里只有 uploads + AGENTS.md
    await composer.click()
    await composer.pressSequentially('@', { delay: 40 })
    await expect(page.locator('.command-palette')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.command-palette .palette-item', {
      hasText: 'cloned-repo/src/alpha.py',
    })).toBeVisible({ timeout: 15_000 })
    const bareTexts = await paletteItemTexts(page)
    expect(bareTexts.some(t => t.includes('uploads/e2e-input.txt'))).toBe(true)
    await screenshot(page, 'mention-search-bare-at')

    // 输入过滤词：服务端评分搜索命中仓库深处文件
    await composer.pressSequentially('alpha', { delay: 40 })
    const item = page.locator('.command-palette .palette-item', { hasText: 'cloned-repo/src/alpha.py' })
    await expect(item).toBeVisible({ timeout: 15_000 })
    await screenshot(page, 'mention-search-filtered')

    // 采纳（点击候选）：token 原位替换为完整相对路径，且可被后端解析（chip 来源）。
    // 注意 .composer-input 落在 ElInput 根 div 上，取值要定位内层 textarea。
    await item.click()
    await expect(composer.locator('textarea')).toHaveValue(/@cloned-repo\/src\/alpha\.py/, { timeout: 5_000 })
    await screenshot(page, 'mention-search-accepted')
  } finally {
    cleanupSessionsByPrefix()
    fs.rmSync(ws, { recursive: true, force: true })
  }
})

test('命中不存在的名字时面板不显示仓库文件', async ({ page }) => {
  const { ws } = makeWorkspace()
  const sid = await seedPlainSession({
    key: newId('mention-neg-'),
    workspacePath: ws,
  })
  try {
    await ensureLogin(page)
    await openChatSession(page, sid)
    const composer = page.locator('.composer-input')
    await expect(composer).toBeVisible({ timeout: 30_000 })
    await waitForSessionOpen(page)
    await composer.click()
    await composer.pressSequentially('@zzz-not-exist', { delay: 40 })
    // 服务端评分无命中 → 文件组为空，palette 整体不出现（无 agent 名命中时）
    await expect(page.locator('.command-palette')).toHaveCount(0, { timeout: 15_000 })
  } finally {
    cleanupSessionsByPrefix()
    fs.rmSync(ws, { recursive: true, force: true })
  }
})
