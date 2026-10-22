/**
 * 智能写作质量检查 · 导入文档（I1–I4）。
 *
 * 覆盖：导入表格单元格上的本地规则、导入基线（首轮不全量调用模型）、
 * 图注 presence 规则、导入失败不留半成品。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  EMPTY_CHECKLIST,
  captureGlobalState,
  cardByRule,
  deleteDocsByTitle,
  ensureSharedLogin,
  expectImportBaseline,
  gotoSuggestList,
  installSharedAuth,
  listDocuments,
  putChecklist,
  putGlobalRules,
  readSharedToken,
  restoreGlobalState,
  writeDocxFixture,
  type GlobalState,
} from './helpers/suggest-editor-quality'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg=='

let api: APIRequestContext
let token = ''
let baseline: GlobalState

test.beforeAll(async ({ browser, playwright }) => {
  await ensureSharedLogin(browser)
  api = await playwright.request.newContext()
  token = readSharedToken()
  baseline = await captureGlobalState(api, token)
})

test.beforeEach(async ({ context }) => {
  await installSharedAuth(context)
  await restoreGlobalState(api, token, baseline)
  await putGlobalRules(api, token, [])
  await putChecklist(api, token, EMPTY_CHECKLIST)
})

test.afterAll(async () => {
  await restoreGlobalState(api, token, baseline)
  await api.dispose()
})

/** 通过列表页 UI 导入 docx，返回导入后打开的编辑页 URL */
async function importDocx(page: Page, filePath: string): Promise<void> {
  await gotoSuggestList(page)
  await page.getByRole('button', { name: '导入 Word' }).click()
  const dialog = page.locator('.el-dialog').filter({
    has: page.locator('.el-dialog__title', { hasText: '导入 Word' }),
  })
  await dialog.waitFor({ state: 'visible' })
  await dialog.locator('input[type="file"]').setInputFiles(filePath)
  await dialog.getByRole('button', { name: '开始导入' }).click()
}

function titleOf(filePath: string): string {
  return path.basename(filePath, path.extname(filePath))
}

test.describe('导入文档', () => {
  test('I1 导入表格单元格的型号错误：毫秒级命中本地 map 规则', async ({ page }) => {
    test.setTimeout(240_000)
    const ts = Date.now()
    const fixture = path.join(os.tmpdir(), `E2E-I1-${ts}.docx`)
    const title = titleOf(fixture)
    try {
      await writeDocxFixture(fixture, (docx) => new docx.Document({
        sections: [{
          children: [
            new docx.Paragraph({ text: '设备清单', heading: docx.HeadingLevel.HEADING_1 }),
            new docx.Table({
              rows: [
                new docx.TableRow({
                  children: [
                    new docx.TableCell({ children: [new docx.Paragraph('设备型号')] }),
                    new docx.TableCell({ children: [new docx.Paragraph('数量')] }),
                  ],
                }),
                new docx.TableRow({
                  children: [
                    new docx.TableCell({ children: [new docx.Paragraph('S-5700')] }),
                    new docx.TableCell({ children: [new docx.Paragraph('3')] }),
                  ],
                }),
              ],
            }),
          ],
        }],
      }))
      await putGlobalRules(api, token, [{
        id: 'e2e-i1-map',
        name: 'E2E 导入型号纠错',
        enabled: true,
        severity: 'error',
        maps: [{ wrong: 'S-5700', right: 'S5700' }],
      }])

      await importDocx(page, fixture)
      await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expect(page.locator('.doc-surface table')).toContainText('S-5700')

      const card = cardByRule(page, 'e2e-i1-map')
      await expect(card).toBeVisible({ timeout: 10_000 })
      await expect(card).toHaveAttribute('data-quote', 'S-5700')
      await expect(card).toHaveAttribute('data-mechanism', 'maps')
      await expect(card).toHaveAttribute('data-source', 'local')
    } finally {
      fs.rmSync(fixture, { force: true })
      await deleteDocsByTitle(api, token, title).catch(() => {})
    }
  })

  test('I2 导入基线：首轮不调用模型；改一段后只分析 1 个变动段落', async ({ page }) => {
    test.setTimeout(240_000)
    const ts = Date.now()
    const fixture = path.join(os.tmpdir(), `E2E-I2-${ts}.docx`)
    const title = titleOf(fixture)
    try {
      await writeDocxFixture(fixture, (docx) => new docx.Document({
        sections: [{
          children: [
            new docx.Paragraph({ text: '导入标题', heading: docx.HeadingLevel.HEADING_1 }),
            new docx.Paragraph({ children: [new docx.TextRun('正文第一段')] }),
            new docx.Paragraph({ children: [new docx.TextRun('正文第二段')] }),
            new docx.Table({
              rows: [new docx.TableRow({
                children: [new docx.TableCell({ children: [new docx.Paragraph('表格单元')] })],
              })],
            }),
          ],
        }],
      }))

      await importDocx(page, fixture)
      await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })
      await expectImportBaseline(page)

      // 只改一个段落：脏块 = 1
      const paragraph = page.locator('.doc-surface p', { hasText: '正文第一段' }).first()
      await paragraph.click()
      await page.keyboard.press('End')
      await page.keyboard.type('X')
      await expect(paragraph).toContainText('正文第一段X')
      await expect(page.locator('.se-status')).toContainText(/分析 1 个变动段落/, { timeout: 30_000 })
    } finally {
      fs.rmSync(fixture, { force: true })
      await deleteDocsByTitle(api, token, title).catch(() => {})
    }
  })

  test('I3 导入图片与图注规则：图注文本触发 presence，补豁免后消失', async ({ page }) => {
    test.setTimeout(240_000)
    const ts = Date.now()
    const fixture = path.join(os.tmpdir(), `E2E-I3-${ts}.docx`)
    const title = titleOf(fixture)
    const png = Buffer.from(PNG_B64, 'base64')
    try {
      await writeDocxFixture(fixture, (docx) => new docx.Document({
        sections: [{
          children: [
            new docx.Paragraph({ text: '图注测试' }),
            new docx.Paragraph({
              children: [new docx.ImageRun({
                type: 'png', data: png, transformation: { width: 60, height: 60 },
              })],
            }),
          ],
        }],
      }))
      await putGlobalRules(api, token, [{
        id: 'e2e-i3-caption',
        name: 'E2E 图注配套',
        enabled: true,
        severity: 'warn',
        presence: [{
          triggers: ['9800M12'],
          requiresAny: ['9800UPS', '不涉及拖带'],
          message: '图注涉及 9800M12 时须写明 9800UPS 或声明不涉及拖带',
        }],
      }])

      await importDocx(page, fixture)
      await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })
      await expect(page.locator('.doc-surface figure img').first()).toBeVisible({ timeout: 20_000 })
      await expectImportBaseline(page)

      const caption = page.locator('.doc-surface figcaption').first()
      await caption.click()
      await page.keyboard.type('图注：9800M12', { delay: 15 })

      const card = cardByRule(page, 'e2e-i3-caption')
      await expect(card).toBeVisible({ timeout: 10_000 })
      await expect(card).toHaveAttribute('data-quote', '9800M12')
      await expect(card).toHaveAttribute('data-notice', 'true')

      await page.keyboard.type('，不涉及拖带', { delay: 15 })
      await expect(card).toHaveCount(0, { timeout: 5_000 })
    } finally {
      fs.rmSync(fixture, { force: true })
      await deleteDocsByTitle(api, token, title).catch(() => {})
    }
  })

  test('I4 导入失败不留半成品：损坏 docx 报错，列表无新增文档', async ({ page }) => {
    test.setTimeout(180_000)
    const title = `E2E-I4-损坏-${Date.now()}`
    try {
      await gotoSuggestList(page)
      await page.getByRole('button', { name: '导入 Word' }).click()
      const dialog = page.locator('.el-dialog').filter({
        has: page.locator('.el-dialog__title', { hasText: '导入 Word' }),
      })
      await dialog.waitFor({ state: 'visible' })
      await dialog.locator('input[type="file"]').setInputFiles({
        name: `${title}.docx`,
        mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        buffer: Buffer.from('this is not a real docx archive', 'utf-8'),
      })
      await dialog.getByRole('button', { name: '开始导入' }).click()

      await expect(dialog.locator('.import-error')).toBeVisible({ timeout: 20_000 })
      await dialog.getByRole('button', { name: '取消' }).click()
      await dialog.waitFor({ state: 'hidden' })

      const documents = await listDocuments(api, token)
      expect(documents.filter((doc) => doc.title === title)).toHaveLength(0)
    } finally {
      await deleteDocsByTitle(api, token, title).catch(() => {})
    }
  })

  test('I5 不支持的图片格式只跳过该图：整份导入成功，png 正常显示', async ({ page }) => {
    test.setTimeout(240_000)
    const ts = Date.now()
    const fixture = path.join(os.tmpdir(), `E2E-I5-${ts}.docx`)
    const title = titleOf(fixture)
    const png = Buffer.from(PNG_B64, 'base64')
    // 假 BMP：前端/服务端白名单都不收，导入必须跳过它而不是中断
    const fakeBmp = Buffer.concat([Buffer.from('BM'), Buffer.alloc(64, 0)])
    try {
      await writeDocxFixture(fixture, (docx) => new docx.Document({
        sections: [{
          children: [
            new docx.Paragraph({ text: '混合图片文档' }),
            new docx.Paragraph({
              children: [new docx.ImageRun({
                type: 'bmp', data: fakeBmp, transformation: { width: 40, height: 40 },
              })],
            }),
            new docx.Paragraph({
              children: [new docx.ImageRun({
                type: 'png', data: png, transformation: { width: 40, height: 40 },
              })],
            }),
          ],
        }],
      }))

      await importDocx(page, fixture)
      await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })

      // 导入完成提示里应出现「已跳过」，且不出现导入错误
      await expect(page.locator('.el-message', { hasText: '已跳过' })).toBeVisible({ timeout: 15_000 })

      // png 正常落进 figure；被跳过的 bmp 不留死链/空 figure
      const img = page.locator('.doc-surface figure img').first()
      await expect(img).toBeVisible({ timeout: 20_000 })
      await expect.poll(
        () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
      ).toBeGreaterThan(0)
      const figureCount = await page.locator('.doc-surface figure').count()
      expect(figureCount).toBe(1)
      await expect(page.locator('.doc-surface')).toContainText('混合图片文档')
    } finally {
      fs.rmSync(fixture, { force: true })
      await deleteDocsByTitle(api, token, title).catch(() => {})
    }
  })
})
