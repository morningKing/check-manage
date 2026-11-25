import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import { dbSeed } from './ai-full/batch/toolbox'

/**
 * AI 会话页深色模式对比度回归（2026-11-25 用户报告三处「看不清」）：
 *   1. 变更文件面板/抽屉的文件路径（.change-file__name / .diff-drawer__title）
 *   2. 侧栏批任务任务名（.bg-name）
 *   3. 批任务子会话名（.bg-child__file）
 *
 * 根因：三者均无显式 color，继承 body 的浅色模式编译色 #303133；
 * 全仓没有 html.dark body 覆盖，深色底 #1c1f26 上对比度 ≈1.2 几乎不可见。
 * 修复 = 显式 var(--el-text-color-primary)（CSS 变量自带深浅适配）。
 *
 * 真实链路：真实 dev 栈（playwright webServer 自动拉起 npm run dev:all）+ 真登录 +
 * DB 种 completed 批 + 子会话（AITEST-SESSV2 前缀，afterAll 清理）+ 真实 git 工作区
 * （scratch 仓：已提交文件改动 + 未跟踪新文件 → 面板真出「修改/新增」行）。
 * 浏览器 getComputedStyle 取真实计算色算 WCAG 对比度，断言 ≥ 4.5；
 * 另跑一遍浅色模式锁「修复不伤浅色」。
 */

const PREFIX = 'AITEST-SESSV2'
const BATCH_NAME = `${PREFIX}-dark-对比度批`
const CHILD_FILE = '样例数据.csv'

let workspace = ''

function newId(prefix: string): string {
  return prefix + crypto.randomBytes(6).toString('hex')
}

/** 造一个真实 git 工作区：已提交 base.md 后再改一行 + 未跟踪 new-report.md → 修改/新增两组 */
function makeGitWorkspace(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aitest-dark-'))
  execFileSync('git', ['init'], { cwd: dir })
  execFileSync('git', ['config', 'user.email', 'aitest@example.com'], { cwd: dir })
  execFileSync('git', ['config', 'user.name', 'aitest'], { cwd: dir })
  fs.writeFileSync(path.join(dir, 'base.md'), '# 基线\n\n第一版内容。\n')
  execFileSync('git', ['add', '.'], { cwd: dir })
  execFileSync('git', ['commit', '-m', 'init'], { cwd: dir })
  fs.writeFileSync(path.join(dir, 'base.md'), '# 基线\n\n第二版内容（已修改）。\n')
  fs.writeFileSync(path.join(dir, 'new-report.md'), '# 新报告\n\n未跟踪新文件。\n')
  return dir
}

function seed(): void {
  const bid = newId('batch_')
  const sid = newId('sess_')
  const ws = workspace.replace(/\\/g, '/')
  dbSeed(`INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total) VALUES ('${bid}', 'user-admin', '${BATCH_NAME}', 'e2e seed', 'completed', 1)`)
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status, batch_id, batch_seq, batch_input_file, workspace_path) VALUES ('${sid}', 'user-admin', '${PREFIX}-dark-子会话', 'completed', '${bid}', 1, '${CHILD_FILE}', '${ws}')`)
}

/** 浏览器内计算：元素文字色 vs 有效背景色（向上找第一个非透明底）的 WCAG 对比度 */
function probeSelector(page: import('@playwright/test').Page, selector: string) {
  return page.evaluate((sel) => {
    const rgb = (c: string): [number, number, number, number] => {
      const m = c.match(/rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/)
      if (!m) return [255, 255, 255, 1]
      return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])]
    }
    const lum = (rgb3: [number, number, number]) => {
      const f = (c: number) => {
        const v = c / 255
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
      }
      return 0.2126 * f(rgb3[0]) + 0.7152 * f(rgb3[1]) + 0.0722 * f(rgb3[2])
    }
    const contrast = (l1: number, l2: number) => {
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
      return (hi + 0.05) / (lo + 0.05)
    }
    const bgOf = (el: Element): [number, number, number] => {
      let cur: Element | null = el
      while (cur) {
        const [r, g, b, a] = rgb(getComputedStyle(cur).backgroundColor)
        if (a >= 0.9) return [r, g, b]
        cur = cur.parentElement
      }
      return [28, 31, 38]
    }
    const els = Array.from(document.querySelectorAll(sel))
    return els.map((el) => {
      const cs = getComputedStyle(el)
      const fg = rgb(cs.color)
      const bg = bgOf(el)
      return {
        text: (el.textContent || '').trim().slice(0, 24),
        color: cs.color,
        bg: `rgb(${bg.join(',')})`,
        contrast: Math.round(contrast(lum([fg[0], fg[1], fg[2]]), lum(bg)) * 100) / 100,
      }
    })
  }, selector)
}

async function loginWithTheme(page: import('@playwright/test').Page, theme: 'dark' | 'light') {
  await page.addInitScript((t) => {
    localStorage.setItem('check-manage:settings',
      JSON.stringify({ theme: t, fontSize: 'default', compact: false }))
  }, theme)
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 15_000 })
}

async function openAiChat(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /AI 助手/ }).click()
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 30_000 })
  // 批任务列表首载偶发失败（快照可见会话列表正常但批区「暂无批任务」，
  // 疑似 SSE 钉线程/大会话量下的偶发请求失败）——未见种子批则整页重载重试
  let lastErr: unknown
  for (let i = 0; i < 4; i++) {
    try {
      await expect(page.locator('.bg-name', { hasText: 'dark-对比度批' }).first())
        .toBeVisible({ timeout: 12_000 })
      return
    } catch (e) {
      lastErr = e
      if (i < 3) {
        await page.reload()
        await input.waitFor({ state: 'visible', timeout: 30_000 })
      }
    }
  }
  throw lastErr
}

test.describe('AI 会话深色对比度回归（变更文件路径 / 批任务名 / 子会话名）', () => {
  test.beforeAll(() => {
    workspace = makeGitWorkspace()
    seed()
  })

  test.afterAll(() => {
    dbSeed(`DELETE FROM ai_chat_sessions WHERE title LIKE '${PREFIX}-%'`)
    dbSeed(`DELETE FROM ai_chat_batches WHERE name LIKE '${PREFIX}-%'`)
    if (workspace) fs.rmSync(workspace, { recursive: true, force: true })
  })

  test('深色：三处文字 WCAG 对比度 ≥ 4.5', async ({ page }) => {
    test.setTimeout(180_000)
    await loginWithTheme(page, 'dark')
    await openAiChat(page)

    // ① 批任务任务名
    const name = page.locator('.bg-name', { hasText: 'dark-对比度批' }).first()
    await expect(name).toBeVisible({ timeout: 20_000 })
    const nameRows = await probeSelector(page, '.bg-name')
    const nameRow = nameRows.find((r) => r.text.includes('dark-对比度批'))
    expect(nameRow, '批任务名应在 DOM 中').toBeTruthy()
    expect(nameRow!.contrast, `批任务名对比度不足: ${JSON.stringify(nameRow)}`)
      .toBeGreaterThanOrEqual(4.5)
    await page.screenshot({ path: 'e2e/screenshots/dark-contrast-batch-name.png' })

    // ② 子会话名：展开批组
    await page.locator('.batch-group__head', { hasText: 'dark-对比度批' }).first().click()
    const child = page.locator('.bg-child__file', { hasText: CHILD_FILE })
    await expect(child).toBeVisible({ timeout: 20_000 })
    const childRows = await probeSelector(page, '.bg-child__file')
    const childRow = childRows.find((r) => r.text.includes(CHILD_FILE))
    expect(childRow, '子会话名应在 DOM 中').toBeTruthy()
    expect(childRow!.contrast, `子会话名对比度不足: ${JSON.stringify(childRow)}`)
      .toBeGreaterThanOrEqual(4.5)

    // ③ 变更文件面板文件路径：点开子会话使其成为 activeId
    await page.locator('.bg-child', { hasText: CHILD_FILE }).first().click()
    await page.locator('.ai-changes').waitFor({ state: 'visible', timeout: 15_000 })
    const refresh = page.locator('.ai-changes__refresh')
    if (await refresh.count()) await refresh.click()
    const changeName = page.locator('.change-file__name', { hasText: 'base.md' })
    await expect(changeName.first()).toBeVisible({ timeout: 20_000 })
    const changeRows = await probeSelector(page, '.change-file__name')
    expect(changeRows.length, '变更文件路径应有可见元素').toBeGreaterThan(0)
    for (const r of changeRows) {
      expect(r.contrast, `变更文件路径对比度不足: ${JSON.stringify(r)}`)
        .toBeGreaterThanOrEqual(4.5)
    }
    await page.screenshot({ path: 'e2e/screenshots/dark-contrast-changes-panel.png' })

    // ④ 变更文件抽屉头部的文件路径（EP 变量本就适配，锁定防回归）
    await changeName.first()
      .locator('xpath=ancestor::div[contains(@class,"change-file")]')
      .getByRole('button', { name: '预览' }).click()
    const drawerTitle = page.locator('.diff-drawer__title')
    await expect(drawerTitle).toBeVisible({ timeout: 20_000 })
    const titleRows = await probeSelector(page, '.diff-drawer__title')
    expect(titleRows.length, '抽屉标题应可见').toBeGreaterThan(0)
    for (const r of titleRows) {
      expect(r.contrast, `抽屉文件路径对比度不足: ${JSON.stringify(r)}`)
        .toBeGreaterThanOrEqual(4.5)
    }
    await page.keyboard.press('Escape')
  })

  test('浅色：同三处仍可读（修复不伤浅色模式）', async ({ page }) => {
    test.setTimeout(180_000)
    await loginWithTheme(page, 'light')
    await openAiChat(page)

    // ① 批任务名
    await expect(page.locator('.bg-name', { hasText: 'dark-对比度批' }).first())
      .toBeVisible({ timeout: 20_000 })
    const nameRows = await probeSelector(page, '.bg-name')
    const nameRow = nameRows.find((r) => r.text.includes('dark-对比度批'))
    expect(nameRow, '批任务名应在 DOM 中').toBeTruthy()
    expect(nameRow!.contrast, `浅色批任务名对比度不足: ${JSON.stringify(nameRow)}`)
      .toBeGreaterThanOrEqual(4.5)

    // ② 展开批组 → 子会话名
    await page.locator('.batch-group__head', { hasText: 'dark-对比度批' }).first().click()
    await expect(page.locator('.bg-child__file', { hasText: CHILD_FILE }).first())
      .toBeVisible({ timeout: 20_000 })
    const childRows = await probeSelector(page, '.bg-child__file')
    const childRow = childRows.find((r) => r.text.includes(CHILD_FILE))
    expect(childRow, '子会话名应在 DOM 中').toBeTruthy()
    expect(childRow!.contrast, `浅色子会话名对比度不足: ${JSON.stringify(childRow)}`)
      .toBeGreaterThanOrEqual(4.5)

    // ③ 点开子会话 → 变更文件面板路径
    await page.locator('.bg-child', { hasText: CHILD_FILE }).first().click()
    await page.locator('.ai-changes').waitFor({ state: 'visible', timeout: 15_000 })
    const refresh = page.locator('.ai-changes__refresh')
    if (await refresh.count()) await refresh.click()
    await expect(page.locator('.change-file__name', { hasText: 'base.md' }).first())
      .toBeVisible({ timeout: 20_000 })
    const changeRows = await probeSelector(page, '.change-file__name')
    expect(changeRows.length, '变更文件路径应有可见元素').toBeGreaterThan(0)
    for (const r of changeRows) {
      expect(r.contrast, `浅色变更文件路径对比度不足: ${JSON.stringify(r)}`)
        .toBeGreaterThanOrEqual(4.5)
    }
  })
})
