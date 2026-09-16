import { test, expect } from '@playwright/test'

/**
 * 深色模式对比度巡检（后台管理页面逐页检查）。
 *
 * 对每个页面抓取所有可见文本节点的计算样式，计算文字色与其有效背景色
 * （向上找第一个非透明背景）的 WCAG 对比度；对比度过低的文本占比必须
 * 低于阈值——防止硬编码浅色主题颜色在深色下"文字看不清"的回归。
 */

const PAGES = [
  '/admin/ai-settings',
  '/admin/ai-scan',
  '/admin/ai-batches',
  '/admin/ai-sessions',
  '/admin/ai-skills',
  '/admin/ai-opencode',
  '/admin/query',
  '/admin/etl',
  '/admin/export-scripts',
  '/admin/validation-scripts',
  '/admin/trigger-rules',
  '/admin/webhook',
  '/admin/dependency-manager',
  '/admin/operation-log',
  '/admin/backup',
  '/admin/system-settings',
  '/admin/users',
  '/admin/roles',
  '/admin/menu',
  '/admin/page-config',
  '/admin/factory-reset',
  '/admin/api-keys',
]

// WCAG 对比度（1~21）；正常文本达标线 4.5，这里取 3.5 为"明显看不清"的下限
const MIN_CONTRAST = 3.5
// 允许的低对比文本占比（徽标水印、禁用态等边角元素）
const MAX_LOW_CONTRAST_RATIO = 0.03

async function ensureDarkAndLogin(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    localStorage.setItem('check-manage:settings',
      JSON.stringify({ theme: 'dark', fontSize: 'default', compact: false }))
  })
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.getByRole('button', { name: /登\s*录/ }).waitFor({ state: 'hidden', timeout: 15_000 })
}

function luminance(r: number, g: number, b: number): number {
  const f = (c: number) => {
    const v = c / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

function contrast(l1: number, l2: number): number {
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
  return (hi + 0.05) / (lo + 0.05)
}

function parseColor(c: string): [number, number, number, number] {
  const m = c.match(/rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/)
  if (!m) return [255, 255, 255, 1]
  return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])]
}

test.describe('深色模式对比度巡检', () => {
  let page: import('@playwright/test').Page

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage()
    await ensureDarkAndLogin(page)
  })

  test.afterAll(async () => {
    await page.close()
  })

  for (const path of PAGES) {
    test(`深色对比度：${path}`, async () => {
      await page.goto(path)
      await page.waitForSelector('.el-table, .el-form, .el-card, main', { timeout: 15_000 })
      await page.waitForTimeout(1200)

      const report = await page.evaluate(({ minContrast }) => {
        const rgb = (c: string): [number, number, number, number] => {
          const m = c.match(/rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/)
          if (!m) return [255, 255, 255, 1]
          return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])]
        }
        const contrast = (l1: number, l2: number): number => {
          const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
          return (hi + 0.05) / (lo + 0.05)
        }
        const lum = (r: number, g: number, b: number) => {
          const f = (c: number) => {
            const v = c / 255
            return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
          }
          return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
        }
        const bgOf = (el: Element): [number, number, number] => {
          let cur: Element | null = el
          while (cur) {
            const [r, g, b, a] = rgb(getComputedStyle(cur).backgroundColor)
            if (a >= 0.9) return [r, g, b]
            cur = cur.parentElement
          }
          return [22, 24, 29] // 页面深色底（--el-bg-color-page 近似）
        }
        const bad: string[] = []
        let total = 0
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
        const seen = new Set<Element>()
        let node: Node | null
        while ((node = walker.nextNode())) {
          const t = node.textContent?.trim()
          if (!t || t.length < 2) continue
          const el = node.parentElement
          if (!el || seen.has(el)) continue
          seen.add(el)
          // 按钮与徽标的语义色底（红/绿/黄底白字）是 EP 全局设计、两种主题一致，
          // 不属于页面自身的深色适配问题
          if (el.closest('button, .el-button, .el-badge__content')) continue
          const r = el.getBoundingClientRect()
          if (r.width < 4 || r.height < 4) continue
          const cs = getComputedStyle(el)
          if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) < 0.6) continue
          total++
          const [fr, fg, fb, fa] = rgb(cs.color)
          if (fa < 0.6) continue // 半透明文字（禁用态等）单独容忍
          const [br, bg2, bb] = bgOf(el)
          const c = contrast(lum(fr, fg, fb), lum(br, bg2, bb))
          if (c < minContrast) {
            bad.push(`"${t.slice(0, 16)}" color=${cs.color} bg=rgb(${br},${bg2},${bb}) contrast=${c.toFixed(2)}`)
          }
        }
        return { total, bad: bad.slice(0, 8), badCount: bad.length }
      }, { minContrast: MIN_CONTRAST })

      const ratio = report.total ? report.badCount / report.total : 0
      const msg = `${path}: ${report.badCount}/${report.total} 低对比文本\n  ${report.bad.join('\n  ')}`
      expect(ratio, msg).toBeLessThanOrEqual(MAX_LOW_CONTRAST_RATIO)
    })
  }
})
