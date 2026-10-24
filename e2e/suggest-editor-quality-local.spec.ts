/**
 * 智能写作质量检查 · 本地确定性矩阵（L1–L16）。
 *
 * 覆盖：内置格式规则、maps、patterns、whitelists、presence、严重级别、
 * 禁用/广播、接受 vs 忽略、表格单元格。
 *
 * 确定性前提：
 * - 全局规则按用例精确设置，检查要点写成空清单 → mock agent 不产出任何 AI 建议
 *   （见 suggest-editor-server/mock-agent.mjs 的 HAS_CHECK_SECTION 开关）；
 * - 本地规则的断言窗口内不会有 AI 卡片覆盖同一范围；
 * - 全局状态由 beforeEach 复位、afterAll 恢复；文档在 finally 删除。
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  EMPTY_CHECKLIST,
  acceptCard,
  appendFast,
  captureGlobalState,
  cardByRule,
  createDocument,
  deleteDocument,
  expectCardShape,
  expectRuleBadge,
  ensureSharedLogin,
  ignoreCard,
  installSharedAuth,
  openDocById,
  putChecklist,
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
  // 每个用例从干净的全局状态出发（上一个用例失败也不会污染下一个）
  await restoreGlobalState(api, token, baseline)
})

test.afterAll(async () => {
  await restoreGlobalState(api, token, baseline)
  await api.dispose()
})

/** 建文档 → 打开 → 等规则装载完成 */
async function openWithRules(
  page: Page,
  title: string,
  rules: LocalCheckRule[],
  options: { checklist?: string } = {},
): Promise<string> {
  await putGlobalRules(api, token, rules)
  await putChecklist(api, token, options.checklist ?? EMPTY_CHECKLIST)
  const docId = await createDocument(api, token, title)
  await openDocById(page, docId)
  await expectRuleBadge(page, rules.filter((rule) => rule.enabled).length)
  return docId
}

/** 逐段输入（Enter 分段），用于把多个缺陷放在同一文档里 */
async function typeParagraphs(page: Page, paragraphs: string[]): Promise<void> {
  await page.locator('.doc-surface').click()
  for (let index = 0; index < paragraphs.length; index += 1) {
    if (index > 0) await page.keyboard.press('Enter')
    await page.keyboard.type(paragraphs[index], { delay: 10 })
  }
}

/** 在指定段落内替换一段固定文本（Home + 右移到起点 + 精确 Shift 选择 + 覆盖输入） */
async function replaceInParagraph(
  page: Page,
  paragraphText: string,
  rightSteps: number,
  selectLength: number,
  replacement: string,
): Promise<void> {
  const paragraph = page.locator('.doc-surface p', { hasText: paragraphText }).first()
  await paragraph.click()
  await page.keyboard.press('Home')
  for (let step = 0; step < rightSteps; step += 1) await page.keyboard.press('ArrowRight')
  // 用 Shift+ArrowRight 精确选 N 个字符（不用 Shift+End：视觉行尾与块尾不一致）
  for (let step = 0; step < selectLength; step += 1) await page.keyboard.press('Shift+ArrowRight')
  await page.keyboard.type(replacement, { delay: 10 })
  // 输入数字/字母可能唤起词表联想弹层（widget 在正文 DOM 内），先关掉再断言正文
  await page.keyboard.press('Escape')
}

test.describe('本地确定性规则', () => {
  test('L1–L6 内置格式规则：六类机械缺陷各自命中、严重度正确、接受后规范化', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L1L6-${Date.now()}`, [])
      await typeParagraphs(page, [
        '方案  说明',
        '完成 ，继续',
        '完成。。',
        '我们的的方案',
        '设备甲,设备乙',
        '等待...',
      ])

      const expectations: Array<{
        ruleId: string
        severity: 'info' | 'warn' | 'error'
        quote: string
        reason: string
        severityLabel: string
      }> = [
        { ruleId: 'double-space', severity: 'error', quote: '  ', reason: '连续空格', severityLabel: '错误' },
        { ruleId: 'space-before-punct', severity: 'warn', quote: ' ，', reason: '标点前多余空格', severityLabel: '建议' },
        { ruleId: 'dup-punct', severity: 'error', quote: '。。', reason: '重复标点', severityLabel: '错误' },
        { ruleId: 'dup-particle', severity: 'error', quote: '的的', reason: '重复助词「的的」', severityLabel: '错误' },
        { ruleId: 'halfwidth-comma', severity: 'info', quote: '甲,设', reason: '中文之间的半角逗号', severityLabel: '提示' },
        { ruleId: 'ascii-ellipsis', severity: 'info', quote: '...', reason: '省略号应为「……」', severityLabel: '提示' },
      ]

      for (const expected of expectations) {
        const card = cardByRule(page, expected.ruleId)
        await expectCardShape(card, {
          source: 'local',
          mechanism: 'builtin',
          severity: expected.severity,
          quote: expected.quote,
          reasonHas: expected.reason,
        })
        await expect(card.locator('.se-suggest-note-severity')).toHaveText(expected.severityLabel)
      }

      // 全部接受：正文按规则规范化（连续空格→单空格、重复标点→单标点、半角→全角…）
      for (const expected of expectations) {
        await acceptCard(page, cardByRule(page, expected.ruleId))
      }
      const surface = page.locator('.doc-surface')
      await expect(surface).toContainText('方案 说明')
      await expect(surface).toContainText('完成，继续')
      await expect(surface).toContainText('完成。我们的方案')
      await expect(surface).toContainText('设备甲，设备乙')
      await expect(surface).toContainText('等待……')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L7 maps：型号纠错卡片锚定错误写法，接受后正文替换并在刷新后保持', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L7-${Date.now()}`, [{
        id: 'e2e-l7-map',
        name: 'E2E 型号纠错',
        enabled: true,
        severity: 'error',
        maps: [{ wrong: 'S-5700', right: 'S5700' }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('设备 S-5700 上线。', { delay: 15 })

      const card = cardByRule(page, 'e2e-l7-map')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'maps',
        severity: 'error',
        quote: 'S-5700',
        reasonHas: 'E2E 型号纠错',
      })
      await acceptCard(page, card)
      await expect(page.locator('.doc-surface')).toContainText('S5700')
      await expect(page.locator('.doc-surface')).not.toContainText('S-5700')

      // 持久化：刷新后正文仍是规范化写法，规则卡片不再出现
      await page.waitForTimeout(1500)
      await page.reload()
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expect(page.locator('.doc-surface')).toContainText('S5700')
      await expect(page.locator('.doc-surface')).not.toContainText('S-5700')
      await expect(card).toHaveCount(0)
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L8 whitelists：多值只锚定非法值、不可接受，改正后提醒消失', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L8-${Date.now()}`, [{
        id: 'e2e-l8-whitelist',
        name: 'E2E 端口类型白名单',
        enabled: true,
        severity: 'warn',
        whitelists: [{
          anchors: ['端口类型：'],
          values: ['GE', '10GE', '100GE'],
          message: '端口类型只能是 GE / 10GE / 100GE',
        }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('端口类型：GE；10GE；25GE', { delay: 15 })

      const card = cardByRule(page, 'e2e-l8-whitelist')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'whitelists',
        severity: 'warn',
        quote: '25GE',
        reasonHas: '端口类型只能是',
        notice: true,
      })
      // 提醒不可接受：只有忽略按钮
      await expect(card.getByRole('button', { name: '接受' })).toHaveCount(0)

      // 25GE → 100GE：与其它用例一致的确定性选法（盲按 Backspace 会被选区竞态打歪），
      // 以卡片消失为最终判据做有界重试
      await expect(async () => {
        await replaceInParagraph(page, '端口类型', 13, 4, '100GE')
        await expect(card).toHaveCount(0, { timeout: 2_000 })
      }).toPass({ timeout: 20_000 })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L9 patterns：单位格式不合规时提醒，补齐 bps 后消失', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L9-${Date.now()}`, [{
        id: 'e2e-l9-unit',
        name: 'E2E 带宽单位',
        enabled: true,
        severity: 'warn',
        patterns: [{
          extract: '\\d+\\s*[MG](?:bps)?',
          valid: '^\\d+\\s*[MG]bps$',
          message: '单位需写 Mbps / Gbps',
        }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('带宽 10G。', { delay: 15 })

      const card = cardByRule(page, 'e2e-l9-unit')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'patterns',
        severity: 'warn',
        quote: '10G',
        reasonHas: '单位需写 Mbps',
        notice: true,
      })

      // 带宽 10G。 → 选中 10G 替换为 10Gbps
      await replaceInParagraph(page, '带宽', 3, 3, '10Gbps')
      await expect(card).toHaveCount(0, { timeout: 5_000 })
      await expect(page.locator('.doc-surface')).toContainText('带宽 10Gbps。')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L10 patterns：日期格式不合规时提醒，改成 YYYY-MM-DD 后消失', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L10-${Date.now()}`, [{
        id: 'e2e-l10-date',
        name: 'E2E 日期格式',
        enabled: true,
        severity: 'info',
        patterns: [{
          extract: '\\d{4}/\\d{1,2}/\\d{1,2}',
          valid: '^\\d{4}-\\d{2}-\\d{2}$',
          message: '日期请写成 YYYY-MM-DD',
        }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('计划 2026/10/1 上线。', { delay: 15 })

      const card = cardByRule(page, 'e2e-l10-date')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'patterns',
        severity: 'info',
        quote: '2026/10/1',
        reasonHas: '日期请写成',
        notice: true,
      })

      await replaceInParagraph(page, '计划', 3, 9, '2026-10-01')
      await expect(card).toHaveCount(0, { timeout: 5_000 })
      await expect(page.locator('.doc-surface')).toContainText('计划 2026-10-01 上线。')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L11 presence：缺配套说明时提醒，补「不涉及拖带」后立即消失', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L11-${Date.now()}`, [{
        id: 'e2e-l11-presence',
        name: 'E2E 拖带完整性',
        enabled: true,
        severity: 'warn',
        presence: [{
          triggers: ['9600M12'],
          requiresAny: ['9600UPS', '不涉及拖带'],
          message: '提到 9600M12 时须写明 9600UPS 或声明不涉及拖带',
        }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('9600M12 组网。', { delay: 15 })

      const card = cardByRule(page, 'e2e-l11-presence')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'presence',
        severity: 'warn',
        quote: '9600M12',
        reasonHas: '拖带完整性',
        notice: true,
      })
      await expect(card.getByRole('button', { name: '接受' })).toHaveCount(0)

      await appendFast(page, '本方案不涉及拖带。')
      await expect(card).toHaveCount(0, { timeout: 5_000 })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L12 presence：功能开启缺许可/回滚说明，补齐后消失', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L12-${Date.now()}`, [{
        id: 'e2e-l12-license',
        name: 'E2E 许可与回滚',
        enabled: true,
        severity: 'error',
        presence: [{
          triggers: ['智能分析'],
          requiresAny: ['许可', '授权', '回滚'],
          message: '开启智能分析须说明许可/授权与回滚方案',
        }],
      }])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('开启智能分析功能。', { delay: 15 })

      const card = cardByRule(page, 'e2e-l12-license')
      await expectCardShape(card, {
        source: 'local',
        mechanism: 'presence',
        severity: 'error',
        quote: '智能分析',
        reasonHas: '许可/授权',
        notice: true,
      })

      await appendFast(page, '已获授权，回滚见 5.2。')
      await expect(card).toHaveCount(0, { timeout: 5_000 })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L13 严重级别：error/warn/info 三条规则分别渲染错误/建议/提示徽标', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L13-${Date.now()}`, [
        {
          id: 'e2e-l13-error',
          name: 'E2E 错误级',
          enabled: true,
          severity: 'error',
          maps: [{ wrong: '错写甲', right: '正确甲' }],
        },
        {
          id: 'e2e-l13-warn',
          name: 'E2E 警告级',
          enabled: true,
          severity: 'warn',
          maps: [{ wrong: '错写乙', right: '正确乙' }],
        },
        {
          id: 'e2e-l13-info',
          name: 'E2E 提示级',
          enabled: true,
          severity: 'info',
          maps: [{ wrong: '错写丙', right: '正确丙' }],
        },
      ])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('错写甲 错写乙 错写丙。', { delay: 15 })

      const expectations: Array<[string, 'error' | 'warn' | 'info', string]> = [
        ['e2e-l13-error', 'error', '错误'],
        ['e2e-l13-warn', 'warn', '建议'],
        ['e2e-l13-info', 'info', '提示'],
      ]
      for (const [ruleId, severity, label] of expectations) {
        const card = cardByRule(page, ruleId)
        await expectCardShape(card, { source: 'local', mechanism: 'maps', severity })
        await expect(card.locator('.se-suggest-note-severity')).toHaveText(label)
      }
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L14 禁用规则：enabled=false 不命中；启用并广播后卡片立即出现', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    const rule: LocalCheckRule = {
      id: 'e2e-l14-toggle',
      name: 'E2E 可开关映射',
      enabled: false,
      severity: 'warn',
      maps: [{ wrong: 'Z-9', right: 'Z9' }],
    }
    try {
      docId = await openWithRules(page, `E2E-L14-${Date.now()}`, [rule])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('型号 Z-9 待定。', { delay: 15 })

      // 禁用规则不计入徽标、也不产生卡片
      await page.waitForTimeout(1200)
      await expect(page.locator('.se-rules-badge')).toHaveText('规则 0')
      await expect(cardByRule(page, 'e2e-l14-toggle')).toHaveCount(0)

      // 重新启用 + 广播（与 RuleManagerDialog 保存后的消息完全一致）
      await putGlobalRules(api, token, [{ ...rule, enabled: true }])
      await page.evaluate(() => {
        const channel = new BroadcastChannel('suggest-editor-rules')
        channel.postMessage({ type: 'changed' })
        channel.close()
      })
      await expectRuleBadge(page, 1)
      const card = cardByRule(page, 'e2e-l14-toggle')
      await expect(card).toBeVisible({ timeout: 10_000 })
      await expect(card).toHaveAttribute('data-quote', 'Z-9')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L15 接受 vs 忽略：接受写回正文并持久化；忽略的提醒刷新后重新计算', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L15-${Date.now()}`, [
        {
          id: 'e2e-l15-map',
          name: 'E2E 型号映射',
          enabled: true,
          severity: 'error',
          maps: [{ wrong: 'S-5701', right: 'S5701' }],
        },
        {
          id: 'e2e-l15-notice',
          name: 'E2E 端口白名单',
          enabled: true,
          severity: 'warn',
          whitelists: [{
            anchors: ['端口类型：'],
            values: ['GE'],
            message: '端口类型只能是 GE',
          }],
        },
      ])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('型号 S-5701，端口类型：10GE。', { delay: 15 })

      const mapCard = cardByRule(page, 'e2e-l15-map')
      const noticeCard = cardByRule(page, 'e2e-l15-notice')
      await expectCardShape(mapCard, { source: 'local', mechanism: 'maps', severity: 'error', quote: 'S-5701' })
      await expectCardShape(noticeCard, { source: 'local', mechanism: 'whitelists', severity: 'warn', quote: '10GE', notice: true })

      await acceptCard(page, mapCard)
      await expect(page.locator('.doc-surface')).toContainText('S5701')
      await expect(page.locator('.doc-surface')).not.toContainText('S-5701')

      // 本地提醒的「忽略」是会话级的：当前页面消失
      await ignoreCard(page, noticeCard)

      // 刷新后：接受的结果持久化；被忽略的本地提醒按设计重新计算并出现
      await page.waitForTimeout(1500)
      await page.reload()
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expect(page.locator('.doc-surface')).toContainText('S5701')
      await expect(page.locator('.doc-surface')).not.toContainText('S-5701')
      await expect(cardByRule(page, 'e2e-l15-notice')).toBeVisible({ timeout: 20_000 })
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L16 表格单元格：内置格式规则在单元格文本节点内命中并可接受', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    try {
      docId = await openWithRules(page, `E2E-L16-${Date.now()}`, [])

      await page.locator('.doc-surface').click()
      await page.keyboard.type('/')
      await page.keyboard.type('biaoge')
      const menu = page.locator('.se-slash-menu')
      await expect(menu).toBeVisible({ timeout: 10_000 })
      await menu.locator('.slash-item', { hasText: '表格' }).click()
      await expect(page.locator('.doc-surface table')).toBeVisible({ timeout: 10_000 })

      await page.locator('.doc-surface th').first().click()
      await page.keyboard.type('完成。。  继续', { delay: 15 })

      const dupPunct = cardByRule(page, 'dup-punct')
      const doubleSpace = cardByRule(page, 'double-space')
      await expectCardShape(dupPunct, {
        source: 'local', mechanism: 'builtin', severity: 'error', quote: '。。', reasonHas: '重复标点',
      })
      await expectCardShape(doubleSpace, {
        source: 'local', mechanism: 'builtin', severity: 'error', quote: '  ', reasonHas: '连续空格',
      })

      await acceptCard(page, dupPunct)
      await acceptCard(page, doubleSpace)
      await expect(page.locator('.doc-surface th').first()).toHaveText('完成。 继续')
    } finally {
      if (docId) await deleteDocument(api, token, docId)
    }
  })

  test('L17 内置规则临时屏蔽：规则对话框关闭「连续空格」，即时生效、刷新后仍屏蔽、可恢复', async ({ page }) => {
    test.setTimeout(180_000)
    let docId = ''
    const clearDisable = () => page.evaluate(() =>
      localStorage.removeItem('check-manage:se:disabled-local-rules')).catch(() => {})
    try {
      docId = await openWithRules(page, `E2E-L17-${Date.now()}`, [])
      await page.locator('.doc-surface').click()
      await page.keyboard.type('方案  说明。', { delay: 15 })
      const card = cardByRule(page, 'double-space')
      await expectCardShape(card, {
        source: 'local', mechanism: 'builtin', severity: 'error', quote: '  ', reasonHas: '连续空格',
      })

      // 规则对话框底部「内置格式规则」区：关闭「连续空格」
      await page.getByRole('button', { name: '规则', exact: true }).click()
      const rulesDialog = page.locator('.el-dialog').filter({
        has: page.locator('.el-dialog__title', { hasText: '检查规则' }),
      })
      await rulesDialog.waitFor({ state: 'visible' })
      await rulesDialog.locator('.builtin-rule-row', { hasText: '连续空格' }).locator('.el-switch').click()

      // 开关写 localStorage 后广播重载，卡片即时消失（对话框开着也能看到）
      await expect(card).toHaveCount(0, { timeout: 10_000 })
      await rulesDialog.getByRole('button', { name: '关闭', exact: true }).click()
      await rulesDialog.waitFor({ state: 'hidden', timeout: 10_000 })

      // 刷新后仍屏蔽：同一处双空格不再出卡片（localStorage 持久化）
      await page.reload()
      await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
      await expectRuleBadge(page, 0)
      await page.waitForTimeout(800)
      await expect(card).toHaveCount(0)

      // 重新打开开关：卡片恢复
      await page.getByRole('button', { name: '规则', exact: true }).click()
      await rulesDialog.waitFor({ state: 'visible' })
      await rulesDialog.locator('.builtin-rule-row', { hasText: '连续空格' }).locator('.el-switch').click()
      await expect(card).toBeVisible({ timeout: 10_000 })
    } finally {
      // 兜底清理屏蔽状态，避免污染后续用例（测试正常路径结束时已重新启用）
      await clearDisable()
      if (docId) await deleteDocument(api, token, docId)
    }
  })
})
