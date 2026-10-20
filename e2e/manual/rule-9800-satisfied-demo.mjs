/**
 * 演示：presence 是文档级判定——文档里已有「不涉及拖带」时，即使重写 9800M12 也不提示；
 * 把豁免词删掉后，提示实时出现（本地毫秒级）。用生产 :8080 真浏览器验证。
 */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8080'
const API = 'http://127.0.0.1:3002'

async function main() {
  const browser = await chromium.launch()
  const page = await browser.newPage()
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

    const created = await page.request.post(`${API}/suggest-editor/documents`, {
      headers, data: { title: `验收-豁免已满足-${Date.now()}` },
    })
    const doc = (await created.json()).document
    await page.goto(`${BASE}/suggest-editor/${doc.id}`)
    await page.locator('.doc-surface').waitFor({ state: 'visible', timeout: 20000 })
    await page.waitForTimeout(1500)

    // 加载规则数徽标（应显示 规则 2）
    const badge = await page.locator('.se-rules-badge').innerText()
    console.log('规则徽标:', badge)

    // 场景 1：写与用户原文一致的「9800M12（不涉及拖带）」→ 不提示（文档级豁免已满足）
    await page.locator('.doc-surface').click()
    await page.keyboard.type('设备类型：9800M12（不涉及拖带）。', { delay: 15 })
    const card = page.locator('.se-suggest-note', { hasText: '9800UPS 拖带完整性' })
    await page.waitForTimeout(2500)
    const countWithExemption = await card.count()
    console.log('含豁免词时提示数:', countWithExemption, countWithExemption === 0 ? '✓ 正确（已满足）' : '✗ 不应出现')

    // 场景 2：删掉豁免词（模拟"重写关键字、没写豁免"）→ 提示应立即出现
    await page.keyboard.press('Control+End')
    for (let i = 0; i < '（不涉及拖带）'.length; i++) await page.keyboard.press('Backspace')
    await card.waitFor({ state: 'visible', timeout: 5000 })
    console.log('✓ 删除豁免词后提示实时出现（<5s）')

    await page.request.delete(`${API}/suggest-editor/documents/${doc.id}`, { headers })
    console.log('✓ 清理完成')
  } finally {
    await browser.close()
  }
}
main().catch((e) => { console.error('FAIL:', e); process.exit(1) })
