/**
 * 数据管理全量 E2E（data-full）共享助手。
 *
 * 约定（spec: docs/superpowers/specs/2026-10-06-data-management-e2e-design.md）：
 * - 测试资产一律 DTEST- 前缀 + 时间戳（数据页名称全局唯一，防撞名）。
 * - menu 删除不级联 page_configs/dynamic_data —— deleteDataPage 按序回收：
 *   记录 → pageConfig → menu 链 data→project→workspace（TD-A20 验证过该顺序）。
 *   data 菜单是 level-3，必须有 project 父级（project 父级必须是 workspace），
 *   故 createDataPage 建 workspace→project→data 三级链。
 * - viewConfig 无法随 POST /pageConfigs 落库（INSERT 未含该列），
 *   createDataPage 的 opts.viewConfig 走 PUT 补写。
 * - 截图证据 e2e/screenshots/data-full/。
 */
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
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
  // 族B 控件配置（计划②预批偏差）：POST /pageConfigs 的 fields 原样落库
  // （server/routes/page_configs.py:78-80 Json(body.get('fields'))），
  // 前端 FieldConfig 读取这些键（src/types/field.ts:268-276）。
  sequenceConfig?: { prefix: string; max: number }
  compositeTextConfig?: { sourceFields: string[]; separator: string }
  workflowConfig?: {
    enabled: boolean
    transitions: { from: string; to: string; label: string; roles?: string[] }[]
  }
  fileConfig?: { allowedExtensions: string[] }
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
  // 产品约束（server/routes/menus.py，与 server/tests/data_full_live.py make_page 一致）：
  // data 菜单是 level-3，父级必须是 project，project 的父级必须是 workspace ——
  // 按 workspace → project → data 三级建链；祖先菜单 id 由 collection 确定性
  // 推导（menu-ws-*/menu-proj-*），deleteDataPage 据此回收整条链。
  const wsId = `menu-ws-${collection}`
  const ws = await api(request, 'POST', '/menus', {
    id: wsId, name: `${collection}-ws`, menuType: 'workspace',
    path: `/dtest-ws/${collection}`, order: 9999,
  })
  if (ws.status !== 201) {
    await api(request, 'DELETE', `/pageConfigs/${pageId}`)
    throw new Error(`create workspace menu failed: ${ws.status} ${JSON.stringify(ws.json)}`)
  }
  const projId = `menu-proj-${collection}`
  const proj = await api(request, 'POST', '/menus', {
    id: projId, name: `${collection}-proj`, menuType: 'project',
    parentId: wsId, path: `/dtest-proj/${collection}`, order: 9999,
  })
  if (proj.status !== 201) {
    await api(request, 'DELETE', `/menus/${wsId}`)
    await api(request, 'DELETE', `/pageConfigs/${pageId}`)
    throw new Error(`create project menu failed: ${proj.status} ${JSON.stringify(proj.json)}`)
  }
  const menu = await api(request, 'POST', '/menus', {
    id: `menu-${collection}`, name, pageId, parentId: projId,
    path: `/dtest/${collection}`, menuType: 'data',
    roles: ['admin', 'developer', 'guest'], order: 9999,
  })
  if (menu.status !== 201) {
    await api(request, 'DELETE', `/menus/${projId}`)
    await api(request, 'DELETE', `/menus/${wsId}`)
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
  // ② 配置 ③ 菜单链（data → project → workspace，menu 删除不级联）
  await api(request, 'DELETE', `/pageConfigs/${h.pageId}`)
  await api(request, 'DELETE', `/menus/${h.menuId}`)
  await api(request, 'DELETE', `/menus/menu-proj-${h.collection}`)
  await api(request, 'DELETE', `/menus/menu-ws-${h.collection}`)
}

export async function createRecord(request: APIRequestContext,
                                   collection: string, data: object):
                                   Promise<{ status: number; json: any }> {
  // dynamic_data.id 为 NOT NULL 且无默认值——载荷缺 id 时服务端 500
  // （NotNullViolation）。产品 UI 在客户端生成 id/createdAt
  // （stores/pageConfig.ts addPageData：`${endpoint}-${uuid 前 8}`），这里对齐。
  const payload = { ...(data as Record<string, unknown>) }
  // id 与 createdAt 各自独立补缺——调用方显式传入的值（如早 createdAt 控制
  // 列表排序）不得被覆盖。
  if (payload.id == null || payload.id === '') {
    payload.id = `${collection}-${randomUUID().slice(0, 8)}`
  }
  if (payload.createdAt == null || payload.createdAt === '') {
    payload.createdAt = new Date().toISOString()
  }
  return api(request, 'POST', `/${collection}`, payload)
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
