/**
 * 智能写作 · 易用性补齐 E2E（2026-10-26）：
 *  U1 字数统计（右下角 HUD，CJK 逐字 + 英文单词口径）
 *  U2 查找替换（Ctrl+F 面板：命中计数/高亮/替换单处/全部替换）
 *  U3 撤销/重做独立按钮
 *  U4 快捷键帮助（Ctrl+/ 与「⋯」菜单两个入口）
 *  U5 AI 改写入口（顶栏按钮 + Alt+A）与新预设（扩写/纠正语病/翻译成英文…）
 *  U6 目录导航（HUD 目录弹层 + 点击滚动定位）
 *
 * 前置：mock 栈（npm run test:e2e 的 dev:all → se:mock）——AI 改写用 mock 确定性
 * 响应（rewrite 按规则清理，「但是」→「然而」）。跑前先停生产栈：
 * AGENT_KIND=opencode 的 SE 会让改写/分析永远等不到结果。
 */
import { test, expect, type Page } from '@playwright/test'
import {
  createDocument,
  deleteDocument,
  ensureSharedLogin,
  installSharedAuth,
  openDocById,
  readSharedToken,
  typeAtEnd,
} from './helpers/suggest-editor-quality'

test.beforeAll(async ({ browser }) => {
  await ensureSharedLogin(browser)
})

test.beforeEach(async ({ context }) => {
  await installSharedAuth(context)
})

/** 在正文末段做全选（点进段落 → Home → Shift+End），供 AI 改写入口用 */
async function selectLastParagraph(page: Page): Promise<void> {
  await page.locator('.doc-surface p').last().click()
  await page.keyboard.press('Home')
  await page.keyboard.press('Shift+End')
}

test('U1 字数统计：随输入实时更新，CJK 逐字 + 英文单词', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-字数-${Date.now()}`)
  try {
    await openDocById(page, docId)
    const counter = page.locator('.se-doc-hud-count')
    await expect(counter).toHaveText('0 字', { timeout: 10_000 })

    await typeAtEnd(page, '智能写作字数')
    await expect(counter).toHaveText('6 字', { timeout: 10_000 })

    await typeAtEnd(page, 'test')
    await expect(counter).toHaveText('7 字', { timeout: 10_000 })
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U2 查找替换：命中计数与高亮、替换单处、全部替换', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-查找-${Date.now()}`)
  try {
    await openDocById(page, docId)
    await typeAtEnd(page, '设备检查记录：设备检查完成。')

    await page.keyboard.press('Control+f')
    const panel = page.locator('.se-find-panel')
    await expect(panel).toBeVisible({ timeout: 5_000 })

    await panel.locator('.se-find-input').first().fill('设备检查')
    await expect(page.locator('.se-find-hit')).toHaveCount(2, { timeout: 5_000 })
    await expect(page.locator('.se-find-current')).toHaveCount(1)
    await expect(panel.locator('.se-find-count')).toHaveText('1/2')

    // 填替换词 → 替换当前命中
    await panel.locator('input[placeholder="替换为…"]').fill('巡检')
    await panel.getByRole('button', { name: '替换', exact: true }).click()
    await expect(page.locator('.doc-surface')).toContainText('巡检记录：设备检查完成。')

    // 全部替换
    await panel.getByRole('button', { name: '全部替换' }).click()
    await expect(page.locator('.doc-surface')).toContainText('巡检记录：巡检完成。')
    await expect(panel.locator('.se-find-count')).toHaveText('0 处')

    // Esc 关闭并清掉高亮
    await page.keyboard.press('Escape')
    await expect(panel).toBeHidden()
    await expect(page.locator('.se-find-hit')).toHaveCount(0)
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U3 撤销/重做独立按钮（原藏在「⋯」菜单）', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-撤销-${Date.now()}`)
  try {
    await openDocById(page, docId)
    await typeAtEnd(page, '撤销重做测试')

    const undoBtn = page.getByRole('button', { name: '撤销 (Ctrl+Z)' })
    const redoBtn = page.getByRole('button', { name: '重做 (Ctrl+Y)' })
    await expect(undoBtn).toBeEnabled({ timeout: 5_000 })
    await expect(redoBtn).toBeDisabled()

    await undoBtn.click()
    await expect(page.locator('.doc-surface')).not.toContainText('撤销重做测试')
    await expect(redoBtn).toBeEnabled()

    await redoBtn.click()
    await expect(page.locator('.doc-surface')).toContainText('撤销重做测试')
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U4 快捷键帮助：Ctrl+/ 与「⋯」菜单两个入口', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-快捷键-${Date.now()}`)
  try {
    await openDocById(page, docId)

    // 入口一：Ctrl+/
    await page.keyboard.press('Control+/')
    const dialog = page.locator('.el-dialog', { hasText: '键盘快捷键' })
    await expect(dialog).toBeVisible({ timeout: 5_000 })
    await expect(dialog).toContainText('Ctrl+F')
    await expect(dialog).toContainText('AI 改写')
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()

    // 入口二：「⋯」更多菜单 → 快捷键…
    await page.locator('span[title="更多格式与操作"]').click()
    await page.locator('.el-dropdown-menu__item', { hasText: '快捷键' }).click()
    await expect(dialog).toBeVisible({ timeout: 5_000 })
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U5 AI 改写入口：顶栏按钮 + Alt+A 唤起，新预设可用', async ({ page }) => {
  test.setTimeout(90_000)
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-改写-${Date.now()}`)
  try {
    await openDocById(page, docId)
    await typeAtEnd(page, '但是这次改造需要验证。')
    await page.waitForTimeout(600)

    const aiBtn = page.getByRole('button', { name: 'AI 改写' })
    await expect(aiBtn).toBeDisabled() // 无选区时不可用

    const reviseMenu = page.locator('.se-revise-menu')

    // 顶栏入口 → 菜单含新预设（同款选区竞态加固：选中→点击→菜单出现 整段重试）
    await expect(async () => {
      await selectLastParagraph(page)
      await expect(aiBtn).toBeEnabled({ timeout: 2_000 })
      await aiBtn.click()
      await expect(reviseMenu).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 20_000 })
    for (const label of ['扩写', '纠正语病', '翻译成英文', '改得口语化']) {
      await expect(reviseMenu.getByRole('button', { name: label })).toBeVisible()
    }

    // 点外关闭（此前菜单只能提交才能关掉）
    await page.locator('.doc-surface').click({ position: { x: 10, y: 10 } })
    await expect(reviseMenu).toBeHidden()

    // Alt+A 入口 → 提交「扩写」→ mock 栈改写以卡片落地（data-source="rewrite"）
    // 选中→按键→菜单出现 整段 toPass 有界重试：分析装饰刷新偶发把 PM 选区塌回光标
    // （1325571 同款竞态），重放幂等——重选前点击会把旧菜单关掉
    await expect(async () => {
      await selectLastParagraph(page)
      await page.keyboard.press('Alt+a')
      await expect(reviseMenu).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 20_000 })
    await reviseMenu.getByRole('button', { name: '扩写' }).click()
    await expect(
      page.locator('.se-suggest-note[data-source="rewrite"]').first(),
    ).toBeVisible({ timeout: 30_000 })
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U6 目录导航：HUD 目录列出标题并点击滚动定位', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-目录-${Date.now()}`)
  try {
    await page.goto(`/suggest-editor/${docId}?debug=1`)
    await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
    await page.waitForFunction(() => Boolean((window as Record<string, unknown>).__editor), undefined, { timeout: 20_000 })

    const paras = Array.from({ length: 30 }, (_, i) => `<p>填充段落 ${i + 1}：目录导航滚动验证文本。</p>`).join('')
    await page.evaluate((html) => {
      const editor = (window as unknown as { __editor?: { commands: { setContent: (h: string) => void } } }).__editor
      if (!editor) throw new Error('__editor 不存在（需要 ?debug=1）')
      editor.commands.setContent(
        `${html}<h1>总述</h1><p>总述正文。</p><h2>细节</h2><p>细节正文。</p>`,
      )
    }, paras)
    await page.waitForTimeout(600) // 等 HUD 防抖刷新

    await page.locator('.se-doc-hud-btn').click()
    const pop = page.locator('.se-doc-hud-pop')
    await expect(pop).toBeVisible({ timeout: 5_000 })
    await expect(pop.locator('.se-outline-item')).toHaveCount(2)
    await expect(pop.locator('.se-outline-item.is-h1')).toHaveText('总述')
    await expect(pop.locator('.se-outline-item.is-h2')).toHaveText('细节')

    const host = page.locator('.se-editor-host')
    const before = await host.evaluate((el) => el.scrollTop)
    await pop.locator('.se-outline-item.is-h2').click()
    await page.waitForTimeout(1200) // smooth scroll
    const after = await host.evaluate((el) => el.scrollTop)
    expect(after, `scrollTop before=${before} after=${after}`).toBeGreaterThan(before)
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U7 打印 / 导出 PDF：⋯菜单触发 window.print（纸面由 print CSS 负责）', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-打印-${Date.now()}`)
  try {
    // 打印对话框在 headless 下无法交互：stub 掉 window.print 断言触发即可
    await page.addInitScript(() => {
      ;(window as unknown as Record<string, unknown>).__printTriggered = false
      window.print = () => {
        ;(window as unknown as Record<string, unknown>).__printTriggered = true
      }
    })
    await openDocById(page, docId)
    await page.locator('span[title="更多格式与操作"]').click()
    await page.locator('.el-dropdown-menu__item', { hasText: '打印 / 导出 PDF' }).click()
    await expect
      .poll(() => page.evaluate(() => (window as unknown as Record<string, unknown>).__printTriggered))
      .toBe(true)
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U8 AI 整段续写：Ctrl+J 发起 → ghost 预览 → Tab 落正文', async ({ page }) => {
  test.setTimeout(90_000)
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-续写-${Date.now()}`)
  try {
    await openDocById(page, docId)
    await typeAtEnd(page, '系统上线前需要完成准备工作。')
    // 等 700ms 自动兜底 debounce 先跑掉：确保自动 sentence 请求先于 manual 发出，
    // 服务端「同客户端只留最新」会保留后发的 paragraph 命令（顺序固定，不被反向取代）
    await page.waitForTimeout(900)

    await page.keyboard.press('Control+j')
    const ghost = page.locator('.ghost-text')
    await expect(ghost).toContainText('综上所述', { timeout: 30_000 })

    await page.keyboard.press('Tab')
    await expect(page.locator('.doc-surface')).toContainText('综上所述', { timeout: 10_000 })
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U9 语气滑杆：口语↔正式连续调节，按语气改写以卡片落地', async ({ page }) => {
  test.setTimeout(90_000)
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-语气-${Date.now()}`)
  try {
    await openDocById(page, docId)
    await typeAtEnd(page, '但是这次改造需要验证。')
    await page.waitForTimeout(600)

    const aiBtn = page.getByRole('button', { name: 'AI 改写' })
    const reviseMenu = page.locator('.se-revise-menu')
    await expect(async () => {
      await selectLastParagraph(page)
      await expect(aiBtn).toBeEnabled({ timeout: 2_000 })
      await aiBtn.click()
      await expect(reviseMenu).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 20_000 })

    // 滑杆调到 4 = 很正式（v-model 监听 input 事件）
    await page.locator('.se-revise-tone-slider').evaluate((el) => {
      const input = el as HTMLInputElement
      input.value = '4'
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await expect(page.locator('.se-revise-tone-value')).toHaveText('很正式')

    await reviseMenu.getByRole('button', { name: '按语气改写' }).click()
    await expect(
      page.locator('.se-suggest-note[data-source="rewrite"]').first(),
    ).toBeVisible({ timeout: 30_000 })
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})

test('U10 拼写检查开关：⋯菜单切换 + 刷新保持', async ({ page }) => {
  const token = readSharedToken()
  const docId = await createDocument(page.request, token, `E2E-易用-拼写-${Date.now()}`)
  try {
    await openDocById(page, docId)
    const surface = page.locator('.doc-surface')
    await expect(surface).toHaveAttribute('spellcheck', 'false', { timeout: 10_000 }) // 中文文档默认关

    await page.locator('span[title="更多格式与操作"]').click()
    await page.locator('.el-dropdown-menu__item', { hasText: '拼写检查：关（点击开启）' }).click()
    await expect(surface).toHaveAttribute('spellcheck', 'true')
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('check-manage:se:spellcheck')))
      .toBe('1')

    // 刷新后从 localStorage 恢复
    await page.reload()
    await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.doc-surface')).toHaveAttribute('spellcheck', 'true', { timeout: 10_000 })

    // 关闭恢复
    await page.locator('span[title="更多格式与操作"]').click()
    await page.locator('.el-dropdown-menu__item', { hasText: '拼写检查：开（点击关闭）' }).click()
    await expect(surface).toHaveAttribute('spellcheck', 'false')
  } finally {
    await deleteDocument(page.request, token, docId)
  }
})
