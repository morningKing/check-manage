/**
 * 数据管理全量 E2E（data-full）共享助手。
 *
 * 约定（spec: docs/superpowers/specs/2026-10-06-data-management-e2e-design.md）：
 * - 测试资产一律 DTEST- 前缀 + 时间戳（数据页名称全局唯一，防撞名）。
 * - menu 删除不级联 page_configs/dynamic_data —— deleteDataPage 二段删：
 *   记录 → pageConfig → menu（TD-A20 验证过该顺序）。
 * - viewConfig 无法随 POST /pageConfigs 落库（INSERT 未含该列），
 *   createDataPage 的 opts.viewConfig 走 PUT 补写。
 * - 截图证据 e2e/screenshots/data-full/。
 */
import fs from 'node:fs'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'

export const AUTH_FILE = 'e2e/.auth/admin.json'
export const SHOT_DIR = 'e2e/screenshots/data-full'

export interface FieldLite {
  id: string
  label: string
  fieldName: string
  controlType: string
  required: boolean
  order: number
  placeholder?: string
  options?: { label: string; value: string }[]
}

/** 族A 标准字段（placeholder 同时是 UI 用例的定位锚点） */
export const CRUD_FIELDS: FieldLite[] = [
  { id: 'f1', label: '名称', fieldName: 'name', controlType: 'text',
    required: true, order: 1, placeholder: '请输入名称' },
  { id: 'f2', label: '数量', fieldName: 'qty', controlType: 'number',
    required: false, order: 2, placeholder: '请输入数量' },
  { id: 'f3', label: '状态', fieldName: 'status', controlType: 'select',
    required: false, order: 3, placeholder: '请选择状态',
    options: [
      { label: '待处理', value: 'todo' },
      { label: '进行中', value: 'doing' },
      { label: '已完成', value: 'done' },
    ] },
]

export interface DataPageHandle {
  collection: string
  pageId: string
  menuId: string
  path: string
  name: string
}

let cachedToken: string | null = null

export async function adminToken(request: APIRequestContext): Promise<string> {
  if (cachedToken) return cachedToken
  const res = await request.post('/api/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  if (res.status() !== 200) throw new Error(`login failed: ${res.status()}`)
  cachedToken = (await res.json()).token
  return cachedToken!
}

export async function api(request: APIRequestContext, method: string,
                          path: string, data?: unknown):
                          Promise<{ status: number; json: any; headers: any }> {
  const token = await adminToken(request)
  const res = await request.fetch(`/api${path}`, {
    method,
    data: data === undefined ? undefined : JSON.stringify(data),
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
  })
  let json: any = null
  try { json = await res.json() } catch { /* 204/二进制 */ }
  return { status: res.status(), json, headers: res.headers() }
}

export function tag(family: string, purpose: string): string {
  return `DTEST-${family}-${purpose}-${Date.now()}`
}

export async function createDataPage(request: APIRequestContext,
                                     family: string, purpose: string,
                                     fields: FieldLite[] = CRUD_FIELDS,
                                     opts: { viewConfig?: object } = {}):
                                     Promise<DataPageHandle> {
  const collection = tag(family, purpose)
  const pageId = `page-${collection}`
  const name = collection // 数据页名称全局唯一，直接用 collection 串
  const pc = await api(request, 'POST', '/pageConfigs', {
    id: pageId, name,
    description: `数据管理 e2e ${family}/${purpose}`,
    apiEndpoint: `/${collection}`, fields,
  })
  if (pc.status !== 201) {
    throw new Error(`create pageConfig failed: ${pc.status} ${JSON.stringify(pc.json)}`)
  }
  if (opts.viewConfig) {
    const put = await api(request, 'PUT', `/pageConfigs/${pageId}`,
                          { viewConfig: opts.viewConfig })
    if (put.status >= 300) {
      await api(request, 'DELETE', `/pageConfigs/${pageId}`)
      throw new Error(`apply viewConfig failed: ${put.status} ${JSON.stringify(put.json)}`)
    }
  }
  const menu = await api(request, 'POST', '/menus', {
    id: `menu-${collection}`, name, pageId,
    path: `/dtest/${collection}`, menuType: 'data',
    roles: ['admin', 'developer', 'guest'], order: 9999,
  })
  if (menu.status !== 201) {
    await api(request, 'DELETE', `/pageConfigs/${pageId}`)
    throw new Error(`create menu failed: ${menu.status} ${JSON.stringify(menu.json)}`)
  }
  return { collection, pageId, menuId: menu.json.id ?? `menu-${collection}`,
           path: `/dtest/${collection}`, name }
}

export async function deleteDataPage(request: APIRequestContext,
                                     h: DataPageHandle): Promise<void> {
  // ① 记录（all=true 全量拉取后逐条删）
  const list = await api(request, 'GET', `/${h.collection}?all=true`)
  for (const rec of list.json?.data || []) {
    await api(request, 'DELETE', `/${h.collection}/${encodeURIComponent(rec.id)}`)
  }
  // ② 配置 ③ 菜单
  await api(request, 'DELETE', `/pageConfigs/${h.pageId}`)
  await api(request, 'DELETE', `/menus/${h.menuId}`)
}

export async function createRecord(request: APIRequestContext,
                                   collection: string, data: object):
                                   Promise<{ status: number; json: any }> {
  return api(request, 'POST', `/${collection}`, data)
}

export async function listRecords(request: APIRequestContext,
                                  collection: string, query = ''):
                                  Promise<{ status: number; json: any }> {
  return api(request, 'GET', `/${collection}${query}`)
}

export async function screenshot(page: Page, name: string): Promise<string> {
  fs.mkdirSync(SHOT_DIR, { recursive: true })
  const file = path.join(SHOT_DIR, `${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  return file
}

export async function gotoWithAuth(page: Page, path: string): Promise<void> {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await page.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
  await page.goto(path)
  try { await page.waitForLoadState('networkidle', { timeout: 8_000 }) }
  catch { /* 尽力等待即可 */ }
}
