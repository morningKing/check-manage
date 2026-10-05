# 数据管理 E2E 测试体系 · 计划①：脚手架 + 族A 打样 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起数据管理全量 E2E 体系的三层脚手架（L3 helpers、L2 live-server 助手、docs/data-testing 文档骨架），并用族A（动态数据 CRUD + 三视图）打样跑绿，验证全套约定可行。

**Architecture:** 镜像 ai-full 模式——L3 Playwright 真实链路（API 断言为主 + 关键 UI 截图留证，三视图必须真实浏览器操作），L2 pytest 打真实 dev 后端 :3002（服务不在则 skip），四件套文档随族补充。测试资产全部 `DTEST-` 前缀，用例自建 PageConfig+Menu，清理二段删（记录→pageConfig→menu，因 menu 删除不级联）。

**Tech Stack:** Playwright + TypeScript（L3）、pytest + requests（L2）、Flask :3002 + Vite :5173 + PostgreSQL。

**Spec:** [docs/superpowers/specs/2026-10-06-data-management-e2e-design.md](../specs/2026-10-06-data-management-e2e-design.md)（八族范围与全部约定；本计划只覆盖族A + 脚手架，族B–H 由计划②–⑤接续）

## Global Constraints

- 测试数据前缀一律 `DTEST-<族>-<用途>-<时间戳>`（数据页名称全局唯一，时间戳防撞名）；禁止触碰任何非 `DTEST-` 数据。
- 后端 Flask :3002 **无自动 reload**——改后端代码必须手动重启；前端 Vite :5173。启动：`npm run dev:all`。
- L3 跑法：`npx playwright test e2e/data-full`（根 playwright.config.ts，workers=1）；L2 跑法：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_data_full_crud.py -v`（Windows）。L2 打真实服务，**服务不在时 skip 而非 fail**。
- 截图证据：`e2e/screenshots/data-full/`；文档：`docs/data-testing/`。
- 用例编号 `TD-<族><序号>`，L2/L3 共用同一编号体系（02 文档登记）。
- 已核实的 API 事实（勿凭记忆改写）：
  - collection = pageConfig.id 去掉 `page-` 前缀；数据端点 `/api/<collection>`（无前缀 catch-all，RESERVED 集合排除保留路径）。
  - 列表：`GET /api/<collection>?page=1&pageSize=50&all=true&keyword=xx&q=<mongo>`，响应 `{data:[...], total}`；记录含 `id/createdAt/updatedAt/_version`。
  - 创建：`POST /api/<collection>` 201 返回请求体；主键重复 409；带过期 `_version` 的 PUT → 409 `{"error":"数据已被其他用户修改，请刷新后重试","code":"VERSION_CONFLICT","_version":<db>}`。
  - 批量：`POST /api/<collection>/batch-create` `{records:[...], options?}`；`POST /api/<collection>/batch-delete` `{ids:[...]}`。
  - `POST /pageConfigs` **不落 viewConfig**（表有该列但 INSERT 未写）——需要 viewConfig（看板等）必须 POST 后再 `PUT /pageConfigs/<id>`。
  - `POST /menus` body 需含 `id`；`menuType:'data'`；数据页名称全局唯一，重名 400。
  - `DELETE /menus/<id>` 只删 menus 行，**不级联** page_configs / dynamic_data。
  - 角色：`POST /roles` `{name, defaultPageAccess:'none'|'read'|'write'}` → 201 `{id,name}`；用户：`POST /users` `{username,password,displayName,role}`；`defaultPageAccess:'none'` 拒绝一切数据页动作。
  - 看板配置存 `pageConfig.viewConfig.kanban`（`{groupField, cardTitle, cardFields, columnOrder}`），前端由 `hasKanbanConfig` 决定是否显示看板页签。
- `docs/superpowers/` 在 .gitignore 中，提交 spec/plan 需 `git add -f`（`docs/data-testing/` 不在 ignore 内，正常 add）。

---

### Task 1: 分支 + L3 helpers 脚手架

**Files:**
- Create: `e2e/data-full/helpers.ts`

**Interfaces:**
- Consumes: 真实端点 `/api/auth/login`、`/api/pageConfigs`、`/api/menus`、`/api/<collection>*`。
- Produces（后续所有 L3 spec 依赖，签名勿改）:
  - `AUTH_FILE: string`、`SHOT_DIR: string`
  - `CRUD_FIELDS: FieldLite[]`（族A 标准三字段：name 文本主键非、qty 数字、status 单选）
  - `interface FieldLite { id: string; label: string; fieldName: string; controlType: string; required: boolean; order: number; placeholder?: string; options?: { label: string; value: string }[] }`
  - `interface DataPageHandle { collection: string; pageId: string; menuId: string; path: string; name: string }`
  - `adminToken(request: APIRequestContext): Promise<string>`
  - `api(request: APIRequestContext, method: string, path: string, data?: unknown): Promise<{ status: number; json: any; headers: any }>`
  - `tag(family: string, purpose: string): string` → `DTEST-<family>-<purpose>-<ms>`
  - `createDataPage(request, family: string, purpose: string, fields?: FieldLite[], opts?: { viewConfig?: object }): Promise<DataPageHandle>`
  - `deleteDataPage(request, h: DataPageHandle): Promise<void>`（二段删：记录→pageConfig→menu）
  - `createRecord(request, collection: string, data: object): Promise<{ status: number; json: any }>`
  - `listRecords(request, collection: string, query?: string): Promise<{ status: number; json: any }>`
  - `screenshot(page: Page, name: string): Promise<string>`
  - `gotoWithAuth(page: Page, path: string): Promise<void>`

- [ ] **Step 1: 建分支**

```bash
git checkout -b feat/data-e2e
```

- [ ] **Step 2: 写 helpers.ts**

```ts
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
```

- [ ] **Step 3: 编译检查**

`tsconfig.json` 只含 `src/**`，e2e 不在检查范围，需对文件单独跑 tsc：

```bash
npx tsc --noEmit --skipLibCheck --module esnext --moduleResolution bundler --target es2022 e2e/data-full/helpers.ts
```

Expected: 无输出（类型通过）。

- [ ] **Step 4: Commit**

```bash
git add e2e/data-full/helpers.ts
git commit -m "test(data-full): L3 脚手架——DTEST- 前缀 helpers 与二段删清理约定"
```

---

### Task 2: 文档骨架（01 方案 + 02 用例目录）

**Files:**
- Create: `docs/data-testing/01-测试方案.md`
- Create: `docs/data-testing/02-数据管理功能测试用例.md`

**Interfaces:**
- Consumes: spec（已确认的设计）、Task 1 的 helpers 语义。
- Produces: 后续计划②–⑤往 02 文档续写对应族用例表；03/04 文档由计划⑤建立。01 文档的「已核实环境事实」小节是全体系的事实底座。

- [ ] **Step 1: 写 01-测试方案.md**

内容以 spec 为底（可直接引用 spec 各节，不整段复制），必须包含且仅补充以下环境事实小节（其余按 spec §1–§4、§6–§10 摘要成文）：

```markdown
# 数据管理功能全量测试方案

> 版本：v1.0 ｜ 日期：2026-10-06 ｜ 范围：check-manage 数据管理八大能力族（A–H）
> 设计文档：[2026-10-06-data-management-e2e-design.md](../superpowers/specs/2026-10-06-data-management-e2e-design.md)
> 配套：[02-数据管理功能测试用例.md](./02-数据管理功能测试用例.md)（03 执行报告 / 04 缺陷记录随计划⑤建立）

## 1. 背景与目标
（摘要 spec §1：ai-full 明确排除数据管理；e2e 对动态数据页面零覆盖；本体系补齐，
镜像 ai-full 三层结构。）

## 2. 范围
（摘 spec §2 八族表 + 排除清单。）

## 3. 分层与交付物
（摘 spec §3：docs 四件套 / L2 server/tests/test_data_full_<族>.py / L3 e2e/data-full/。
补充：L2 打真实 dev :3002，服务不在 skip；L3 Playwright workers=1。）

## 4. 已核实的环境事实（2026-10-06，族A 打样期间验证）
1. `POST /pageConfigs` 不落 viewConfig（INSERT 列清单未含），需 `PUT /pageConfigs/<id>` 补写；
   看板配置存 `viewConfig.kanban`。
2. `DELETE /menus/<id>` 只删 menus 行，不级联 page_configs / dynamic_data ——
   清理必须二段删（记录 → pageConfig → menu），helpers.deleteDataPage 已固化（TD-A20）。
3. 动态数据端点为无前缀 catch-all `/api/<collection>`，RESERVED 集合排除保留路径；
   主键重复 409；过期 `_version` PUT 409 VERSION_CONFLICT。
4. 列表查询参数：page/pageSize/all/keyword/q（mongo）/ids/locateId/sort/order。
5. 数据页名称全局唯一（menus 部分唯一索引），测试名必须带时间戳。

## 5. 维度与准出 / 数据隔离 / 缺陷流程 / 实施节奏
（摘 spec §6–§10，含硬规则：新测试先在修复前 commit 上失败再修。）
```

- [ ] **Step 2: 写 02-数据管理功能测试用例.md（族A 部分）**

```markdown
# 数据管理功能测试用例

> 编号规则：TD-<族><序号>；L2=pytest（server/tests/），L3=Playwright（e2e/data-full/）。
> 族B–H 用例表由计划②–⑤补充落表。

## 族A 动态数据 CRUD + 三视图

| 编号 | 用例 | 层 | 断言要点 |
|------|------|----|----------|
| TD-A01 | 建页正路径与重名拒绝 | L2 | pageConfigs 201 + menus 201；重名数据页 400 |
| TD-A02 | 建记录与列表可见 | L2 | POST 201；GET 列表 data 含之、total≥1 |
| TD-A03 | 单条读改与版本递增 | L2 | PUT 合并字段、_version+1、updatedAt 刷新 |
| TD-A04 | 删除记录 | L2 | DELETE <300；单条 404、列表不含 |
| TD-A05 | batch-create | L2 | 3 条 total+3 |
| TD-A06 | batch-delete | L2 | 2 条 total 回落 |
| TD-A07 | 分页 | L2 | page=1&pageSize=2 时 data≤2 且 total 不变 |
| TD-A08 | keyword 搜索 | L2 | ?keyword= 命中子串 |
| TD-A09 | 不存在 collection | L2 | 404 {"error":"Not found"} 不泄漏存在性 |
| TD-A10 | 畸形 JSON | L2 | 400 且非 5xx |
| TD-A11 | 主键冲突 | L2 | 同 isPrimaryKey 值第二条 409 |
| TD-A12 | 乐观锁 | L2 | 过期 _version → 409 VERSION_CONFLICT + 回传当前版本 |
| TD-A13 | 越权 | L2 | defaultPageAccess:'none' 角色用户 GET/POST 均 403 |
| TD-A14 | 保留路径不受吞噬 | L2 | GET /api/pageConfigs 走配置端点 200 |
| TD-A15 | UI 新增/编辑/删除全链路 | L3 | DynamicForm 真实填写、表格行断言、删除确认 |
| TD-A16 | UI 搜索过滤 | L3 | 搜索框输入后行集变化 |
| TD-A17 | UI 批量删除 | L3 | 勾选→批量删除→确认→行消失 |
| TD-A18 | Excel 视图编辑回写 | L3 | Univer 单元格编辑→API 断言落库 |
| TD-A19 | 看板拖拽改分组 | L3 | 卡片跨列拖拽→API 断言 status 变化 |
| TD-A20 | menu 删除不级联与二段删 | L2 | 仅删 menu 后配置/数据仍在；helpers 顺序清理干净 |
| TD-A21 | UI 并发编辑冲突提示 | L3 | 过期提交→ElMessage「数据已被其他用户修改」 |
| TD-A22 | 超长字段值健壮性 | L2 | 1MB 文本值不 5xx |
```

- [ ] **Step 3: Commit**

```bash
git add docs/data-testing/01-测试方案.md docs/data-testing/02-数据管理功能测试用例.md
git commit -m "docs(data-testing): 测试方案与族A用例目录——环境事实五条底座"
```

---

### Task 3: L2 live-server 助手 + 族A pytest

**Files:**
- Create: `server/tests/data_full_live.py`（非 test_ 前缀，不被收集，供各族 import）
- Create: `server/tests/test_data_full_crud.py`（TD-A01–A14、A20、A22，共 16 例）

**Interfaces:**
- Consumes: Task 1 同款 API 事实（Global Constraints）。
- Produces（后续族B–H 的 pytest 文件依赖，签名勿改）:
  - `BASE: str`（env `DATA_FULL_BASE_URL` 覆盖，默认 `http://localhost:3002`）
  - `server_up() -> bool`
  - `login() -> dict`（Authorization headers）
  - `api(method: str, path: str, headers: dict | None = None, json=None) -> requests.Response`
  - `CRUD_FIELDS: list[dict]`
  - `make_page(headers: dict, family: str, purpose: str, fields=None, view_config=None) -> dict`（键：`collection/page_id/menu_id/path/name`）
  - `drop_page(headers: dict, page: dict) -> None`（二段删）

- [ ] **Step 1: 写 data_full_live.py**

```python
"""数据管理全量 E2E —— L2 live-server 公共助手。

与 server/tests/ 既有 mock-DB 单测不同：本模块打真实 dev 后端（:3002）。
服务未启动时由 fixture skip，不影响 `npm run test:server` 离线跑。
"""
import os
import time

import requests

BASE = os.environ.get('DATA_FULL_BASE_URL', 'http://localhost:3002')


def server_up() -> bool:
    try:
        return requests.get(f'{BASE}/api/auth/login', timeout=3).status_code in (200, 401, 405)
    except requests.ConnectionError:
        return False


def login() -> dict:
    r = requests.post(f'{BASE}/api/auth/login',
                      json={'username': 'admin', 'password': 'admin123'}, timeout=10)
    assert r.status_code == 200, f'login failed: {r.status()} {r.text}'
    return {'Authorization': f"Bearer {r.json()['token']}"}


def api(method: str, path: str, headers: dict | None = None, json=None) -> requests.Response:
    return requests.request(method, f'{BASE}/api{path}',
                            headers=headers, json=json, timeout=30)


CRUD_FIELDS = [
    {'id': 'f1', 'label': '名称', 'fieldName': 'name', 'controlType': 'text',
     'required': True, 'order': 1, 'placeholder': '请输入名称'},
    {'id': 'f2', 'label': '数量', 'fieldName': 'qty', 'controlType': 'number',
     'required': False, 'order': 2, 'placeholder': '请输入数量'},
    {'id': 'f3', 'label': '状态', 'fieldName': 'status', 'controlType': 'select',
     'required': False, 'order': 3, 'placeholder': '请选择状态',
     'options': [{'label': '待处理', 'value': 'todo'},
                 {'label': '进行中', 'value': 'doing'},
                 {'label': '已完成', 'value': 'done'}]},
]


def make_page(headers: dict, family: str, purpose: str,
              fields=None, view_config=None) -> dict:
    """建 PageConfig(+viewConfig) + Menu，返回句柄 dict。数据页名称全局唯一。"""
    ts = int(time.time() * 1000)
    collection = f'DTEST-{family}-{purpose}-{ts}'
    page_id = f'page-{collection}'
    body = {'id': page_id, 'name': collection,
            'description': f'数据管理 e2e {family}/{purpose}',
            'apiEndpoint': f'/{collection}',
            'fields': CRUD_FIELDS if fields is None else fields}
    r = api('POST', '/pageConfigs', headers, body)
    assert r.status_code == 201, f'pageConfig create failed: {r.status_code} {r.text}'
    if view_config is not None:
        r2 = api('PUT', f'/pageConfigs/{page_id}', headers,
                 {'viewConfig': view_config})
        assert r2.status_code < 300, f'viewConfig PUT failed: {r2.status_code} {r2.text}'
    r3 = api('POST', '/menus', headers,
             {'id': f'menu-{collection}', 'name': collection, 'pageId': page_id,
              'path': f'/dtest/{collection}', 'menuType': 'data',
              'roles': ['admin', 'developer', 'guest'], 'order': 9999})
    assert r3.status_code == 201, f'menu create failed: {r3.status_code} {r3.text}'
    return {'collection': collection, 'page_id': page_id,
            'menu_id': r3.json().get('id', f'menu-{collection}'),
            'path': f'/dtest/{collection}', 'name': collection}


def drop_page(headers: dict, page: dict) -> None:
    """二段删：记录 → pageConfig → menu（menu 删除不级联）。"""
    r = api('GET', f"/{page['collection']}?all=true", headers)
    for rec in (r.json() or {}).get('data') or []:
        api('DELETE', f"/{page['collection']}/{rec['id']}", headers)
    api('DELETE', f"/pageConfigs/{page['page_id']}", headers)
    api('DELETE', f"/menus/{page['menu_id']}", headers)
```

- [ ] **Step 2: 写 test_data_full_crud.py（TD-A01–A14、A20）**

```python
"""族A 动态数据 CRUD —— L2 live-server API 层（TD-A01–A14、A20、A22）。"""
import time

import pytest
import requests

import data_full_live as live

pytestmark = pytest.mark.data_full


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 后端 :3002 未启动（npm run dev:all 或 cd server && python app.py）')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    """整个模块共享一页 CRUD 测试页；模块结束二段删。"""
    page = live.make_page(admin, 'A', 'crud')
    yield page
    live.drop_page(admin, page)


def _ids(admin, collection, query=''):
    r = live.api('GET', f'/{collection}{query}', admin)
    assert r.status_code == 200
    return r.json()


def test_td_a01_create_page_ok_and_dup_rejected(admin):
    page = live.make_page(admin, 'A', 'page-ok')
    try:
        assert live.api('GET', '/pageConfigs', admin).status_code == 200
        dup = live.api('POST', '/menus', admin,
                       {'id': f"menu-{page['collection']}-dup",
                        'name': page['name'], 'pageId': page['page_id'],
                        'path': '/dtest/dup', 'menuType': 'data'})
        assert dup.status_code == 400  # 数据页名称全局唯一
    finally:
        live.drop_page(admin, page)


def test_td_a02_create_record_and_list(admin, pageh):
    r = live.api('POST', f"/{pageh['collection']}", admin,
                 {'name': '记录甲', 'qty': 3, 'status': 'todo'})
    assert r.status_code == 201
    assert r.json().get('id')
    body = _ids(admin, pageh['collection'])
    assert any(rec.get('name') == '记录甲' for rec in body['data'])
    assert body['total'] >= 1


def test_td_a03_get_put_version_bump(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin,
                   {'name': '记录乙', 'qty': 1}).json()
    rid = rec['id']
    got = live.api('GET', f"/{pageh['collection']}/{rid}", admin)
    assert got.status_code == 200
    v0 = got.json()['_version']
    put = live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
                   {'qty': 9, '_version': v0})
    assert put.status_code < 300
    after = live.api('GET', f"/{pageh['collection']}/{rid}", admin).json()
    assert after['qty'] == 9 and after['name'] == '记录乙'  # PUT 合并不清字段
    assert after['_version'] == v0 + 1


def test_td_a04_delete_record(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin, {'name': '记录丙'}).json()
    rid = rec['id']
    assert live.api('DELETE', f"/{pageh['collection']}/{rid}", admin).status_code < 300
    assert live.api('GET', f"/{pageh['collection']}/{rid}", admin).status_code == 404
    assert not any(x['id'] == rid for x in _ids(admin, pageh['collection'])['data'])


def test_td_a05_batch_create(admin, pageh):
    before = _ids(admin, pageh['collection'])['total']
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'name': f'批{i}'} for i in range(3)]})
    assert r.status_code < 300
    assert _ids(admin, pageh['collection'])['total'] == before + 3


def test_td_a06_batch_delete(admin, pageh):
    recs = _ids(admin, pageh['collection'])['data']
    victims = [x['id'] for x in recs if str(x.get('name', '')).startswith('批')][:2]
    assert len(victims) == 2
    before = _ids(admin, pageh['collection'])['total']
    r = live.api('POST', f"/{pageh['collection']}/batch-delete", admin,
                 {'ids': victims})
    assert r.status_code < 300
    assert _ids(admin, pageh['collection'])['total'] == before - 2


def test_td_a07_pagination(admin, pageh):
    body = _ids(admin, pageh['collection'], '?page=1&pageSize=2')
    assert len(body['data']) <= 2
    assert body['total'] == _ids(admin, pageh['collection'])['total']


def test_td_a08_keyword_search(admin, pageh):
    live.api('POST', f"/{pageh['collection']}", admin, {'name': '搜索针XYZ'})
    body = _ids(admin, pageh['collection'], '?keyword=搜索针')
    assert any(rec.get('name') == '搜索针XYZ' for rec in body['data'])


def test_td_a09_missing_collection_404_no_leak(admin):
    r = live.api('GET', '/DTEST-no-such-collection', admin)
    assert r.status_code == 404
    assert r.json()['error'] == 'Not found'


def test_td_a10_malformed_json_400(admin, pageh):
    r = requests.post(f"{live.BASE}/api/{pageh['collection']}",
                      data='{bad json', timeout=10,
                      headers={'Content-Type': 'application/json',
                               **admin})
    assert 400 <= r.status_code < 500


def test_td_a11_primary_key_conflict(admin, pageh):
    fields = [dict(f) for f in live.CRUD_FIELDS]
    fields[0]['isPrimaryKey'] = True  # name 为主键
    page = live.make_page(admin, 'A', 'pk', fields=fields)
    try:
        assert live.api('POST', f"/{page['collection']}", admin,
                        {'name': 'DUPE'}).status_code == 201
        assert live.api('POST', f"/{page['collection']}", admin,
                        {'name': 'DUPE'}).status_code == 409
    finally:
        live.drop_page(admin, page)


def test_td_a12_optimistic_lock_version_conflict(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin,
                   {'name': '并发甲'}).json()
    rid = rec['id']
    stale = live.api('GET', f"/{pageh['collection']}/{rid}", admin).json()['_version']
    live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
             {'qty': 1, '_version': stale})  # 推进到 stale+1
    r = live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
                 {'qty': 2, '_version': stale})  # 用过期版本再写
    assert r.status_code == 409
    assert r.json()['code'] == 'VERSION_CONFLICT'
    assert r.json()['_version'] == stale + 1


def test_td_a13_low_privilege_role_denied(admin, pageh):
    role_name = f"DTEST-A-role-{int(time.time() * 1000)}"
    role = live.api('POST', '/roles', admin,
                    {'name': role_name, 'defaultPageAccess': 'none'})
    assert role.status_code == 201
    rid = role.json()['id']
    uid = None
    try:
        uname = f"dtest_a_user_{int(time.time() * 1000)}"
        u = live.api('POST', '/users', admin,
                     {'username': uname, 'password': 'Dtest#12345',
                      'displayName': 'DTEST-A 越权探针', 'role': rid})
        assert u.status_code == 201, f'user create: {u.status_code} {u.text}'
        uid = u.json()['id']
        login = live.api('POST', '/auth/login', None,
                         {'username': uname, 'password': 'Dtest#12345'})
        assert login.status_code == 200, f'low-priv login failed: {login.status_code}'
        user_headers = {'Authorization': f"Bearer {login.json()['token']}"}
        for method, path in [('GET', f"/{pageh['collection']}"),
                             ('POST', f"/{pageh['collection']}")]:
            resp = live.api(method, path, user_headers,
                            {'name': 'x'} if method == 'POST' else None)
            assert resp.status_code == 403, f'{method} 应 403，得 {resp.status_code}'
    finally:
        if uid:
            live.api('DELETE', f'/users/{uid}', admin)
        live.api('DELETE', f'/roles/{rid}', admin)


def test_td_a14_reserved_path_not_swallowed(admin):
    r = live.api('GET', '/pageConfigs', admin)
    assert r.status_code == 200  # catch-all 未吞噬保留路径


def test_td_a20_menu_delete_no_cascade_and_two_phase_cleanup(admin):
    page = live.make_page(admin, 'A', 'cascade')
    rec = live.api('POST', f"/{page['collection']}", admin, {'name': '级联探针'}).json()
    try:
        assert live.api('DELETE', f"/menus/{page['menu_id']}", admin).status_code < 300
        # 配置与数据仍在 —— 证实不级联
        assert live.api('GET', f"/pageConfigs/{page['page_id']}", admin).status_code == 200
        assert live.api('GET',
                        f"/{page['collection']}/{rec['id']}",
                        admin).status_code == 200
    finally:
        live.drop_page(admin, page)  # 二段删兜底清干净
    assert live.api('GET', f"/pageConfigs/{page['page_id']}", admin).status_code == 404
    assert live.api('GET', f"/{page['collection']}/{rec['id']}", admin).status_code == 404


def test_td_a22_oversized_field_value_no_5xx(admin, pageh):
    """超长字段值（1MB 文本）：接受或 4xx 均可，唯独不许 5xx 泄堆栈。"""
    r = live.api('POST', f"/{pageh['collection']}", admin,
                 {'name': '长' * 500_000})
    assert r.status_code < 500, f'超长值应不 5xx，得 {r.status_code}: {r.text[:200]}'
```

- [ ] **Step 3: 登记收集器**

`server/pytest.ini` 的 markers 增加 `data_full: 数据管理全量 E2E（live-server，打 :3002）`；`addopts` 的默认排除改为 `-m "not stress"`（保持不变，data_full 不排除——服务不在时用例自身 skip）。即只在 `markers =` 段追加一行：

```ini
    data_full: 数据管理全量 E2E（live-server，:3002 不在则 skip）
```

- [ ] **Step 4: 起服务跑绿**

```bash
# 确保 dev 栈活着（另开终端或已有）：npm run dev:all
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_data_full_crud.py -v
```

Expected: 16 passed（TD-A01–A14 + A20 + A22），0 failed；服务未启动时应全部 skip 而非 fail。
判别力说明：本任务是**新增测试套件**（非缺陷修复），预期在当前代码上直接通过；「先失败后修」规则适用于后续发现的缺陷（spec §9）。

- [ ] **Step 5: Commit**

```bash
git add server/tests/data_full_live.py server/tests/test_data_full_crud.py server/pytest.ini
git commit -m "test(data-full): 族A L2 —— CRUD/分页/搜索/主键/乐观锁/越权/超长值 16 例（live-server）"
```

---

### Task 4: 族A L3 Playwright 用例

**Files:**
- Create: `e2e/data-full/data-crud.spec.ts`

**Interfaces:**
- Consumes: Task 1 helpers 全部导出。
- Produces: TD-A15–A19、A21 六条 UI 用例；选择器锚点（placeholder 定位表单、`.view-toggle` 切视图、`.kanban-board`、`.univer-container`）登记到 02 文档备后续族复用。

- [ ] **Step 1: 写 data-crud.spec.ts**

```ts
/**
 * 族A 动态数据 CRUD + 三视图 —— L3 真实链路（TD-A15–A19、A21）。
 * 表单控件用 placeholder 定位（helpers.CRUD_FIELDS 预埋锚点）。
 */
import { test, expect } from '@playwright/test'
import {
  api, createDataPage, createRecord, deleteDataPage, gotoWithAuth, listRecords,
  screenshot, tag,
} from './helpers'

test.setTimeout(120_000)

let h: Awaited<ReturnType<typeof createDataPage>>

test.beforeAll(async ({ request }) => {
  h = await createDataPage(request, 'A', 'ui', undefined, {
    viewConfig: { kanban: { groupField: 'status', cardTitle: 'name',
                            cardFields: ['qty'],
                            columnOrder: ['todo', 'doing', 'done'] } },
  })
})

test.afterAll(async ({ request }) => {
  if (h) await deleteDataPage(request, h)
})

test('TD-A15 UI 新增/编辑/删除全链路', async ({ page }) => {
  await gotoWithAuth(page, h.path)
  await expect(page.getByRole('button', { name: '新增' })).toBeVisible()

  // 新增
  await page.getByRole('button', { name: '新增' }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('请输入名称').fill('UI记录甲')
  await dialog.getByPlaceholder('请输入数量').fill('7')
  await dialog.getByPlaceholder('请选择状态').click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item',
                     { hasText: '待处理' }).click()
  await dialog.getByRole('button', { name: '确定' }).click()
  const row = page.locator('.table-card .el-table__body tr', { hasText: 'UI记录甲' })
  await expect(row).toBeVisible()
  await screenshot(page, 'crud-ui-created')

  // 编辑
  await row.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.getByPlaceholder('请输入数量').fill('9')
  await editDialog.getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: 'UI记录甲' }))
    .toContainText('9')

  // 删除（行内"更多"下拉 → 删除 → 确认框）
  const row2 = page.locator('.table-card .el-table__body tr', { hasText: 'UI记录甲' })
  await row2.locator('.row-actions-trigger').click()
  await page.locator('.el-dropdown-menu__item', { hasText: '删除' }).click()
  const confirmBox = page.locator('.el-dialog:visible', { hasText: '删除确认' })
  await confirmBox.getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: 'UI记录甲' }))
    .toHaveCount(0)
})

test('TD-A16 UI 搜索过滤', async ({ page, request }) => {
  await createRecord(request, h.collection, { name: '搜索针甲' })
  await createRecord(request, h.collection, { name: '无关乙' })
  await gotoWithAuth(page, h.path)
  await page.getByPlaceholder('搜索...').fill('搜索针')
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '搜索针甲' }))
    .toBeVisible()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '无关乙' }))
    .toHaveCount(0)
  await screenshot(page, 'crud-ui-search')
})

test('TD-A17 UI 批量删除', async ({ page, request }) => {
  await createRecord(request, h.collection, { name: `批量删甲-${tag('A', 'x')}` })
  await createRecord(request, h.collection, { name: `批量删乙-${tag('A', 'y')}` })
  await gotoWithAuth(page, h.path)
  await page.locator('.table-card .el-table__body tr',
                     { hasText: '批量删甲-' }).locator('input[type="checkbox"]').check()
  await page.locator('.table-card .el-table__body tr',
                     { hasText: '批量删乙-' }).locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: '批量删除' }).click()
  const confirmBox = page.locator('.el-dialog:visible, .el-message-box:visible')
  await confirmBox.getByRole('button', { name: /确定|删除/ }).click()
  await expect(page.locator('.table-card .el-table__body tr', { hasText: '批量删' }))
    .toHaveCount(0)
})

test('TD-A18 Excel 视图编辑回写', async ({ page, request }) => {
  const marker = `XLS-${Date.now()}`
  await createRecord(request, h.collection, { name: marker, qty: 1 })
  await gotoWithAuth(page, h.path)
  // 视图切换：table → excel（el-radio-button 渲染为 label 包 input[type=radio]）
  await page.locator('.view-toggle .el-radio-button:has(input[value="excel"])').click()
  await expect(page.locator('.univer-container')).toBeVisible()
  await page.waitForTimeout(3_000) // Univer 渲染稳定
  await screenshot(page, 'crud-ui-excel-view')

  // 双击「名称」列第 2 行单元格（首行表头、首列名称，1 基），追加后缀
  const box = await page.locator('.univer-container').boundingBox()
  expect(box, 'Univer 容器应有尺寸').not.toBeNull()
  // 列宽取 COLUMN_WIDTH_MAP.default=150、行高 24；若断言失败先看截图核对列序再调常量
  const cellX = box!.x + 150 * 0.5
  const cellY = box!.y + 24 * 1.5
  await page.mouse.dblclick(cellX, cellY)
  await page.keyboard.type('-改')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2_000)
  await screenshot(page, 'crud-ui-excel-edited')

  const listed = await listRecords(request, h.collection)
  const rec = (listed.json.data || []).find((r: any) =>
    String(r.name).includes(marker))
  expect(rec, 'Excel 编辑应回写落库').toBeTruthy()
  expect(String(rec.name).endsWith('-改'), `回写后名称应为 ${marker}-改，实为 ${rec?.name}`)
    .toBe(true)
})

test('TD-A19 看板拖拽改分组', async ({ page, request }) => {
  const marker = `KAN-${Date.now()}`
  await createRecord(request, h.collection, { name: marker, status: 'todo' })
  await gotoWithAuth(page, h.path)
  await page.locator('.view-toggle .el-radio-button:has(input[value="kanban"])').click()
  const board = page.locator('.kanban-board')
  await expect(board).toBeVisible()
  const card = board.locator('.kanban-card', { hasText: marker })
  await expect(card).toBeVisible()
  await screenshot(page, 'crud-ui-kanban-before')

  // vuedraggable(HTML5) 拖拽：手动鼠标序列比 dragTo 稳
  const cardBox = await card.boundingBox()
  const target = board.locator('.kanban-column', { hasText: '进行中' })
  const targetBox = await target.boundingBox()
  expect(cardBox).not.toBeNull(); expect(targetBox).not.toBeNull()
  await page.mouse.move(cardBox!.x + cardBox!.width / 2, cardBox!.y + 10)
  await page.mouse.down()
  await page.mouse.move(targetBox!.x + targetBox!.width / 2,
                        targetBox!.y + targetBox!.height / 2, { steps: 12 })
  await page.mouse.up()
  await expect(card).not.toBeInTheDocument() // 已离开 todo 列
  await expect(target.locator('.kanban-card', { hasText: marker })).toBeVisible()
  await screenshot(page, 'crud-ui-kanban-after')

  const listed = await listRecords(request, h.collection)
  const rec = (listed.json.data || []).find((r: any) => r.name === marker)
  expect(rec?.status, '拖拽后 status 应回写为 doing').toBe('doing')
})

test('TD-A21 UI 并发编辑冲突提示', async ({ page, request }) => {
  const marker = `Conflict-${Date.now()}`
  const rec = await createRecord(request, h.collection, { name: marker, qty: 1 })
  const rid = rec.json.id
  await gotoWithAuth(page, h.path)

  // 打开编辑对话框（持有旧版本），在提交前用 API 抢先改同一记录
  const row = page.locator('.table-card .el-table__body tr', { hasText: marker })
  await row.getByRole('button', { name: '编辑' }).click()
  await api(request, 'PUT', `/${h.collection}/${rid}`, { qty: 100 })

  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.getByPlaceholder('请输入数量').fill('2')
  await editDialog.getByRole('button', { name: '确定' }).click()
  // conflict.ts: ElMessage.warning('数据已被其他用户修改，请刷新后重试')
  await expect(page.locator('.el-message', { hasText: '数据已被其他用户修改' }))
    .toBeVisible({ timeout: 10_000 })
  await screenshot(page, 'crud-ui-conflict')
})
```

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full --project=chromium
```

Expected: 6 passed。已知风险点与处置：
- TD-A18 Univer 坐标：若回写断言失败，先看 `crud-ui-excel-view.png` 核对首列/表头布局，只允许调整 `cellX/cellY` 常量，不许改成纯 API 断言。
- TD-A19 拖拽不生效：加大 `steps` 或改用 `page.dragAndDrop()`；仍不行则记入 02 文档「已知限制」并改断言为「卡片可见性 + 手动 API 改值后看板列位置正确」（降级须经记录，不许静默删用例）。
- el-dialog 定位统一用 `.el-dialog:visible`；Element Plus 弹层（select 下拉/dropdown 菜单）挂在 body 下，不要在 dialog 作用域内找。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/data-crud.spec.ts
git commit -m "test(data-full): 族A L3 —— UI 增删改/搜索/批删/Excel 回写/看板拖拽/并发冲突 6 例"
```

---

### Task 5: 全量跑绿 + 文档收口

**Files:**
- Modify: `docs/data-testing/01-测试方案.md`（若 Task 3/4 发现与「环境事实」不符，修正该节）
- Modify: `docs/data-testing/02-数据管理功能测试用例.md`（补「执行结果」列：TD-A01–A20 各自 pass/skip + 关键截图文件名）

**Interfaces:**
- Produces: 族A 打样结论——helpers/L2/L3 模式定型，计划②–⑤按同样子扩族。

- [ ] **Step 1: 全量跑一遍**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_data_full_crud.py -v
npx playwright test e2e/data-full
```

Expected: L2 16 passed；L3 6 passed。截图证据在 `e2e/screenshots/data-full/`（至少 crud-ui-created / crud-ui-excel-view / crud-ui-kanban-after / crud-ui-conflict）。

- [ ] **Step 2: 02 文档补执行结果列，01 文档核对环境事实**

每条 TD-A 编号标注结果；发现与本计划「已核实的 API 事实」不符的，回改 Task 3/4 代码并以实测为准，同时更新 01 文档（记录发现→验证→结论）。

- [ ] **Step 3: Commit + 合并前自查**

```bash
git add docs/data-testing/
git commit -m "docs(data-testing): 族A 打样执行结果落表——L2 16 例 + L3 6 例全绿"
```

自查清单：`git status` 干净；`git log --oneline feat/data-e2e ^main` 应为 5 个 commit（Task 1–5 各一）；e2e/screenshots/data-full/ 有 ≥3 张截图。

---

## 计划②–⑤ 接续说明（不在本计划内实施）

- **计划② 族B/C**：字段类型（autoSequence 并发不重号、autoTimestamp、compositeText、workflow 状态机）+ 关联（relation/reference/quoteSelect/relation-graph）；复用 `data_full_live.py` 与 helpers。
- **计划③ 族D/E**：分支（diff/merge/lock/switch）+ 依赖；导入导出 ETL（xlsx 造数、dry_run、cancel、重跑幂等、导出内容断言）。
- **计划④ 族F/G/H**：触发/校验/Webhook（测试进程内 stub server）/行动作；列视图+查询台；评论/时间线/备份。
- **计划⑤ 收口**：02 用例全表定稿、首轮全量执行报告（03）、缺陷记录（04，若有）、`docs/user-guide/` 核对。
