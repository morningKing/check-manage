/**
 * 生产验收（一次性脚本）：9600M12 拖带完整性规则在编辑器中真实生效。
 * 1) 登录 → API 建文档 → 打开编辑器（:8080 生产入口）
 * 2) 输入「设备 9600M12 组网。」→ 期望出现「9600UPS 拖带完整性」卡片（本地毫秒级）
 * 3) 补写「不涉及拖带。」→ 期望卡片消失
 * 4) 删除文档
 */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8080'
const API = 'http://127.0.0.1:3002'

async function main() {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const ok = (label) => console.log(`✓ ${label}`)
  try {
    await page.goto(`${BASE}/`)
    await page.waitForTimeout(1200)
    if (page.url().includes('/login')) {
      await page.fill('input[placeholder*="用户名"]', 'admin')
      await page.fill('input[placeholder*="密码"]', 'admin123')
      await page.getByRole('button', { name: /登\s*录/ }).click()
    }
    await page.waitForFunction(() => !!localStorage.getItem('check-manage:token'), undefined, { timeout: 20000 })
    const token = await page.evaluate(() => JSON.parse(localStorage.getItem('check-manage:token') || '""'))
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

    // API 建文档
    const created = await page.request.post(`${API}/suggest-editor/documents`, {
      headers, data: { title: `生产验收-9600-${Date.now()}` },
    })
    const doc = (await created.json()).document
    await page.goto(`${BASE}/suggest-editor/${doc.id}`)
    await page.locator('.doc-surface').waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('.se-badge').first().waitFor({ state: 'visible' })
    await page.waitForTimeout(1500)

    // 输入 9600M12 → 本地 presence 卡片
    await page.locator('.doc-surface').click()
    await page.keyboard.type('设备 9600M12 组网。', { delay: 20 })
    const card = page.locator('.se-suggest-note', { hasText: '9600UPS 拖带完整性' })
    await card.waitFor({ state: 'visible', timeout: 8000 })
    ok('9600M12 → 出现「9600UPS 拖带完整性」提示（本地毫秒级）')

    // 补豁免词 → 消失
    await page.keyboard.press('Control+End')
    await page.keyboard.type('本方案不涉及拖带。', { delay: 20 })
    await card.waitFor({ state: 'hidden', timeout: 8000 })
    ok('补写「不涉及拖带」→ 提示自动消失')

    // 清理
    await page.request.delete(`${API}/suggest-editor/documents/${doc.id}`, { headers })
    ok('清理完成')
  } finally {
    await browser.close()
  }
}

main().catch((e) => { console.error('FAIL:', e); process.exit(1) })
