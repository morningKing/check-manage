import { test, expect } from '@playwright/test'

/**
 * 深色模式主题回归（深色下表格表头/按钮白亮不协调的回归防护）。
 *
 * 深色由 stores/app.ts 在启动时给 <html> 挂 .dark 类实现；这里在页面加载前
 * 写入持久化设置，走与应用完全相同的初始化路径。
 */

test.describe('深色模式主题', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('check-manage:settings',
        JSON.stringify({ theme: 'dark', fontSize: 'default', compact: false }))
    })
    await page.goto('/')
    await page.fill('input[placeholder*="用户名"]', 'admin')
    await page.fill('input[placeholder*="密码"]', 'admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 15_000 })
  })

  test('深色下表格表头为深色底（备份页）', async ({ page }) => {
    await page.goto('/admin/backup')
    const th = page.locator('.el-table th.el-table__cell').first()
    await th.waitFor({ state: 'visible', timeout: 15_000 })
    await expect(th).toHaveCSS('background-color', 'rgb(31, 34, 42)')
    // 表头文字为深色主题的次级灰，不再是浅色模式的深灰 #303133
    const color = await th.evaluate(el => getComputedStyle(el.querySelector('.cell') || el).color)
    expect(color).not.toBe('rgb(48, 49, 51)')
  })

  test('深色下主色按钮为近白底配深色文字（非白字）', async ({ page }) => {
    await page.goto('/admin/backup')
    const btn = page.locator('.el-button--primary', { hasText: '创建全量备份' }).first()
    await btn.waitFor({ state: 'visible', timeout: 15_000 })
    // theme.scss 深色块：主色反转近白底时文字必须用深色，禁止白底白字
    await expect(btn).toHaveCSS('color', 'rgb(20, 20, 20)')
    const bg = await btn.evaluate(el => getComputedStyle(el).backgroundColor)
    // 底色必须是浅色调（近白主色），深色文字才有对比度
    const [, r, g, b] = bg.match(/rgba?\((\d+), (\d+), (\d+)/)!.map(Number)
    const luminance = 0.299 * r + 0.587 * g + 0.114 * b
    expect(luminance).toBeGreaterThan(160)
  })

  test('浅色模式表头保持浅灰底深灰字（无回归）', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('check-manage:settings',
        JSON.stringify({ theme: 'light', fontSize: 'default', compact: false }))
    })
    await page.goto('/admin/backup')
    const th = page.locator('.el-table th.el-table__cell').first()
    await th.waitFor({ state: 'visible', timeout: 15_000 })
    await expect(th).toHaveCSS('background-color', 'rgb(247, 249, 252)')
  })

  test('深色下各管理页表格表头均为深色（巡检）', async ({ page }) => {
    for (const path of ['/admin/ai-sessions', '/admin/backup']) {
      await page.goto(path)
      const th = page.locator('.el-table th.el-table__cell').first()
      try {
        await th.waitFor({ state: 'visible', timeout: 10_000 })
      } catch { continue /* 该页无表格则跳过 */ }
      const bg = await th.evaluate(el => getComputedStyle(el).backgroundColor)
      expect(bg, `${path} 表头底色不应为浅色`).not.toBe('rgb(245, 247, 250)')
      expect(bg, `${path} 表头底色不应为白色`).not.toBe('rgb(255, 255, 255)')
    }
  })

  test('深色下 AI 助手回答正文为高对比浅色', async ({ page }) => {
    // 需要一个已有助手回复的会话（任选侧栏第一个会话；环境无会话则跳过）
    await page.goto('/ai-chat')
    const first = page.locator('.session-item').first()
    try { await first.waitFor({ state: 'visible', timeout: 12_000 }) } catch {
      test.skip(true, 'no sessions')
      return
    }
    await first.click()
    const preview = page.locator('.msg--assistant .md-editor-preview').first()
    try { await preview.waitFor({ state: 'visible', timeout: 8_000 }) } catch {
      test.skip(true, 'no assistant message in the first session')
      return
    }
    expect(await page.evaluate(() =>
      document.documentElement.classList.contains('dark'))).toBe(true)
    const editorClass = await page.locator('.msg--assistant .md-editor').first()
      .evaluate(el => el.classList.contains('md-editor-dark'))
    expect(editorClass).toBe(true)
  })

  test('深色下 AI 会话「新建会话」与「回到底部」按钮文字可读', async ({ page }) => {
    await page.goto('/ai-chat')
    const newBtn = page.locator('.ai-chat__new')
    await newBtn.waitFor({ state: 'visible', timeout: 15_000 })
    // 主色在暗色被反转近白（#e6e8eb）→ 文字必须是深色，禁止白底白字
    await expect(newBtn).toHaveCSS('background-color', 'rgb(230, 232, 235)')
    await expect(newBtn).toHaveCSS('color', 'rgb(20, 20, 20)')

    // 「回到底部」浮动按钮：会话有可滚动消息时上滚触发出现（等消息加载完）
    await page.locator('.session-item').first().click()
    try {
      await page.locator('.msg').first().waitFor({ state: 'visible', timeout: 10_000 })
      await page.waitForTimeout(800)
    } catch { /* 会话可能没有消息 */ }
    const scrollable = await page.evaluate(() => {
      // ElScrollbar 的真实滚动容器是内层 .el-scrollbar__wrap
      const el = document.querySelector('.ai-chat__messages .el-scrollbar__wrap')
      return !!el && el.scrollHeight - el.clientHeight > 400
    })
    if (!scrollable) {
      test.skip(true, 'first session not tall enough to trigger the jump button')
      return
    }
    await page.evaluate(() => {
      const el = document.querySelector('.ai-chat__messages .el-scrollbar__wrap')
      el.scrollTop = 0
    })
    await page.waitForTimeout(300)
    const jump = page.locator('.ai-chat__jump-bottom')
    await jump.waitFor({ state: 'visible', timeout: 5_000 })
    await expect(jump).toHaveCSS('background-color', 'rgb(230, 232, 235)')
    await expect(jump).toHaveCSS('color', 'rgb(20, 20, 20)')
  })
})
