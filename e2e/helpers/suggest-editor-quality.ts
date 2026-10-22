/**
 * 智能写作质量检查 E2E 的公共辅助。
 *
 * 设计约束（对应 docs/superpowers/specs/2026-10-22-suggest-editor-quality-e2e-design.md）：
 * - 状态自包含：全局规则/检查要点/词表在 beforeAll 抓快照，beforeEach 复位，afterAll 恢复；
 * - 用例只经 API 建文档/写规则/写词表，finally 删除文档；
 * - 断言优先用卡片上的 data-rule-id / data-source / data-mechanism / data-severity，
 *   文案只做辅助（避免把提示语当契约）。
 */
import fs from 'node:fs'
import path from 'node:path'
import {
  expect,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from '@playwright/test'

export const API_BASE = 'http://127.0.0.1:3002'
const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']

/** 与「无任何检查项」等价的检查要点内容：Node 侧只读非注释行 → 空清单 */
export const EMPTY_CHECKLIST = '# 本地规则 E2E：本文件不注入任何检查要点\n'

export interface GlobalState {
  rules: unknown[]
  checklist: string
  vocabulary: string
}

export interface LocalCheckRule {
  id: string
  name: string
  enabled: boolean
  severity: 'info' | 'warn' | 'error'
  aiPrompt?: string
  maps?: { wrong: string; right: string; caseSensitive?: boolean }[]
  patterns?: { extract: string; valid: string; message: string; flags?: string }[]
  whitelists?: { anchors: string[]; values: string[]; valuePattern?: string; message: string }[]
  presence?: { triggers: string[]; requiresAny: string[]; message: string }[]
}

/* ------------------------------- 登录态 ------------------------------- */

/** 每个 spec 的 beforeAll 调一次：真实登录并写回共享 e2e/.auth/admin.json */
export async function ensureSharedLogin(browser: Browser): Promise<void> {
  fs.mkdirSync(AUTH_DIR, { recursive: true })
  let auth: Record<string, string> | null = null
  for (let attempt = 0; attempt < 3 && !auth; attempt += 1) {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    try {
      await page.goto('/')
      await page.fill('input[placeholder*="用户名"]', 'admin')
      await page.fill('input[placeholder*="密码"]', 'admin123')
      await page.getByRole('button', { name: /登\s*录/ }).click()
      await page.waitForFunction(
        () => !!localStorage.getItem('check-manage:token')
          && !!localStorage.getItem('check-manage:userInfo'),
        undefined,
        { timeout: 30_000 },
      )
      await page.waitForTimeout(1500)
      auth = await page.evaluate((keys) => {
        const out: Record<string, string> = {}
        for (const key of keys) {
          const value = localStorage.getItem(key)
          if (value) out[key] = value
        }
        return out
      }, AUTH_KEYS)
    } catch {
      /* 重试 */
    }
    await ctx.close()
  }
  if (!auth) throw new Error('共享登录失败：admin/admin123')
  fs.writeFileSync(AUTH_FILE, JSON.stringify(auth))
}

/** beforeEach：把共享登录态注入新 context（保持与既有 spec 一致的写法） */
export async function installSharedAuth(context: BrowserContext): Promise<void> {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8')) as Record<string, string>
  await context.addInitScript((entries) => {
    for (const [key, value] of Object.entries(entries)) localStorage.setItem(key, value)
  }, auth)
}

export async function tokenOf(page: Page): Promise<string> {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem('check-manage:token') || '""')) as Promise<string>
}

/** 不依赖页面拿 token（beforeAll / request-only 上下文用） */
export function readSharedToken(): string {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8')) as Record<string, string>
  const token = auth['check-manage:token']
  if (!token) throw new Error('e2e/.auth/admin.json 缺少 token，请先执行登录')
  return JSON.parse(token) as string
}

export function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

/* ------------------------------ API 小封装 ------------------------------ */

export async function apiGet<T>(
  api: APIRequestContext, token: string, url: string,
): Promise<T> {
  const res = await api.get(`${API_BASE}${url}`, { headers: authHeaders(token) })
  expect(res.ok(), `GET ${url} -> ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()) as T
}

export async function apiPut<T>(
  api: APIRequestContext, token: string, url: string, data: unknown,
): Promise<T> {
  const res = await api.put(`${API_BASE}${url}`, { headers: authHeaders(token), data })
  expect(res.ok(), `PUT ${url} -> ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()) as T
}

export async function apiDelete(
  api: APIRequestContext, token: string, url: string,
): Promise<void> {
  const res = await api.delete(`${API_BASE}${url}`, { headers: authHeaders(token) })
  expect(res.ok(), `DELETE ${url} -> ${res.status()} ${await res.text()}`).toBeTruthy()
}

/* ------------------------------ 全局状态 ------------------------------ */

export async function getGlobalRules(
  api: APIRequestContext, token: string,
): Promise<LocalCheckRule[]> {
  return (await apiGet<{ rules?: LocalCheckRule[] }>(
    api, token, '/suggest-editor/rules/global',
  )).rules ?? []
}

export async function putGlobalRules(
  api: APIRequestContext, token: string, rules: LocalCheckRule[],
): Promise<void> {
  await apiPut(api, token, '/suggest-editor/rules/global', { rules })
}

export async function getChecklist(api: APIRequestContext, token: string): Promise<string> {
  return (await apiGet<{ content?: string }>(
    api, token, '/suggest-editor/checklists/global',
  )).content ?? ''
}

export async function putChecklist(
  api: APIRequestContext, token: string, content: string,
): Promise<void> {
  await apiPut(api, token, '/suggest-editor/checklists/global', { content })
}

export async function getGlobalVocabulary(
  api: APIRequestContext, token: string,
): Promise<string> {
  return (await apiGet<{ content?: string }>(
    api, token, '/suggest-editor/vocabularies/global',
  )).content ?? ''
}

export async function putGlobalVocabulary(
  api: APIRequestContext, token: string, content: string,
): Promise<void> {
  await apiPut(api, token, '/suggest-editor/vocabularies/global', { content })
}

export async function getDocVocabulary(
  api: APIRequestContext, token: string, docId: string,
): Promise<string> {
  return (await apiGet<{ content?: string }>(
    api, token, `/suggest-editor/documents/${docId}/vocabulary`,
  )).content ?? ''
}

export async function putDocVocabulary(
  api: APIRequestContext, token: string, docId: string, content: string,
): Promise<void> {
  await apiPut(api, token, `/suggest-editor/documents/${docId}/vocabulary`, { content })
}

export async function putDocRules(
  api: APIRequestContext, token: string, docId: string, rules: LocalCheckRule[],
): Promise<void> {
  await apiPut(api, token, `/suggest-editor/documents/${docId}/rules`, { rules })
}

/** 抓取全局规则 / 检查要点 / 词表快照 */
export async function captureGlobalState(
  api: APIRequestContext, token: string,
): Promise<GlobalState> {
  return {
    rules: await getGlobalRules(api, token),
    checklist: await getChecklist(api, token),
    vocabulary: await getGlobalVocabulary(api, token),
  }
}

/** 原样恢复快照（beforeEach 复位 + afterAll 收尾都用它） */
export async function restoreGlobalState(
  api: APIRequestContext, token: string, state: GlobalState,
): Promise<void> {
  await putGlobalRules(api, token, state.rules as LocalCheckRule[])
  await putChecklist(api, token, state.checklist)
  await putGlobalVocabulary(api, token, state.vocabulary)
}

/**
 * 本地规则专用环境：清空检查规则 + 检查要点为空。
 * mock agent 只在 prompt 带「检查要点 / 领域检查规则」时产出通用建议，
 * 因此这个环境里不会出现 AI 卡片，本地规则的断言不会被 AI 覆盖。
 */
export async function putLocalOnlyEnvironment(
  api: APIRequestContext, token: string,
): Promise<void> {
  await putGlobalRules(api, token, [])
  await putChecklist(api, token, EMPTY_CHECKLIST)
}

/* ------------------------------ 文档操作 ------------------------------ */

export async function listDocuments(
  api: APIRequestContext, token: string,
): Promise<Array<{ id: string; title: string }>> {
  const body = await apiGet<{ documents?: Array<{ id: string; title: string }> }>(
    api, token, '/suggest-editor/documents',
  )
  return body.documents ?? []
}

export async function createDocument(
  api: APIRequestContext,
  token: string,
  title: string,
  seedHtml?: string,
): Promise<string> {
  const res = await api.post(`${API_BASE}/suggest-editor/documents`, {
    headers: authHeaders(token),
    data: { title },
  })
  expect(res.ok(), `POST documents -> ${res.status()} ${await res.text()}`).toBeTruthy()
  const created = (await res.json()) as { document: { id: string } }
  const id = created.document.id
  if (seedHtml !== undefined) {
    await apiPut(api, token, `/suggest-editor/documents/${id}/seed`, { contentHtml: seedHtml })
  }
  return id
}

export async function deleteDocument(
  api: APIRequestContext, token: string, docId: string,
): Promise<void> {
  const res = await api.delete(`${API_BASE}/suggest-editor/documents/${docId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok() && res.status() !== 404) {
    throw new Error(`DELETE document ${docId} -> ${res.status()} ${await res.text()}`)
  }
}

/** 按标题兜底清理（用例在建文档前失败时也能复用） */
export async function deleteDocsByTitle(
  api: APIRequestContext, token: string, title: string,
): Promise<void> {
  for (const doc of (await listDocuments(api, token)).filter((item) => item.title === title)) {
    await deleteDocument(api, token, doc.id)
  }
}

/* ------------------------------ 编辑器操作 ------------------------------ */

/** 从菜单进入智能写作列表页 */
export async function gotoSuggestList(page: Page): Promise<void> {
  await page.goto('/home')
  const menuItem = page.locator('.el-menu-item', { hasText: '智能写作' }).first()
  await menuItem.waitFor({ state: 'visible', timeout: 30_000 })
  await menuItem.click()
  await expect(page.getByRole('button', { name: '新建文档' })).toBeVisible({ timeout: 15_000 })
}

/** 直接打开文档编辑页，并等到 WS 同步完成（顶栏徽标「已同步」） */
export async function openDocById(page: Page, docId: string): Promise<void> {
  await page.goto(`/suggest-editor/${docId}`)
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 30_000 })
  await page.waitForTimeout(400)
}

/** 等到规则徽标显示指定条数（= 前端已拉取并编译完规则） */
export async function expectRuleBadge(page: Page, count: number): Promise<void> {
  await expect(page.locator('.se-rules-badge')).toHaveText(`规则 ${count}`, { timeout: 20_000 })
}

/** 等到导入基线建立（导入文档首轮不调用模型） */
export async function expectImportBaseline(page: Page): Promise<void> {
  await expect(page.locator('.se-status')).toContainText('已建立导入基线', { timeout: 60_000 })
}

/** 在正文末尾输入（先点进正文确保编辑器获得焦点） */
export async function typeAtEnd(page: Page, text: string): Promise<void> {
  await page.locator('.doc-surface').click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type(text, { delay: 15 })
}

/** 在正文末尾追加并立即返回（不做等待，用于需要同一轮脏块的场景） */
export async function appendFast(page: Page, text: string): Promise<void> {
  await page.locator('.doc-surface').click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type(text, { delay: 0 })
}

/** 卡片定位：优先 data-rule-id，其次 data-source + 文案 */
export function cardByRule(page: Page, ruleId: string): Locator {
  return page.locator(`.se-suggest-note[data-rule-id="${ruleId}"]`)
}

export function cardBySource(page: Page, source: 'local' | 'ai' | 'rewrite'): Locator {
  return page.locator(`.se-suggest-note[data-source="${source}"]`)
}

/** 接受卡片并等到它消失（接受后规则不再命中） */
export async function acceptCard(page: Page, card: Locator): Promise<void> {
  await expect(card).toBeVisible({ timeout: 10_000 })
  await card.getByRole('button', { name: '接受' }).click()
  await expect(card).toHaveCount(0, { timeout: 10_000 })
}

/** 忽略卡片并等到它消失 */
export async function ignoreCard(page: Page, card: Locator): Promise<void> {
  await expect(card).toBeVisible({ timeout: 10_000 })
  await card.getByRole('button', { name: '忽略' }).click()
  await expect(card).toHaveCount(0, { timeout: 10_000 })
}

/** 断言卡片的关键属性（来源/机制/严重度/锚定文本），文案只作辅助 */
export async function expectCardShape(
  card: Locator,
  expected: {
    source?: 'local' | 'ai' | 'rewrite'
    mechanism?: string
    severity?: 'info' | 'warn' | 'error'
    quote?: string
    reasonHas?: string
    notice?: boolean
  },
): Promise<void> {
  await expect(card).toHaveCount(1)
  if (expected.source) await expect(card).toHaveAttribute('data-source', expected.source)
  if (expected.mechanism) await expect(card).toHaveAttribute('data-mechanism', expected.mechanism)
  if (expected.severity) await expect(card).toHaveAttribute('data-severity', expected.severity)
  if (expected.quote !== undefined) {
    await expect(card).toHaveAttribute('data-quote', expected.quote)
    await expect(card.locator('.se-suggest-note-quote')).toContainText(expected.quote)
  }
  if (expected.reasonHas) await expect(card.locator('.se-suggest-note-reason')).toContainText(expected.reasonHas)
  if (expected.notice !== undefined) {
    await expect(card).toHaveAttribute('data-notice', expected.notice ? 'true' : 'false')
  }
}

/** 写入可复现的 docx fixture（供导入用例复用） */
export async function writeDocxFixture(
  filePath: string,
  build: (helpers: typeof import('docx')) => import('docx').Document,
): Promise<void> {
  const docx = await import('docx')
  fs.writeFileSync(filePath, await docx.Packer.toBuffer(build(docx)))
}
