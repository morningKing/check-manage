/**
 * 智能写作质量检查 · AI 语义矩阵（A1–A9）。
 *
 * 不调用真模型：mock agent 的 SEMANTIC_CASES 采用双条件——
 * 「分析 prompt 里出现规则名/检查要点」且「文档快照里出现缺陷短语」才产出建议，
 * 因此这些用例同时验证了：
 *   规则/要点 → Node 分析 prompt 注入 → ACP 链路 → quote 锚定 → 卡片 → 接受/忽略。
 *
 * 时序：分析有 ~0.8s debounce + mock 延迟；统一等卡片可见（≤15s），不用固定 sleep。
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  EMPTY_CHECKLIST,
  captureGlobalState,
  cardBySource,
  createDocument,
  deleteDocument,
  expectCardShape,
  expectImportBaseline,
  expectRuleBadge,
  ensureSharedLogin,
  gotoSuggestList,
  installSharedAuth,
  openDocById,
  putChecklist,
  putDocRules,
  putGlobalRules,
  readSharedToken,
  restoreGlobalState,
  type GlobalState,
  type LocalCheckRule,
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
})

test.afterAll(async () => {
  await restoreGlobalState(api, token, baseline)
  await api.dispose()
})

/** 文档级 AI 规则（含 aiPrompt）→ 打开文档 → 等规则装载 */
async function openWithDocAiRule(
  page: Page,
  title: string,
  rule: LocalCheckRule,
  options: { seed?: string } = {},
): Promise<string> {
  await putGlobalRules(api, token, [])
  await putChecklist(api, token, EMPTY_CHECKLIST)
  const docId = await createDocument(api, token, title, options.seed)
  await putDocRules(api, token, docId, [rule])
  await openDocById(page, docId)
  await expectRuleBadge(page, 1)
  if (options.seed !== undefined) await expectImportBaseline(page)
  return docId
}

/** 全局检查要点 → 打开空文档 */
async function openWithChecklist(
  page: Page,
  title: string,
  checklist: string,
): Promise<string> {
  await putGlobalRules(api, token, [])
  await putChecklist(api, token, checklist)
  const docId = await createDocument(api, token, title)
  await openDocById(page, docId)
  await expectRuleBadge(page, 0)
  return docId
}

const AI_CARD = '.se-suggest-note[data-source="ai"]'

test.describe('AI 语义检查（mock 契约）', () => {
  test('A1 绝对化承诺：规则名注入 prompt 后命中，接受替换为限定型号', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithDocAiRule(page, `E2E-A1-${Date.now()}`, {
        id: 'e2e-a1-absolute',
        name: '绝对化表述',
        enabled: true,
        severity: 'warn',
        aiPrompt: '绝对化表述：不得承诺支持所有型号，须列出适用范围。',
      })
      await page.locator('.doc-surface').click()
      await page.keyboard.type('本产品支持所有型号。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai',
        mechanism: 'ai',
        severity: 'warn',
        quote: '支持所有型号',
        reasonHas: '绝对化表述',
        notice: false,
      })

      await card.getByRole('button', { name: '接受' }).click()
      await expect(page.locator('.doc-surface')).toContainText('支持 9800M12、9600M12')
      await expect(card).toHaveCount(0)
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A2 指代不明：锚定「该系统」，reason 注明指代规则', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithDocAiRule(page, `E2E-A2-${Date.now()}`, {
        id: 'e2e-a2-pronoun',
        name: '指代明确',
        enabled: true,
        severity: 'info',
        aiPrompt: '指代明确：避免使用「该系统/前者/上述」等无明确指代的说法。',
      })
      await page.locator('.doc-surface').click()
      await page.keyboard.type('该系统负责转发。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'info', quote: '该系统', reasonHas: '指代',
      })

      await card.getByRole('button', { name: '接受' }).click()
      await expect(page.locator('.doc-surface')).toContainText('本方案中的 9800UPS 系统负责转发')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A3 数据不一致：正文「总计 10 台」与表格明细 12 台冲突', async ({ page }) => {
    test.setTimeout(180_000)
    const seed = [
      '<p>总计 10 台</p>',
      '<table><tbody>',
      '<tr><th><p>设备</p></th><th><p>数量</p></th></tr>',
      '<tr><td><p>9800M12</p></td><td><p>12</p></td></tr>',
      '</tbody></table>',
    ].join('')
    let docId = ''
    try {
      docId = await openWithDocAiRule(page, `E2E-A3-${Date.now()}`, {
        id: 'e2e-a3-consistency',
        name: '数据一致性',
        enabled: true,
        severity: 'error',
        aiPrompt: '数据一致性：总数必须与明细表分项之和一致。',
      }, { seed })

      // 导入基线建立后，改动段落才进入分析（表格与正文同屏，作为上下文）
      await expect(page.locator('.doc-surface table')).toContainText('12')
      const paragraph = page.locator('.doc-surface p', { hasText: '总计 10 台' }).first()
      await paragraph.click()
      await page.keyboard.press('End')
      await page.keyboard.type('。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'error', quote: '总计 10 台', reasonHas: '12',
      })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A4 缺回滚方案：全局检查要点命中「上线步骤」', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithChecklist(
        page,
        `E2E-A4-${Date.now()}`,
        '回滚方案：上线步骤必须包含回滚步骤与触发条件\n',
      )
      await page.locator('.doc-surface').click()
      await page.keyboard.type('上线步骤：第一步，重启设备。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'warn', quote: '上线步骤', reasonHas: '回滚方案',
      })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A5 安全与权限：检查要点命中明文密码，严重度 error', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithChecklist(
        page,
        `E2E-A5-${Date.now()}`,
        '权限与加密：不得明文描述密码处理方式\n',
      )
      await page.locator('.doc-surface').click()
      await page.keyboard.type('管理员密码写入配置文件。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'error', quote: '管理员密码', reasonHas: '权限与加密',
      })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A6 表格内 AI 建议：锚定单元格文本，刷新后仍在', async ({ page }) => {
    test.setTimeout(180_000)
    const seed = [
      '<table><tbody>',
      '<tr><th><p>能力</p></th></tr>',
      '<tr><td><p></p></td></tr>',
      '</tbody></table>',
    ].join('')
    let docId = ''
    try {
      docId = await openWithDocAiRule(page, `E2E-A6-${Date.now()}`, {
        id: 'e2e-a6-table',
        name: '绝对化表述',
        enabled: true,
        severity: 'warn',
        aiPrompt: '绝对化表述：不得承诺支持所有型号。',
      }, { seed })

      const cell = page.locator('.doc-surface td p').first()
      await cell.click()
      await page.keyboard.type('支持所有型号', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'warn', quote: '支持所有型号', reasonHas: '绝对化表述',
      })

      // AI 建议持久化在 Y.Doc/数据库：刷新后无需重新分析也在
      await page.waitForTimeout(1500)
      await page.reload()
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expect(page.locator('.doc-surface td p').first()).toContainText('支持所有型号')
      await expect(cardBySource(page, 'ai')).toBeVisible({ timeout: 20_000 })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A7 跨标签页 AI 规则：B 页保存规则 → A 页徽标更新，下一次编辑即命中', async ({ page, context }) => {
    test.setTimeout(240_000)
    const title = `E2E-A7-${Date.now()}`
    const pageB = await context.newPage()
    let docId = ''
    try {
      await putGlobalRules(api, token, [])
      await putChecklist(api, token, EMPTY_CHECKLIST)
      docId = await createDocument(api, token, title)
      await openDocById(page, docId)
      await expectRuleBadge(page, 0)

      // B 页：全局检查规则 → 新建一条只有 AI 说明的规则
      await gotoSuggestList(pageB)
      await pageB.getByRole('button', { name: '检查规则', exact: true }).click()
      const rulesDialog = pageB.locator('.el-dialog').filter({
        has: pageB.locator('.el-dialog__title', { hasText: '检查规则' }),
      })
      await rulesDialog.waitFor({ state: 'visible' })
      await rulesDialog.getByRole('button', { name: '新建规则' }).click()

      const form = pageB.locator('.el-dialog').filter({
        has: pageB.locator('.el-dialog__title', { hasText: '新建规则' }),
      })
      await form.waitFor({ state: 'visible' })
      await form.getByPlaceholder('规则名，如：型号纠错').fill('绝对化表述')
      await form.getByPlaceholder('可选：注入 AI 分析的语义核对说明')
        .fill('绝对化表述：不得承诺支持所有型号，须列出适用范围。')
      await form.getByRole('button', { name: '确定' }).click()
      await expect(pageB.locator('.el-message', { hasText: '规则已保存' })).toBeVisible({
        timeout: 10_000,
      })

      // A 页不刷新、不重新打开：广播重载规则 → 徽标数量更新
      await expectRuleBadge(page, 1)

      // 广播只重载规则、不主动分析：再输入一个字符触发下一轮
      await page.locator('.doc-surface').click()
      await page.keyboard.type('本产品支持所有型号。', { delay: 15 })
      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await expectCardShape(card, {
        source: 'ai', mechanism: 'ai', severity: 'warn', quote: '支持所有型号', reasonHas: '绝对化表述',
      })
    } finally {
      await pageB.close().catch(() => {})
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A8 本地/AI 同范围去重：AI 卡片到达后同一「非常」只剩 AI 一条', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      await putGlobalRules(api, token, [{
        id: 'e2e-a8-local-map',
        name: 'E2E 冗余表达',
        enabled: true,
        severity: 'warn',
        maps: [{ wrong: '非常', right: '' }],
        aiPrompt: '冗余表达：删除可删的冗余修饰词。',
      }])
      await putChecklist(api, token, EMPTY_CHECKLIST)
      docId = await createDocument(api, token, `E2E-A8-${Date.now()}`)
      await openDocById(page, docId)
      await expectRuleBadge(page, 1)

      await page.locator('.doc-surface').click()
      await page.keyboard.type('这个方案非常不错。', { delay: 15 })

      // 本地 map 卡片先出现（毫秒级），随后 AI 卡片覆盖同一范围
      const aiCard = page.locator(`${AI_CARD}[data-quote="非常"]`)
      await expect(aiCard).toBeVisible({ timeout: 15_000 })
      await expect(aiCard).toHaveAttribute('data-severity', 'warn')
      await expect(aiCard.locator('.se-suggest-note-reason')).toContainText('冗余')
      await expect(page.locator('.se-suggest-note[data-quote="非常"]')).toHaveCount(1)
      await expect(page.locator('.se-suggest-note[data-rule-id="e2e-a8-local-map"]')).toHaveCount(0)
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('A9 AI 建议忽略持久化：忽略后卡片消失，刷新不再出现', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithDocAiRule(page, `E2E-A9-${Date.now()}`, {
        id: 'e2e-a9-ignore',
        name: '绝对化表述',
        enabled: true,
        severity: 'warn',
        aiPrompt: '绝对化表述：不得承诺支持所有型号，须列出适用范围。',
      })
      await page.locator('.doc-surface').click()
      await page.keyboard.type('本产品支持所有型号。', { delay: 15 })

      const card = cardBySource(page, 'ai')
      await expect(card).toBeVisible({ timeout: 15_000 })
      await card.getByRole('button', { name: '忽略' }).click()
      await expect(card).toHaveCount(0)

      await page.waitForTimeout(1500)
      await page.reload()
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expect(page.locator('.doc-surface')).toContainText('支持所有型号')
      // 建议映射已删除并持久化；块级缓存命中也不会重新分析出同一建议
      await page.waitForTimeout(1500)
      await expect(cardBySource(page, 'ai')).toHaveCount(0)
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })
})
