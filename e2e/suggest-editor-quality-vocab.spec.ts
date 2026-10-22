/**
 * 智能写作质量检查 · 词表矩阵（V1–V6）。
 *
 * 真源是服务端文件（全局 词表-全局.txt / 文档 ws/<id>/词表.txt），经 Node 同步进
 * Y.Map("vocabulary")。这里验证别名替换、文档级覆盖、导入/导出/去重、前缀联想、
 * 文档隔离，以及与检查规则并存时互不干扰。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  EMPTY_CHECKLIST,
  captureGlobalState,
  cardByRule,
  createDocument,
  deleteDocument,
  ensureSharedLogin,
  getGlobalVocabulary,
  gotoSuggestList,
  installSharedAuth,
  openDocById,
  putChecklist,
  putDocVocabulary,
  putGlobalRules,
  putGlobalVocabulary,
  readSharedToken,
  restoreGlobalState,
  type GlobalState,
} from './helpers/suggest-editor-quality'

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
  // 词表用例不需要 AI：空检查项 + 空规则，避免 AI 卡片混入断言
  await putGlobalRules(api, token, [])
  await putChecklist(api, token, EMPTY_CHECKLIST)
})

test.afterAll(async () => {
  await restoreGlobalState(api, token, baseline)
  await api.dispose()
})

/** 输入前缀 → 等候选出现 → Tab 接受 */
async function acceptCompletion(page: Page, typed: string, term: string): Promise<void> {
  await page.locator('.doc-surface').click()
  await page.keyboard.type(typed, { delay: 60 })
  const candidate = page.locator(`.se-completion-popup [data-vocab-term="${term}"]`)
  await expect(candidate).toBeVisible({ timeout: 10_000 })
  await page.keyboard.press('Tab')
  await expect(page.locator('.doc-surface')).toContainText(term)
}

test.describe('词表', () => {
  test('V1 全局词表别名：输入别名反向替换为词条本体', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      await putGlobalVocabulary(api, token, '# e2e 全局词表\n分布式存储|DS\n')
      docId = await createDocument(api, token, `E2E-V1-${Date.now()}`)
      await openDocById(page, docId)

      await acceptCompletion(page, 'DS', '分布式存储')
      await expect(page.locator('.doc-surface')).not.toContainText('DS')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('V2 文档词表覆盖全局：同一别名在两个文档解析到不同词条', async ({ page }) => {
    test.setTimeout(240_000)
    let docA = ''
    let docB = ''
    try {
      await putGlobalVocabulary(api, token, '# e2e 全局词表\n量子网关|QG\n')
      docA = await createDocument(api, token, `E2E-V2-A-${Date.now()}`)
      docB = await createDocument(api, token, `E2E-V2-B-${Date.now()}`)
      await putDocVocabulary(api, token, docA, '# 文档词表\n量子中继|QG\n')
      await putDocVocabulary(api, token, docB, '# 无文档词条\n')

      await openDocById(page, docA)
      await acceptCompletion(page, 'QG', '量子中继')
      await expect(page.locator('.doc-surface')).toContainText('量子中继')

      await openDocById(page, docB)
      await acceptCompletion(page, 'QG', '量子网关')
      await expect(page.locator('.doc-surface')).toContainText('量子网关')
    } finally {
      if (docA) await deleteDocument(api, token, docA)
      if (docB) await deleteDocument(api, token, docB)
    }
  })

  test('V3 管理面导入/导出/去重：导出含条目，清空后导入可恢复，重复导入全部跳过', async ({ page }) => {
    test.setTimeout(180_000)
    const exported = path.join(os.tmpdir(), `se-vocab-${Date.now()}.txt`)
    try {
      await gotoSuggestList(page)
      await page.getByRole('button', { name: '词表管理' }).click()
      const dialog = page.locator('.el-dialog').filter({
        has: page.locator('.el-dialog__title', { hasText: '全局词表' }),
      })
      await dialog.waitFor({ state: 'visible' })
      const textarea = dialog.locator('.vocab-textarea textarea')

      await textarea.fill('# e2e 导出\n导出校验词|DCV\n')
      await dialog.getByRole('button', { name: '保存' }).click()
      await expect(page.locator('.el-message', { hasText: '已保存' }).last()).toBeVisible({
        timeout: 10_000,
      })

      const downloadPromise = page.waitForEvent('download')
      await dialog.locator('[data-testid="asset-export"]').click()
      const download = await downloadPromise
      await download.saveAs(exported)
      expect(fs.readFileSync(exported, 'utf-8')).toContain('导出校验词|DCV')

      // 清空（覆盖写入空词表）
      await textarea.fill('# cleared\n')
      await dialog.getByRole('button', { name: '保存' }).click()
      // 上一次保存的 toast 可能还在堆叠中，用 .last() 锚定本次
      await expect(page.locator('.el-message', { hasText: '已保存' }).last()).toBeVisible({
        timeout: 10_000,
      })
      expect((await getGlobalVocabulary(api, token)).trim()).toBe('# cleared')

      const importBuffer = Buffer.from('导出校验词|DCV\n导入新词|DRV\n', 'utf-8')
      const fileInput = dialog.locator('input[type="file"]')
      await fileInput.setInputFiles({
        name: 'vocab-import.txt', mimeType: 'text/plain', buffer: importBuffer,
      })
      await expect(page.locator('.el-message', { hasText: '导入完成：新增 2' })).toBeVisible({
        timeout: 10_000,
      })
      await expect(textarea).toHaveValue(/导出校验词\|DCV/)
      await expect(textarea).toHaveValue(/导入新词\|DRV/)

      // 同文件再导入一次：全部跳过（去重键 = | 之前的词条）
      await fileInput.setInputFiles({
        name: 'vocab-import.txt', mimeType: 'text/plain', buffer: importBuffer,
      })
      await expect(page.locator('.el-message', { hasText: '新增 0' })).toBeVisible({ timeout: 10_000 })
      await expect(page.locator('.el-message', { hasText: '跳过 2' })).toBeVisible({ timeout: 10_000 })
    } finally {
      fs.rmSync(exported, { force: true })
    }
  })

  test('V4 前缀联想：输入中文前缀即出现候选，Tab 接受补全', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      await putGlobalVocabulary(api, token, '# e2e 前缀\n量子加密网关\n')
      docId = await createDocument(api, token, `E2E-V4-${Date.now()}`)
      await openDocById(page, docId)

      await page.locator('.doc-surface').click()
      await page.keyboard.type('量子', { delay: 60 })
      await expect(page.locator('.se-completion-popup [data-vocab-term="量子加密网关"]')).toBeVisible({
        timeout: 10_000,
      })
      await page.keyboard.press('Tab')
      await expect(page.locator('.doc-surface')).toContainText('量子加密网关')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('V5 文档隔离：A 文档的词条不出现在 B 文档的候选里', async ({ page }) => {
    test.setTimeout(240_000)
    let docA = ''
    let docB = ''
    try {
      await putGlobalVocabulary(api, token, '# e2e 隔离\n')
      docA = await createDocument(api, token, `E2E-V5-A-${Date.now()}`)
      docB = await createDocument(api, token, `E2E-V5-B-${Date.now()}`)
      await putDocVocabulary(api, token, docA, '# A 专属\n隔离术语甲|GTA\n')
      await putDocVocabulary(api, token, docB, '# B 无专属\n')

      await openDocById(page, docA)
      await page.locator('.doc-surface').click()
      await page.keyboard.type('GTA', { delay: 60 })
      await expect(page.locator('.se-completion-popup [data-vocab-term="隔离术语甲"]')).toBeVisible({
        timeout: 10_000,
      })

      await openDocById(page, docB)
      await page.locator('.doc-surface').click()
      await page.keyboard.type('GTA', { delay: 60 })
      await page.waitForTimeout(1200)
      await expect(page.locator('.se-completion-popup [data-vocab-term="隔离术语甲"]')).toHaveCount(0)
    } finally {
      if (docA) await deleteDocument(api, token, docA)
      if (docB) await deleteDocument(api, token, docB)
    }
  })

  test('V6 词表与检查规则并存：联想替换不破坏卡片，卡片也不阻断联想', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      await putGlobalVocabulary(api, token, '# e2e 并存\n设备清单|SBQD\n')
      await putGlobalRules(api, token, [{
        id: 'e2e-v6-whitelist',
        name: 'E2E 端口白名单',
        enabled: true,
        severity: 'warn',
        whitelists: [{
          anchors: ['端口类型：'],
          values: ['GE'],
          message: '端口类型只能是 GE',
        }],
      }])
      docId = await createDocument(api, token, `E2E-V6-${Date.now()}`)
      await openDocById(page, docId)
      await expect(page.locator('.se-rules-badge')).toHaveText('规则 1', { timeout: 20_000 })

      await page.locator('.doc-surface').click()
      await page.keyboard.type('端口类型：10GE。', { delay: 15 })
      const card = cardByRule(page, 'e2e-v6-whitelist')
      await expect(card).toBeVisible({ timeout: 10_000 })

      await page.keyboard.press('Enter')
      await acceptCompletion(page, 'SBQD', '设备清单')

      await expect(card).toBeVisible()
      await expect(page.locator('.doc-surface')).toContainText('端口类型：10GE。')
      await expect(page.locator('.doc-surface')).toContainText('设备清单')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })
})
