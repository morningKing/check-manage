/**
 * 复现「规则编辑保存」UI 路径（生产 :8080 直连，一次性诊断脚本）：
 * 登录 → 智能写作 → 检查规则 → 编辑第一条规则 → triggers 追加 9700M12 → 确定
 * → 断言「规则已保存」→ 回读接口确认落盘 → 撤销该测试触发词。
 */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8080'
const API = 'http://127.0.0.1:3002'

async function main() {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const logs = []
  page.on('console', (m) => logs.push(`[console:${m.type()}] ${m.text()}`))
  page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))

  try {
    await page.goto(`${BASE}/`)
    await page.waitForTimeout(1500)
    if (page.url().includes('/login')) {
      await page.fill('input[placeholder*="用户名"]', 'admin')
      await page.fill('input[placeholder*="密码"]', 'admin123')
      await page.getByRole('button', { name: /登\s*录/ }).click()
    }
    await page.waitForFunction(() => !!localStorage.getItem('check-manage:token'), undefined, { timeout: 20000 })
    console.log('login ok')

    // 进入列表页 → 检查规则
    await page.goto(`${BASE}/home`)
    await page.locator('.el-menu-item', { hasText: '智能写作' }).first().click()
    await page.getByRole('button', { name: '检查规则' }).click()
    const dialog = page.locator('.el-dialog').filter({ has: page.locator('.el-dialog__title', { hasText: '检查规则' }) })
    await dialog.waitFor({ state: 'visible' })
    console.log('rules dialog open, rows:', await dialog.locator('.el-table__row').count())

    // 编辑第一条规则：triggers 追加 9700M12（临时测试词）
    await dialog.locator('.el-table__row').first().getByRole('button', { name: '编辑' }).click()
    const form = page.locator('.el-dialog').filter({ has: page.locator('.el-dialog__title', { hasText: '编辑规则' }) })
    await form.waitFor({ state: 'visible' })
    const triggers = form.locator('[data-field="triggers"]')
    const before = await triggers.inputValue()
    console.log('triggers before:', JSON.stringify(before))
    await triggers.fill(before ? `${before}\n9700M12` : '9700M12')
    await form.getByRole('button', { name: '确定' }).click()
    try {
      await page.locator('.el-message', { hasText: '规则已保存' }).waitFor({ state: 'visible', timeout: 10000 })
      console.log('save toast: 规则已保存 ✓')
    } catch {
      const msgs = await page.locator('.el-message').allInnerTexts().catch(() => [])
      console.log('save toast MISSING; visible messages:', msgs)
    }

    // 回读接口确认落盘
    const token = await page.evaluate(() => JSON.parse(localStorage.getItem('check-manage:token') || '""'))
    const res = await page.request.get(`${API}/suggest-editor/rules/global`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const rules = (await res.json()).rules
    console.log('persisted triggers:', JSON.stringify(rules[0].presence[0].triggers))

    // 撤销临时触发词（保持环境干净）
    rules[0].presence[0].triggers = rules[0].presence[0].triggers.filter((t) => t !== '9700M12')
    await page.request.put(`${API}/suggest-editor/rules/global`, {
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      data: { rules },
    })
    console.log('restored triggers:', JSON.stringify(rules[0].presence[0].triggers))
  } finally {
    if (logs.length) console.log('--- page logs ---\n' + logs.slice(-20).join('\n'))
    await browser.close()
  }
}

main().catch((e) => { console.error('FAIL:', e); process.exit(1) })
