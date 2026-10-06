# AI 联动功能正确性测试套件 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为三个 AI 联动功能（会话管理 v2 / SkillOpt 拟合 / 轨迹分析）补齐 16 例功能正确性 e2e（14 确定性 + 2 例 @llm 冒烟），分三批落地。

**Architecture:** 全部落位 `e2e/ai-full/` 三个新 spec，断言走三通道（Playwright UI 真实链路 + API 契约 + `db_exec.py` DB 断言）。种子全部用自造 `AITEST-` 前缀值经 DB 直插，每例 `finally` 自清理。LLM 依赖路径只做 @llm 冒烟（annotation 标记）。

**Tech Stack:** Playwright + TypeScript（`workers=1` 串行）、`batch/toolbox.ts`（dbSeed/secondUser/restartBackend）、`db_exec.py` SQL 桥（stdin、autocommit、输出单行 JSON）、dev 栈（backend :3002 / vite :5173 / OpenCode :4096 / MCP :3003）。

**Spec:** `docs/superpowers/specs/2026-10-06-ai-linked-features-correctness-testing-design.md`（本计划从 spec 论证，执行者两个文件都要读）。

## Global Constraints

- 分支：`feat/ai-linked-correctness-tests`（Task 1 从 main 创建），每个 Task 一个 commit。
- 环境：跑测试前确认 dev 栈存活——3002/5173/4096/3003 四服务（`npm run dev:all` 已含）。后端刚重启首轮用例可能预热抖动，重跑一次即可。
- **禁止并发 pytest**：dev 库共享，pytest 全量不能与 e2e 同时跑。
- 种子纪律：DB 插值只允许自造值（`AITEST-` 前缀 + uuid/tag）；中文常量（如 oplog 断言串）逐字来自源码。
- 清理纪律：每例 `try/finally` 清理——DB 按 title/def_name 前缀 DELETE、临时目录 `fs.rmSync`、`restartBackend()` 无参恢复 env、设置开关恢复原值。
- **断言强度**：禁止多状态接受（`toContain([200,409])`）与条件分支跳过；唯一允许的 skip 是两个 @llm 用例在 502 时 `test.skip()`。
- 前端**无 data-test 属性**，全部用文本/类名定位（与 `execution-audit.spec.ts` 同款）。
- spec/种子文件内的 `__dirname`/`DIRNAME` 解析沿用 `batch/toolbox.ts` 现行手法（照抄其两行），勿自创。
- 单文件运行：`npx playwright test e2e/ai-full/<file>.spec.ts`；`dbSeed` 返回 `any[][]`（每行为数组）。
- Windows：写 DB 的路径一律 `replace(/\\/g, '/')`；fs 用 `path.join`。
- dev 栈登录态 `e2e/.auth/admin.json` 过期时 UI 用例会弹回 /login——处置见 Task 1 Step 0。

---

## Batch 1：会话管理 v2（`e2e/ai-full/ai-session-admin-v2.spec.ts`，5 例）

### Task 1: 共享 DB 种子 helper + spec 骨架 + TC-SESS-01（筛选正确性）

**Files:**
- Create: `e2e/ai-full/db-helpers.ts`（共享：newId / seedPlainSession / cleanupSessionsByPrefix）
- Create: `e2e/ai-full/ai-session-admin-v2.spec.ts`
- 不修改任何产品代码。

**Interfaces:**
- Consumes: `batch/toolbox.ts` 的 `dbSeed(sql: string): any[][]`、`secondUser(prefix?)`、`API`、`authHeaders(token)`；`helpers.ts` 的 `api(request, method, path, data?)` → `{status, json}`、`gotoWithAuth(page, path)`、`tag(prefix)` → `AITEST-<prefix>-<ts>`。
- Produces（后续 Task 复用）:
  - `newId(prefix: string): string` — `prefix + 12位hex`
  - `seedPlainSession(o: { key: string; userId?: string; status?: string; kind?: string | null; batchId?: string | null; scanTaskId?: boolean; kefu?: boolean; messages?: { role: 'user' | 'assistant'; text: string }[]; workspacePath?: string }): Promise<string>` — 返回 sid；标题恒为 `AITEST-SESSV2-<key>`
  - `seedBatch(apiKey: string | null): string` — 返回 bid（batch 表 name=`AITEST-SESSV2-batch-<ts>`；`api_key_id` 非空时 source_type 计算为 api_batch）
  - `cleanupSessionsByPrefix(): void` — `DELETE FROM ai_chat_sessions WHERE title LIKE 'AITEST-SESSV2-%'`
  - `cleanupBatchesByPrefix(): void` — `DELETE FROM ai_chat_batches WHERE name LIKE 'AITEST-SESSV2-%'`（FK 级联其子会话，必须先于 sessions 清理调用）

- [ ] **Step 0: 环境预检与分支**

```bash
cd /e/wsl/check/check-manage
git checkout main && git pull && git checkout -b feat/ai-linked-correctness-tests
# dev 栈四端口探活（任一不通先 npm run dev:all）
curl -s -o /dev/null -w "backend:%{http_code}\n" http://127.0.0.1:3002/auth/login -X POST -H "Content-Type: application/json" -d '{}'
curl -s -o /dev/null -w "vite:%{http_code}\n" http://localhost:5173/
# 登录态新鲜度：若 .auth/admin.json 过期（UI 用例弹回 /login），用真实表单重新登录写回：
# 复用 e2e/execution-audit.spec.ts 的 beforeAll 手法（admin/admin123），或直接跑一次
#   npx playwright test e2e/execution-audit.spec.ts -g "真实会话"   （它会重写 .auth/admin.json）
```

- [ ] **Step 1: 写 `e2e/ai-full/db-helpers.ts`**

```ts
/**
 * AI 联动正确性套件共享 DB 种子助手。
 * 纪律：插值只允许自造值（AITEST 前缀 / uuid / 数字）；调用方负责 finally 清理。
 */
import crypto from 'node:crypto'
import { dbSeed } from './batch/toolbox'

export const SESS_PREFIX = 'AITEST-SESSV2'

export function newId(prefix: string): string {
  return prefix + crypto.randomBytes(6).toString('hex')
}

export interface PlainSessionOpts {
  key: string
  userId?: string
  status?: string
  kind?: string | null
  batchId?: string | null
  scanTaskId?: boolean
  kefu?: boolean
  workspacePath?: string
  messages?: { role: 'user' | 'assistant'; text: string }[]
}

export async function seedPlainSession(o: PlainSessionOpts): Promise<string> {
  const sid = newId('sess_')
  const cols = ['id', 'user_id', 'title', 'status']
  const vals = [`'${sid}'`, `'${o.userId ?? 'user-admin'}'`,
    `'${SESS_PREFIX}-${o.key}'`, `'${o.status ?? 'completed'}'`]
  if (o.kind) { cols.push('kind'); vals.push(`'${o.kind}'`) }
  if (o.batchId) { cols.push('batch_id'); vals.push(`'${o.batchId}'`) }
  if (o.scanTaskId) { cols.push('scan_task_id'); vals.push(`'${newId('scan_')}'`) }
  if (o.kefu) { cols.push('kefu_instance_id'); vals.push(`'${newId('kefu_')}'`) }
  if (o.workspacePath) { cols.push('workspace_path'); vals.push(`'${o.workspacePath.replace(/\\/g, '/')}'`) }
  dbSeed(`INSERT INTO ai_chat_sessions (${cols.join(',')}) VALUES (${vals.join(',')})`)
  for (const m of o.messages ?? []) {
    dbSeed(`INSERT INTO ai_chat_messages (id, session_id, role, content) VALUES ('${newId('msg_')}', '${sid}', '${m.role}', '[{"type":"text","text":"${m.text}"}]')`)
  }
  return sid
}

let batchSeq = 0
export function seedBatch(apiKey: string | null): string {
  batchSeq += 1
  const bid = newId('batch_')
  dbSeed(`INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total, api_key_id) VALUES ('${bid}', 'user-admin', '${SESS_PREFIX}-batch-${batchSeq}', 'e2e seed', 'completed', 1, ${apiKey ? `'${apiKey}'` : 'NULL'})`)
  return bid
}

export function cleanupSessionsByPrefix(): void {
  dbSeed(`DELETE FROM ai_chat_sessions WHERE title LIKE '${SESS_PREFIX}-%'`)
}

export function cleanupBatchesByPrefix(): void {
  dbSeed(`DELETE FROM ai_chat_batches WHERE name LIKE '${SESS_PREFIX}-%'`)
}
```

- [ ] **Step 2: 写 spec 骨架 + TC-SESS-01**

`e2e/ai-full/ai-session-admin-v2.spec.ts`：

```ts
/**
 * AI 会话管理 v2 功能正确性 E2E（spec §2，TC-SESS-01~05）。
 * 零 LLM：DB 种子 + API 契约 + UI 真实链路。判别力登记见
 * docs/ai-testing/evidence/2026-10-06-ai-linked-correctness-判别力登记.md
 */
import { test, expect } from '@playwright/test'
import { api, gotoWithAuth } from './helpers'
import { API, authHeaders, adminToken, secondUser, dbSeed } from './batch/toolbox'
import { newId, seedPlainSession, seedBatch, cleanupSessionsByPrefix, cleanupBatchesByPrefix } from './db-helpers'

test.setTimeout(120_000)

// 逐例清理：批先删（FK 级联其子会话），再兜底删剩余种子会话
async function cleanup() {
  cleanupBatchesByPrefix()
  cleanupSessionsByPrefix()
}

test('TC-SESS-01 v2 筛选：sourceType 五类 × status × kind 默认隐藏 × 400 校验', async ({ request }) => {
  const key = newId('f1-')          // 随机 keyword，收敛到种子集
  const bBatch = seedBatch(null)
  const bApi = seedBatch('ak-e2e-fake')
  const sRegular = await seedPlainSession({ key, status: 'completed' })
  const sBatch = await seedPlainSession({ key, batchId: bBatch })
  const sApi = await seedPlainSession({ key, batchId: bApi })
  const sScan = await seedPlainSession({ key, scanTaskId: true })
  const sKefu = await seedPlainSession({ key, kefu: true })
  const sTrace = await seedPlainSession({ key, kind: 'trace_analysis' })
  try {
    const q = async (params: string) =>
      (await api(request, 'get', `/ai/chat/admin/sessions/v2?pageSize=100&${params}`)).json
    const ids = (r: any) => r.items.map((x: any) => x.id)

    // sourceType 逐类精确命中，且返回项无跨类泄漏
    for (const [t, sid] of [['regular', sRegular], ['batch', sBatch], ['api_batch', sApi],
      ['scan', sScan], ['kefu', sKefu]] as const) {
      const r = await q(`sourceType=${t}&keyword=${key}`)
      expect(ids(r)).toEqual([sid])
    }
    // 判别力：api_batch（批行带 api_key_id）不得被 sourceType=batch 命中（计算列优先级）
    const rb = await q(`sourceType=batch&keyword=${key}`)
    expect(ids(rb)).toEqual([sBatch])
    // kind 默认隐藏 trace_analysis；kind=all 可见；kind=trace_analysis 只看分析会话
    expect(ids(await q(`keyword=${key}`))).not.toContain(sTrace)
    expect(ids(await q(`keyword=${key}`))).toHaveLength(5)
    const all = await q(`keyword=${key}&kind=all`)
    expect(ids(all)).toHaveLength(6)
    expect(ids(all)).toContain(sTrace)
    expect(ids(await q(`keyword=${key}&kind=trace_analysis`))).toEqual([sTrace])
    // status 过滤
    const st = await q(`keyword=${key}&status=completed&kind=all`)
    expect(ids(st)).toHaveLength(6)   // 种子会话全部 completed
    // 分页 total 与 items 一致
    expect(all.total).toBe(6)
    // 非法参数 400（中文错误契约）
    const badStatus = await api(request, 'get', '/ai/chat/admin/sessions/v2?status=bogus')
    expect(badStatus.status).toBe(400)
    expect(badStatus.json.error).toContain('无效状态')
    const badSrc = await api(request, 'get', '/ai/chat/admin/sessions/v2?sourceType=bogus')
    expect(badSrc.status).toBe(400)
    expect(badSrc.json.error).toContain('无效来源类型')
  } finally { await cleanup() }
})
```

- [ ] **Step 3: 运行验证**

```bash
npx playwright test e2e/ai-full/ai-session-admin-v2.spec.ts
```
Expected: 1 passed。若 `toEqual([sid])` 失败且返回项含 dev 库既有会话——检查 keyword 是否漏拼；若 401 弹登录——.auth 过期，回 Step 0。

- [ ] **Step 4: 全绿基线自查（判别力前置）**——确认该用例断言全部为精确值（`toEqual`/`toBe`），无多状态接受。确认后提交。

```bash
git add e2e/ai-full/db-helpers.ts e2e/ai-full/ai-session-admin-v2.spec.ts
git commit -m "test(e2e): 会话管理v2 TC-SESS-01 筛选正确性（sourceType计算列/kind隐藏/400契约）+ 共享DB种子helper"
```

### Task 2: TC-SESS-02 关键词全消息搜索

**Files:**
- Modify: `e2e/ai-full/ai-session-admin-v2.spec.ts`（追加用例）

**Interfaces:** Consumes Task 1 全部。

- [ ] **Step 1: 追加用例**

```ts
test('TC-SESS-02 关键词搜索：命中消息正文、精确排除、无匹配返空', async ({ request }) => {
  const key = newId('kw-')
  const needle = `${key}-needle`    // 只出现在消息正文，不出现在标题
  const sHit = await seedPlainSession({ key, messages: [{ role: 'user', text: needle }] })
  await seedPlainSession({ key })   // 标题同前缀但正文无 needle
  try {
    const r = (await api(request, 'get', `/ai/chat/admin/sessions/v2?keyword=${encodeURIComponent(needle)}&kind=all`)).json
    expect(r.items.map((x: any) => x.id)).toEqual([sHit])
    expect(r.total).toBe(1)
    const none = (await api(request, 'get', `/ai/chat/admin/sessions/v2?keyword=${key}-nope&kind=all`)).json
    expect(none.items).toEqual([])
  } finally { await cleanup() }
})
```

- [ ] **Step 2: 运行** — `npx playwright test e2e/ai-full/ai-session-admin-v2.spec.ts`，Expected: 2 passed（搜索走 `p->>'text' ILIKE` 的 EXISTS 路径；若命中 0 条，先查 `search_text` 触发器是否维护了 content jsonb——种子经直插同样触发该触发器）。

- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 会话管理v2 TC-SESS-02 关键词全消息搜索"`

### Task 3: TC-SESS-03 详情抽屉三 tab（UI + 文件内容一致性）

**Files:**
- Modify: `e2e/ai-full/ai-session-admin-v2.spec.ts`

**Interfaces:** Consumes Task 1；文件头补 `import fs from 'node:fs'`、`import os from 'node:os'`、`import path from 'node:path'`（部分已在文件头）。

**与 spec 的偏差（显式声明）**：spec TC-SESS-03 的「子代理下钻」断言不在本用例实现——该交互已由 `e2e/ai-full/batch/admin.spec.ts` 既有覆盖（BatchConversationView 子任务消息），不重复建设。

- [ ] **Step 1: 追加用例**

```ts
test('TC-SESS-03 详情抽屉：基本信息/对话历史/文件列表 + 下载内容一致', async ({ page, request }) => {
  const key = newId('detail-')
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'aitest-ws-'))
  fs.mkdirSync(path.join(ws, 'outputs'))
  fs.writeFileSync(path.join(ws, 'outputs', 'report.md'), 'AITEST-REPORT-CONTENT-42')
  const sid = await seedPlainSession({
    key,
    messages: [{ role: 'user', text: 'hello drawer' }, { role: 'assistant', text: 'hi drawer' }],
    workspacePath: ws,
  })
  try {
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '详情' }).first().click()
    const drawer = page.locator('.el-drawer', { hasText: '会话详情' })
    await drawer.waitFor({ state: 'visible' })
    // 基本信息：会话 ID / 标题 精确渲染
    await expect(drawer.locator('.session-admin__sid', { hasText: sid })).toBeVisible()
    await expect(drawer.getByText(`AITEST-SESSV2-${key}`)).toBeVisible()
    // 对话历史：种子消息渲染
    await drawer.locator('.el-tabs__item', { hasText: '对话历史' }).click()
    await expect(drawer.getByText('hello drawer')).toBeVisible({ timeout: 15_000 })
    // 文件 tab：outputs 分组与文件名
    await drawer.locator('.el-tabs__item', { hasText: '文件' }).click()
    await expect(drawer.getByText('report.md')).toBeVisible({ timeout: 15_000 })
    // 下载内容与种子一致（API 通道；UI 下载走 window.open 不做 UI 捕获）
    const tk = await adminToken()
    const dl = await fetch(`${API}/ai/chat/admin/sessions/v2/${sid}/files/download?path=${encodeURIComponent('outputs/report.md')}`, { headers: authHeaders(tk) })
    expect(dl.status).toBe(200)
    expect(await dl.text()).toBe('AITEST-REPORT-CONTENT-42')
  } finally {
    await cleanup()
    fs.rmSync(ws, { recursive: true, force: true })
  }
})
```

- [ ] **Step 2: 运行** — Expected: 3 passed。文件 tab 断言依赖 `list_session_files` 对临时目录的扫描（outputs/ 递归、排除 dotfiles）；抽屉标题默认 `会话详情`。

- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 会话管理v2 TC-SESS-03 详情抽屉三tab+文件下载内容一致"`

### Task 4: TC-SESS-04 v2 行内归档（UI）+ 批控 409（API）+ oplog

**Files:**
- Modify: `e2e/ai-full/ai-session-admin-v2.spec.ts`
- Consumes: `batch/toolbox.ts` 的 `seedRunningChild(): Promise<{bid, sid}>`（种子 running 批子会话；**清理必须 `DELETE FROM ai_chat_batches WHERE id=bid`，不能走 API**）

- [ ] **Step 1: 追加用例**

```ts
import { seedRunningChild } from './batch/toolbox'   // 并入文件头 import

test('TC-SESS-04 v2 归档：UI 归档 active 会话 + oplog 留痕；批控会话 409', async ({ page, request }) => {
  const key = newId('arch-')
  const sid = await seedPlainSession({ key, status: 'active' })
  const running = await seedRunningChild({})
  try {
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '归档' }).first().click()
    await page.locator('.el-message-box').getByText('确定', { exact: true }).click()
    await expect(page.locator('.el-message', { hasText: '已归档' })).toBeVisible()
    // 列表刷新后状态徽标
    await page.locator('button', { hasText: '查询' }).click()
    await expect(page.locator('.el-table__row', { hasText: `AITEST-SESSV2-${key}` }).first())
      .toContainText('已归档')
    // oplog 留痕（描述逐字来自 ai_chat.py archive 端点）
    const logs = dbSeed(`SELECT action, description FROM operation_logs WHERE target_type='ai_chat_session' AND target_id='${sid}' ORDER BY created_at DESC LIMIT 1`)
    expect(logs).toHaveLength(1)
    expect(logs[0]).toEqual(['update', '归档会话（admin）'])
    // 批控 running 子会话：归档 409 BATCH_SESSION_CONTROLLED（API 通道；UI 对 running 行不渲染归档项）
    const tk = await adminToken()
    const res = await fetch(`${API}/ai/chat/sessions/${running.sid}/archive`, { method: 'POST', headers: authHeaders(tk) })
    expect(res.status).toBe(409)
    expect((await res.json()).error.code).toBe('BATCH_SESSION_CONTROLLED')
  } finally {
    dbSeed(`DELETE FROM ai_chat_batches WHERE id='${running.bid}'`)
    await cleanup()
  }
})
```

- [ ] **Step 2: 运行** — Expected: 4 passed。归档确认框文本 `确定归档此会话？`（确认按钮默认 `确定`）。

- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 会话管理v2 TC-SESS-04 UI归档+oplog+批控409"`

### Task 5: TC-SESS-05 权限边界

**Files:**
- Modify: `e2e/ai-full/ai-session-admin-v2.spec.ts`

- [ ] **Step 1: 追加用例**

```ts
test('TC-SESS-05 权限边界：guest 对 v2 系列与归档全 403', async ({ request }) => {
  const u = await secondUser('sessv2')
  const key = newId('perm-')
  const sid = await seedPlainSession({ key })
  try {
    const g = async (p: string, method = 'GET') => fetch(`${API}${p}`, {
      method, headers: { ...authHeaders(u.token), 'Content-Type': 'application/json' },
    })
    expect((await g('/ai/chat/admin/sessions/v2')).status).toBe(403)
    expect((await g(`/ai/chat/admin/sessions/v2/${sid}`)).status).toBe(403)
    expect((await g(`/ai/chat/admin/sessions/v2/${sid}/messages`)).status).toBe(403)
    expect((await g(`/ai/chat/admin/sessions/v2/${sid}/files`)).status).toBe(403)
    expect((await g(`/ai/chat/sessions/${sid}/archive`, 'POST')).status).toBe(403)
    // 不存在的会话同样先过权限关（require_permission 先于存在性检查）→ 403 而非 404
    expect((await g(`/ai/chat/admin/sessions/v2/${newId('sess_')}`)).status).toBe(403)
  } finally {
    await u.cleanup()
    await cleanup()
  }
})
```

- [ ] **Step 2: 运行** — Expected: 5 passed。若任一返回 404 而非 403，说明装饰器顺序与预期不符——这是真实发现，登记判别力报告后再定断言。

- [ ] **Step 3: Batch 1 收尾——跑既有相关回归防串扰**：`npx playwright test e2e/ai-governance-audit.spec.ts e2e/execution-audit.spec.ts e2e/ai-full/ai-session-core.spec.ts`，Expected: 全绿。

- [ ] **Step 4: Commit** — `git commit -am "test(e2e): 会话管理v2 TC-SESS-05 权限边界（guest 全 403）；Batch 1 完成"`

---

## Batch 2：SkillOpt 拟合（`e2e/ai-full/ai-skillopt-fit.spec.ts`，6 例）

### Task 6: seedFitAttempt 种子助手 + TC-FIT-01（拟合三态）

**Files:**
- Create: `e2e/ai-full/ai-skillopt-fit.spec.ts`
- Create: `e2e/ai-full/fit-seed.ts`（种子助手，本 spec 专用）

**Interfaces:**
- Consumes: `db-helpers.ts` 的 `newId`；`toolbox.ts` 的 `dbSeed`；`helpers.ts` 的 `api`、`gotoWithAuth`。

**与 spec 的偏差（显式声明）**：spec TC-FIT-01 的「子代理归属列」UI 断言不在本用例实现——子代理拟合的种子构造依赖 compute 的 subagent 路径细节，L1（`test_skill_fit.py`）已覆盖该语义，e2e 侧不为它引入脆弱种子。
- Produces（Task 7/8/11 复用）:
  - `resolveWorkspaceRoot(): string` — 经 `python -c` 读 `config.AI_WORKSPACE_ROOT`（与 db_exec.py 同款 sys.path 手法）
  - `seedFitAttempt(trace: string[]): Promise<FitSeed>` — `FitSeed = { attemptId, sessionId, defName, path, dir }`；种 session + attempt（started NOW()-1h / finished NOW()）+ manifest（kind='skill', content_hash=文件 sha256）+ `agent_tool_calls` 行（state='completed', occurred_at NOW()-30min，落在窗口内）；定义文件写在 `AI_WORKSPACE_ROOT/AITEST-fit-<ts>/SKILL.md`（`_path_in_allowed_roots` 白名单内，frontmatter `fit.steps` 为 read_input→read / save_result→write 两步）
  - `cleanupFitSeeds(): void` — 按 `def_name LIKE 'AITEST-fit-%'` / `title LIKE 'AITEST-SKILLOPT-%'` 级联清理 + 删临时目录

**关键事实**（来自 skill_fit.py 源码，种子正确性依据）：拟合轨迹源是 `agent_tool_calls`（`root_session_id` + `state='completed'` + `occurred_at BETWEEN started_at AND finished_at`）；状态判定 `hits==total→fit / hits==0→diverged / 否则 partial`；score=hits/total×100；唯一索引 `uq_skill_fit_attempt_def(attempt_id, def_name)`；recompute 无请求体，attempt 不存在 404。

- [ ] **Step 1: 写 `e2e/ai-full/fit-seed.ts`**

```ts
/**
 * SkillOpt 拟合链路种子：定义文件 + attempt + manifest + agent_tool_calls 轨迹。
 * 轨迹事实源是 agent_tool_calls（skill_fit._load_trace），窗口 = attempt started..finished。
 */
import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { dbSeed } from './batch/toolbox'
import { newId } from './db-helpers'

export const FIT_TITLE_PREFIX = 'AITEST-SKILLOPT-'
const madeDirs: string[] = []

export function resolveWorkspaceRoot(): string {
  const serverDir = path.resolve(__dirname, '..', '..', 'server')
  const out = execFileSync('python', ['-c',
    `import sys; sys.path.insert(0, r'${serverDir}'); from config import AI_WORKSPACE_ROOT; print(AI_WORKSPACE_ROOT)`],
    { encoding: 'utf-8' })
  return out.trim()
}

export const TWO_STEPS = [
  { id: 'read_input', expect: [{ tool: 'read' }] },
  { id: 'save_result', expect: [{ tool: 'write' }] },
]

function writeSkillFile(root: string): { path: string; dir: string } {
  const dir = fs.mkdtempSync(path.join(root, 'AITEST-fit-'))
  madeDirs.push(dir)
  const p = path.join(dir, 'SKILL.md')
  const steps = TWO_STEPS.map(s =>
    `    - id: ${s.id}\n      expect:\n${s.expect.map(e => `        - tool: ${e.tool}\n`).join('')}`).join('')
  fs.writeFileSync(p, `---\nname: AITEST Fit Skill\nfit:\n  steps:\n${steps}---\n\n# AITEST fit skill body\n`)
  return { path: p, dir }
}

export interface FitSeed { attemptId: string; sessionId: string; defName: string; path: string; dir: string }

export async function seedFitAttempt(trace: string[]): Promise<FitSeed> {
  const root = resolveWorkspaceRoot()
  const { path: defPath, dir } = writeSkillFile(root)
  const sessionId = newId('sess_')
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status) VALUES ('${sessionId}', 'user-admin', '${FIT_TITLE_PREFIX}${sessionId}', 'completed')`)
  const attemptId = newId('att_')
  dbSeed(`INSERT INTO ai_execution_attempts (id, session_id, source_type, attempt_no, operation, status, started_at, finished_at)
          VALUES ('${attemptId}', '${sessionId}', 'interactive', 1, 'send', 'completed', NOW() - INTERVAL '1 hour', NOW())`)
  const content = fs.readFileSync(defPath)
  const hash = crypto.createHash('sha256').update(content).digest('hex')
  const defName = `AITEST-fit-${Date.now()}-${crypto.randomBytes(2).toString('hex')}`
  dbSeed(`INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source, path, content_hash)
          VALUES ('${newId('man_')}', '${attemptId}', 'skill', '${defName}', 'session', '${defPath.replace(/\\/g, '/')}', '${hash}')`)
  const ocSid = 'ses_' + crypto.randomBytes(6).toString('hex')
  for (const [i, tool] of trace.entries()) {
    dbSeed(`INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id, tool, args_text, state, occurred_at)
            VALUES ('${ocSid}', '${sessionId}', 'p${i}', '${tool}', '{"path":"a.csv"}', 'completed', NOW() - INTERVAL '30 minutes')`)
  }
  return { attemptId, sessionId, defName, path: defPath, dir }
}

export function cleanupFitSeeds(): void {
  dbSeed(`DELETE FROM ai_skill_fit_results WHERE def_name LIKE 'AITEST-fit-%'`)
  dbSeed(`DELETE FROM ai_skill_def_versions WHERE def_name LIKE 'AITEST-fit-%'`)
  dbSeed(`DELETE FROM agent_tool_calls WHERE root_session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_skill_invocations WHERE session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_events WHERE attempt_id IN (SELECT a.id FROM ai_execution_attempts a JOIN ai_chat_sessions s ON s.id=a.session_id WHERE s.title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_manifests WHERE attempt_id IN (SELECT a.id FROM ai_execution_attempts a JOIN ai_chat_sessions s ON s.id=a.session_id WHERE s.title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_execution_attempts WHERE session_id IN (SELECT id FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%')`)
  dbSeed(`DELETE FROM ai_chat_sessions WHERE title LIKE '${FIT_TITLE_PREFIX}%'`)
  for (const d of madeDirs.splice(0)) fs.rmSync(d, { recursive: true, force: true })
}
```

- [ ] **Step 2: 写 spec + TC-FIT-01**

```ts
/**
 * SkillOpt 拟合链路功能正确性 E2E（spec §3，TC-FIT-01~06）。
 * apply/preview 纯机械（steps 由请求体传入，不经 LLM）→ 确定性覆盖；
 * generate/diagnose 打 LLM → @llm 冒烟（TC-FIT-06）。
 */
import { test, expect } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { api, gotoWithAuth } from './helpers'
import { API, dbSeed } from './batch/toolbox'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'
import { seedFitAttempt, cleanupFitSeeds, TWO_STEPS, resolveWorkspaceRoot } from './fit-seed'

test.setTimeout(180_000)

test('TC-FIT-01 拟合三态：recompute 落库 + 幂等不重复 + 明细 perStep + 汇总聚合', async ({ page, request }) => {
  const aFit = await seedFitAttempt(['read', 'write'])       // 2/2 → fit, 100
  const aPart = await seedFitAttempt(['read'])               // 1/2 → partial, 50
  const aDiv = await seedFitAttempt(['glob'])                // 0/2 → diverged, 0
  try {
    for (const [a, status] of [[aFit, 'fit'], [aPart, 'partial'], [aDiv, 'diverged']] as const) {
      const r = await api(request, 'post', `/ai/chat/admin/skill-fit/${a.attemptId}/recompute`)
      expect(r.status).toBe(200)
      expect(r.json.fits[0].status).toBe(status)
    }
    const list = (await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${aFit.defName}&limit=100`)).json
    expect(list.fits).toHaveLength(3)
    expect(list.fits.find((f: any) => f.attemptId === aFit.attemptId).score).toBe(100)
    expect(list.fits.find((f: any) => f.attemptId === aPart.attemptId).score).toBe(50)
    expect(list.fits.find((f: any) => f.attemptId === aDiv.attemptId).score).toBe(0)
    // 幂等：重复 recompute 不产生重复行（uq_skill_fit_attempt_def upsert）
    await api(request, 'post', `/ai/chat/admin/skill-fit/${aFit.attemptId}/recompute`)
    expect(((await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${aFit.defName}&limit=100`)).json.fits)).toHaveLength(3)
    // 明细：partial 的 perStep 状态恰为 hit, miss
    const detail = (await api(request, 'get', `/ai/chat/admin/skill-fit/${aPart.attemptId}`)).json
    expect(detail.fits[0].perStep.map((s: any) => s.status)).toEqual(['hit', 'miss'])
    // definition-summary 聚合 + 拟合时自动登记 def 版本
    const sum = (await api(request, 'get', '/ai/chat/admin/skill-fit/definition-summary')).json
    const d = sum.definitions.find((x: any) => x.defName === aFit.defName)
    expect(d.tasks).toBe(3)
    expect(d.versions).toBeGreaterThanOrEqual(1)
    // 404：attempt 不存在
    expect((await api(request, 'post', `/ai/chat/admin/skill-fit/${newId('att_')}/recompute`)).status).toBe(404)
    // UI：主从布局出现定义行 + 结果行 + 重新计算 toast
    await gotoWithAuth(page, '/admin/ai-skills?tab=fit')
    const defCard = page.locator('.fit-def', { hasText: aFit.defName }).first()
    await defCard.waitFor({ state: 'visible', timeout: 30_000 })
    await defCard.click()
    await expect(page.locator('.el-table__row').first()).toBeVisible({ timeout: 15_000 })
    await page.locator('button', { hasText: '重新计算' }).first().click()
    await expect(page.locator('.el-message', { hasText: '已重新计算' })).toBeVisible()
  } finally { cleanupFitSeeds() }
})
```

- [ ] **Step 3: 运行** — `npx playwright test e2e/ai-full/ai-skillopt-fit.spec.ts`，Expected: 1 passed。UI 段注意：「重新计算」按钮在 `FitResultsPanel.vue:21-28` 的行内 `.fit-detail__ops` 中，若该区在行展开态才渲染，先点行展开再点按钮（以组件实际结构为准）。若 `resolveWorkspaceRoot` 打印异常（config import 副作用），改读 `dbSeed("SELECT workspace_path FROM ai_chat_sessions WHERE workspace_path IS NOT NULL ORDER BY created_at DESC LIMIT 1")` 取样本路径去掉末段推断根——实现定夺，但必须留注释说明取值来源。

- [ ] **Step 4: Commit** — `git add e2e/ai-full/fit-seed.ts e2e/ai-full/ai-skillopt-fit.spec.ts && git commit -m "test(e2e): SkillOpt TC-FIT-01 拟合三态+幂等+聚合+UI 主从布局（agent_tool_calls 轨迹种子）"`

### Task 7: TC-FIT-02 试算 preview（确定性）

**Files:** Modify `ai-skillopt-fit.spec.ts`

- [ ] **Step 1: 追加用例**

```ts
test('TC-FIT-02 preview：贪心匹配契约 + 400 形状族 + 404 + 不落库', async ({ request }) => {
  const a = await seedFitAttempt(['read', 'write'])
  try {
    const steps = [
      { id: 'read_input', expect: [{ tool: 'read' }] },
      { id: 'save_result', expect: [{ tool: 'write', args_pattern: '"path"' }] },
    ]
    const r = await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps })
    expect(r.status).toBe(200)
    expect(r.json.preview).toMatchObject({ steps_total: 2, steps_hit: 2, score: 100, status: 'fit' })
    // 不落库：行数不变
    const list = (await api(request, 'get', `/ai/chat/admin/skill-fit?defName=${a.defName}&limit=100`)).json
    expect(list.fits).toHaveLength(0)
    // 400 族：缺 id / expect 非数组 / 非法正则 / 缺 attemptId
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ expect: [{ tool: 'read' }] }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ id: 'x', expect: 'read' }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: a.attemptId, steps: [{ id: 'x', expect: [{ tool: 'read', args_pattern: '(unclosed' }] }] })).status).toBe(400)
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { steps })).status).toBe(400)
    // 404：attempt 不存在
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/preview', { attemptId: newId('att_'), steps })).status).toBe(404)
  } finally { cleanupFitSeeds() }
})
```

- [ ] **Step 2: 运行** — Expected: 2 passed。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): SkillOpt TC-FIT-02 试算 preview 契约（纯确定性不落库）"`

### Task 8: TC-FIT-03 回写→自动归档→回滚（核心判别力）

**Files:** Modify `ai-skillopt-fit.spec.ts`；头部补 `import path from 'node:path'`。

- [ ] **Step 1: 追加用例**

```ts
test('TC-FIT-03 回写→自动归档→回滚：sha256 自洽、虚假版本零产生、越界/非法正则拒绝', async ({ request }) => {
  const a = await seedFitAttempt(['read', 'write'])
  const versionsOf = async () => (await api(request, 'get',
    `/ai/chat/admin/skill-def-versions?defKind=skill&defName=${a.defName}`)).json.versions
  try {
    // 基线：recompute 登记版本（content_hash = 种子文件 sha256）
    await api(request, 'post', `/ai/chat/admin/skill-fit/${a.attemptId}/recompute`)
    const orig = fs.readFileSync(a.path)
    expect(await versionsOf()).toHaveLength(1)

    const newSteps = [...TWO_STEPS, { id: 'apply_marker_step', expect: [{ tool: 'glob' }] }]
    const r = await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply', { path: a.path, steps: newSteps })
    expect(r.status).toBe(200)
    const after = fs.readFileSync(a.path)
    expect(after.equals(orig)).toBe(false)
    expect(after.toString()).toContain('apply_marker_step')   // steps 写进 frontmatter fit.steps
    const newHash = crypto.createHash('sha256').update(after).digest('hex')
    const vs = await versionsOf()
    expect(vs).toHaveLength(2)                                 // 基线 + apply 新版本
    const applied = vs.find((v: any) => v.contentHash === newHash)
    expect(applied).toBeTruthy()
    expect(applied.archived).toBe(true)                        // bytes 回读归档正文
    expect(applied.versionLabel).toBe(`AI步骤优化 ${new Date().toLocaleDateString('sv-SE')}`)
    // 非法正则 400 且不写盘（先校验后写盘）
    const bad = await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply',
      { path: a.path, steps: [{ id: 'x', expect: [{ tool: 'read', args_pattern: '(unclosed' }] }] })
    expect(bad.status).toBe(400)
    expect(fs.readFileSync(a.path).equals(after)).toBe(true)
    // 路径越界 400（_path_in_allowed_roots）
    expect((await api(request, 'post', '/ai/chat/admin/skill-def-steps/apply',
      { path: 'C:/Windows/system32/evil.md', steps: newSteps })).status).toBe(400)
    // 回滚：文件恢复原文 + 不产生虚假新版本行
    const rb = await api(request, 'post', `/ai/chat/admin/skill-def-versions/${applied.id}/rollback`)
    expect(rb.status).toBe(200)
    expect(fs.readFileSync(a.path).equals(orig)).toBe(true)
    expect(await versionsOf()).toHaveLength(2)
  } finally { cleanupFitSeeds() }
})
```

- [ ] **Step 2: 运行** — Expected: 3 passed。versionLabel 日期用服务器本地时区（`toLocaleDateString('sv-SE')` = YYYY-MM-DD，与后端 `f'%Y-%m-%d'` 同机同区）。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): SkillOpt TC-FIT-03 回写自动归档+回滚零虚假版本（核心判别力）"`

### Task 9: TC-FIT-04 调用采集链路（internal runtime-events）

**Files:** Modify `ai-skillopt-fit.spec.ts`

**关键事实**：`POST /ai/memory/internal/runtime-events`，鉴权头 `X-Internal-Token` 全等 `config.MCP_INTERNAL_TOKEN`（env 默认空→一律 403）；dev 栈 token 在 `server/.env` 第 6 行（测试直接读文件，为空即环境错误直接红）。`kind='skill'` 体 `{skillName, sessionID, messageID, partID, status, title}`；`kind='session.idle'` 体 `{sessionID}` 会把该会话 invocations 的 outcome 收敛为 completed。落库 UNIQUE(attempt_id, skill_name, skill_hash) 幂等。

- [ ] **Step 1: 追加用例**

```ts
function internalToken(): string {
  const envPath = path.resolve(__dirname, '..', '..', 'server', '.env')
  const m = fs.readFileSync(envPath, 'utf-8').match(/^MCP_INTERNAL_TOKEN=(.*)$/m)
  const tok = m ? m[1].trim() : ''
  expect(tok, 'server/.env 必须配置 MCP_INTERNAL_TOKEN（dev 栈前置）').not.toBe('')
  return tok
}

test('TC-FIT-04 采集链路：runtime 事件落库 + 幂等不降级 + idle 收敛 + 聚合 + 403', async ({ request }) => {
  const a = await seedFitAttempt(['read'])
  const tok = internalToken()
  const evt = { kind: 'skill', skillName: `AITEST-skill-${Date.now()}`, sessionID: a.sessionId,
    messageID: 'm1', partID: 'p1', status: 'completed', title: 'e2e' }
  const post = (body: unknown, token = tok) => fetch(`${API}/ai/memory/internal/runtime-events`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Internal-Token': token },
    body: JSON.stringify(body),
  })
  try {
    expect((await post(evt)).status).toBe(200)
    expect((await post(evt)).status).toBe(200)          // 重复上报
    const rows = dbSeed(`SELECT source, evidence_level FROM ai_skill_invocations WHERE session_id='${a.sessionId}' AND skill_name='${evt.skillName}'`)
    expect(rows).toHaveLength(1)                         // 幂等：仍 1 行
    expect(rows[0][0]).toBe('runtime')                   // source 不被降级
    expect((await post({ kind: 'session.idle', sessionID: a.sessionId })).status).toBe(200)
    expect(dbSeed(`SELECT outcome FROM ai_skill_invocations WHERE session_id='${a.sessionId}' AND skill_name='${evt.skillName}'`)[0][0]).toBe('completed')
    const an = (await api(request, 'get', '/ai/chat/admin/skill-analytics')).json
    const s = an.skills.find((x: any) => x.name === evt.skillName)
    expect(s.runtime_confirmed).toBe(1)
    expect((await post(evt, 'wrong-token')).status).toBe(403)
  } finally { cleanupFitSeeds() }
})
```

- [ ] **Step 2: 运行** — Expected: 4 passed。若 runtime-events 对缺 attempt 的处理与预期不符（500），这是真实发现——种子已含 attempt，不应触发。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): SkillOpt TC-FIT-04 runtime 采集链路（幂等/不降级/idle 收敛/403）"`

### Task 10: TC-FIT-05 反馈→效果追踪

**Files:** Modify `ai-skillopt-fit.spec.ts`

**Step 0（先读再写）**：读 `server/routes/ai_session_admin.py:703-775`，确认 feedback applied 时 `before_metrics` 快照的存储字段与 effect 端点读取的键名（当前判断：`appliedValue` 传 `{skillName}`，effect 按 before_metrics.skillName 过滤 invocations）。若实际键名不同，以下用例中的 `appliedValue` 形状按源码修正——修正必须留注释引用行号。

- [ ] **Step 1: 追加用例**

```ts
test('TC-FIT-05 反馈→效果追踪：applied 落库 + before 窗口含种子调用 + 400/404', async ({ request }) => {
  const key = newId('fb-')
  const sid = await seedPlainSession({ key })
  const attemptId = newId('att_')
  dbSeed(`INSERT INTO ai_execution_attempts (id, session_id, source_type, attempt_no, operation, status, started_at, finished_at)
          VALUES ('${attemptId}', '${sid}', 'interactive', 1, 'send', 'completed', NOW() - INTERVAL '2 hour', NOW() - INTERVAL '1 hour')`)
  const skillName = `AITEST-skill-${Date.now()}`
  dbSeed(`INSERT INTO ai_skill_invocations (session_id, attempt_id, skill_name, skill_hash, source, evidence_level, invoked_at, outcome)
          VALUES ('${sid}', '${attemptId}', '${skillName}', 'h1', 'runtime', 'confirmed', NOW() - INTERVAL '90 minutes', 'completed')`)
  const diagId = newId('ana_')
  dbSeed(`INSERT INTO ai_execution_diagnoses (id, target_session_id, status) VALUES ('${diagId}', '${sid}', 'completed')`)
  const sugId = 'sug_' + crypto.randomBytes(4).toString('hex')
  try {
    const fb = await api(request, 'post', `/ai/chat/admin/analyses/${diagId}/suggestions/${sugId}/feedback`,
      { action: 'applied', appliedValue: { skillName } })
    expect(fb.status).toBe(200)
    const row = (await api(request, 'get', '/ai/chat/admin/suggestion-feedbacks')).json.feedbacks
      .find((f: any) => f.diagnosis_id === diagId)
    expect(row).toBeTruthy()
    expect(row.action).toBe('applied')
    // effect：tracked + before 7 天窗口包含 90 分钟前的种子调用
    const eff = (await api(request, 'get', `/ai/chat/admin/skill-suggestions/${sugId}/effect`)).json
    expect(eff.status).toBe('tracked')
    expect(eff.before.invocations).toBeGreaterThanOrEqual(1)
    expect(eff.after).toBeTruthy()
    // 非法 action 400 / 诊断不存在 404
    expect((await api(request, 'post', `/ai/chat/admin/analyses/${diagId}/suggestions/${sugId}/feedback`, { action: 'bogus' })).status).toBe(400)
    expect((await api(request, 'post', `/ai/chat/admin/analyses/${newId('ana_')}/suggestions/${sugId}/feedback`, { action: 'applied' })).status).toBe(404)
  } finally {
    dbSeed(`DELETE FROM ai_suggestion_feedback WHERE diagnosis_id='${diagId}'`)
    dbSeed(`DELETE FROM ai_skill_invocations WHERE session_id='${sid}'`)
    dbSeed(`DELETE FROM ai_execution_diagnoses WHERE id='${diagId}'`)
    dbSeed(`DELETE FROM ai_execution_attempts WHERE id='${attemptId}'`)
    cleanupSessionsByPrefix()
  }
})
```

- [ ] **Step 2: 运行** — Expected: 5 passed。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): SkillOpt TC-FIT-05 建议反馈与效果追踪 7 天窗口"`

### Task 11: TC-FIT-06 @llm 生成+诊断冒烟

**Files:** Modify `ai-skillopt-fit.spec.ts`

- [ ] **Step 1: 追加用例**

```ts
test('TC-FIT-06 @llm 步骤生成+偏差诊断冒烟', async ({ request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const a = await seedFitAttempt(['glob'])        // diverged → 可诊断
  try {
    const gen = await api(request, 'post', '/ai/chat/admin/skill-def-steps/generate',
      { kind: 'skill', path: a.path })
    if (gen.status === 502) return test.skip(true, 'LLM 不可用（generate 502），冒烟跳过')
    expect(gen.status).toBe(200)
    expect(Array.isArray(gen.json.steps)).toBe(true)
    expect(gen.json.steps.length).toBeGreaterThan(0)
    expect(gen.json.steps[0].id).toBeTruthy()
    await api(request, 'post', `/ai/chat/admin/skill-fit/${a.attemptId}/recompute`)
    const fitId = (await api(request, 'get', `/ai/chat/admin/skill-fit/${a.attemptId}`)).json.fits[0].id
    const dg = await api(request, 'post', `/ai/chat/admin/skill-fit/${fitId}/diagnose`)
    if (dg.status === 502) return test.skip(true, 'LLM 不可用（diagnose 502），冒烟跳过')
    expect(dg.status).toBe(200)
    expect(['definition_stale', 'step_redundant', 'order_deviation', 'model_noncompliance', 'environment'])
      .toContain(dg.json.diagnosis.cause)
    expect(Array.isArray(dg.json.diagnosis.suggestions)).toBe(true)
    expect(Array.isArray(dg.json.diagnosis.revised_steps)).toBe(true)
    // oplog 留痕两条（generate target_id=定义路径；diagnose target_id=结果行 id）
    expect(dbSeed(`SELECT count(*) FROM operation_logs WHERE target_type='ai_skill_def_steps' AND target_id='${a.path.replace(/\\/g, '/')}'`)[0][0]).toBeGreaterThanOrEqual(1)
    expect(dbSeed(`SELECT count(*) FROM operation_logs WHERE target_type='ai_skill_fit_diagnosis' AND target_id='${fitId}'`)[0][0]).toBe(1)
  } finally { cleanupFitSeeds() }
})
```

- [ ] **Step 2: 运行** — Expected: passed 或 skip（LLM 不可用时），其余 5 例 passed。skip 需在判别力登记表留证（截图/日志）。
- [ ] **Step 3: Batch 2 收尾回归**：`npx playwright test e2e/ai-full/ai-skillopt-page.spec.ts e2e/ai-full/ai-skillopt-version-archive.spec.ts`，Expected: 全绿。
- [ ] **Step 4: Commit** — `git commit -am "test(e2e): SkillOpt TC-FIT-06 @llm 生成+诊断冒烟；Batch 2 完成"`

---

## Batch 3：轨迹分析（`e2e/ai-full/ai-trace-analysis.spec.ts`，5 例）

### Task 12: restartBackend 共享化 + spec 骨架 + TC-TRACE-04（历史/报告契约）

**Files:**
- Modify: `e2e/ai-full/helpers.ts`（restartBackend 实现迁入，需 `child_process` import）
- Modify: `e2e/ai-full/batch/toolbox.ts`（原实现替换为 `export { restartBackend } from '../helpers'`——batch 既有用例 import 路径不变）
- Create: `e2e/ai-full/ai-trace-analysis.spec.ts`

**Interfaces:**
- Produces: `helpers.ts` 导出 `restartBackend(env: Record<string, string> = {}): Promise<void>`——行为逐字保留 toolbox 版：netstat 找 3002 PID → taskkill → `spawn('python', ['app.py'], { cwd: server, env: {...process.env, ...env}, detached })` → 60s 轮询 `POST /auth/login` 探活（`status < 500` 即起）。无参调用=恢复默认 env。
- 消费方：Task 13/14/15 通过 `import { restartBackend } from './helpers'`。

- [ ] **Step 1: 迁移 restartBackend**——把 `batch/toolbox.ts:174-215` 的函数体原样搬入 `helpers.ts`（补 `import { execFileSync, spawn } from 'node:child_process'`；server 目录路径按 helpers.ts 现有 `__dirname` 风格解析），toolbox.ts 原位置替换为 re-export。运行 `npx playwright test e2e/ai-full/batch/resilience.spec.ts -g "卡死"` 验证 batch 侧不受影响（Expected: passed；该例含真实 LLM 与重启，约 9 分钟——若时间紧可先 `-g "进程级"`）。

- [ ] **Step 2: 写 spec 骨架 + TC-TRACE-04**

```ts
/**
 * AI 轨迹分析功能正确性 E2E（spec §4，TC-TRACE-01~05）。
 * fail-closed 三分支确定性触发（设置开关 / restartBackend 死端口）；
 * 分析闭环只做 @llm 冒烟（TC-TRACE-05）。
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { api, gotoWithAuth, restartBackend } from './helpers'
import { API, authHeaders, adminToken, dbSeed } from './batch/toolbox'
import { newId, seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'
import { resolveWorkspaceRoot } from './fit-seed'

test.setTimeout(180_000)

function analysisRowCount(sid: string): number {
  return Number(dbSeed(`SELECT count(*) FROM ai_chat_sessions WHERE title = '轨迹分析: ${sid}'`)[0][0])
}

test('TC-TRACE-04 分析历史与报告契约（种子：snake_case 历史 / camelCase 轮询 / report 形状）', async ({ request }) => {
  const key = newId('ana-')
  const sid = await seedPlainSession({ key })
  const asid = newId('sess_')
  dbSeed(`INSERT INTO ai_chat_sessions (id, user_id, title, status, kind) VALUES ('${asid}', 'user-admin', '轨迹分析: ${sid}', 'completed', 'trace_analysis')`)
  const aDone = newId('ana_')
  const aPending = newId('ana_')
  dbSeed(`INSERT INTO ai_execution_diagnoses (id, target_session_id, analysis_session_id, status, report, created_at)
          VALUES ('${aDone}', '${sid}', '${asid}', 'completed', '{"summary":"e2e-seed","findings":[]}', NOW())`)
  dbSeed(`INSERT INTO ai_execution_diagnoses (id, target_session_id, analysis_session_id, status, created_at)
          VALUES ('${aPending}', '${sid}', '${asid}', 'pending', NOW() - INTERVAL '5 minutes')`)
  try {
    // v2 列表契约：trace_analysis 会话默认隐藏 / kind 过滤三态
    const q = async (p: string) =>
      (await api(request, 'get', `/ai/chat/admin/sessions/v2?pageSize=100&keyword=${key}${p}`)).json
    expect(q('').items.map((x: any) => x.id)).not.toContain(asid)
    expect(q('&kind=all').items.map((x: any) => x.id)).toContain(asid)
    expect(q('&kind=trace_analysis').items.map((x: any) => x.id)).toEqual([asid])
    // 会话维度历史（snake_case + created_at DESC）
    const hist = (await api(request, 'get', `/ai/chat/admin/sessions/v2/${sid}/analyses`)).json.analyses
    expect(hist.map((x: any) => x.id)).toEqual([aDone, aPending])
    expect(hist[0]).toMatchObject({ status: 'completed', analysis_session_id: asid })
    // 状态轮询（camelCase 契约）+ 404
    const st = (await api(request, 'get', `/ai/chat/admin/analyses/${aDone}`)).json
    expect(st).toMatchObject({ analysisId: aDone, targetSessionId: sid, analysisSessionId: asid, status: 'completed' })
    expect((await api(request, 'get', `/ai/chat/admin/analyses/${newId('ana_')}`)).status).toBe(404)
    // 报告契约：已完成行回种子 JSONB；pending 行无 summary
    const rep = (await api(request, 'get', `/ai/chat/admin/analyses/${aDone}/report`)).json
    expect(rep.report).toMatchObject({ summary: 'e2e-seed' })
    const repEmpty = (await api(request, 'get', `/ai/chat/admin/analyses/${aPending}/report`)).json
    expect(repEmpty.status).toBe('pending')
    expect(repEmpty.report.summary).toBeUndefined()
  } finally {
    dbSeed(`DELETE FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${asid}'`)   // 种子分析会话标题不带 AITEST 前缀，按 id 清
    cleanupSessionsByPrefix()
  }
})
```

- [ ] **Step 3: 运行** — `npx playwright test e2e/ai-full/ai-trace-analysis.spec.ts`，Expected: 1 passed。
- [ ] **Step 4: Commit** — `git add -A e2e/ai-full && git commit -m "test(e2e): restartBackend 共享化至 helpers + 轨迹分析 TC-TRACE-04 历史/报告契约"`

### Task 13: TC-TRACE-01 内置 MCP 禁用 → 409 零残留

**Files:** Modify `ai-trace-analysis.spec.ts`

**关键事实**：开关 `PUT /ai/mcp-servers/internal {enabled}`（权限 `admin.ai_settings`，走 vite 代理 `/api` 前缀）；analyze 在**建任何行之前**检查 `internal_mcp_enabled()`（读 `ai_settings.mcp_internal_enabled`）→ 409 文案含「内置 MCP 已被禁用」。

- [ ] **Step 1: 追加用例**

```ts
test('TC-TRACE-01 fail-closed：内置 MCP 禁用 → 409 且会话/诊断零残留', async ({ request }) => {
  const key = newId('tr1-')
  const sid = await seedPlainSession({ key })
  try {
    expect((await api(request, 'put', '/ai/mcp-servers/internal', { enabled: false })).status).toBe(200)
    try {
      const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
      expect(r.status).toBe(409)
      expect(r.json.error).toContain('内置 MCP 已被禁用')
      expect(analysisRowCount(sid)).toBe(0)
      expect(Number(dbSeed(`SELECT count(*) FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)[0][0])).toBe(0)
    } finally {
      expect((await api(request, 'put', '/ai/mcp-servers/internal', { enabled: true })).status).toBe(200)
    }
  } finally { cleanupSessionsByPrefix() }
})
```

- [ ] **Step 2: 运行** — Expected: 2 passed。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 轨迹分析 TC-TRACE-01 MCP 禁用 409 fail-closed 零残留"`

### Task 14: TC-TRACE-02 MCP 不可达 → 502（进程级）

**Files:** Modify `ai-trace-analysis.spec.ts`

- [ ] **Step 1: 追加用例**

```ts
test('TC-TRACE-02 fail-closed：MCP 不可达 → 502（进程级 env 覆盖）', async ({ request }) => {
  test.setTimeout(240_000)
  const key = newId('tr2-')
  const sid = await seedPlainSession({ key })
  try {
    await restartBackend({ MCP_SERVER_URL: 'http://127.0.0.1:1' })
    const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
    expect(r.status).toBe(502)
    expect(r.json.error).toContain('MCP 服务不可用')
    expect(analysisRowCount(sid)).toBe(0)
  } finally {
    await restartBackend()          // 无参恢复默认 env
    cleanupSessionsByPrefix()
  }
})
```

- [ ] **Step 2: 运行** — Expected: 3 passed（含两次后端重启，各 ≤60s 探活）。注意 restartBackend 后 3002 是裸 `python app.py` 进程——批次后续用例照常可用；Batch 收尾后手动 `npm run dev:all` 恢复完整四服务（OpenCode/MCP 本就未被杀，仅后端进程形态变化）。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 轨迹分析 TC-TRACE-02 MCP 不可达 502（进程级确定性触发）"`

### Task 15: TC-TRACE-03 派发失败清理（三处残留核对）

**Files:** Modify `ai-trace-analysis.spec.ts`

**关键事实**：`OPENCODE_BASE_URL` 指死端口时 MCP 健康预检通过（MCP 真身在 3003 未动）→ 会话+诊断行已插 → `client.create_session` 抛错 → `_fail(502, 'OpenCode 会话创建失败: …')`：删 messages/sessions 行、diagnosis 置 failed（error_message 截 1000 字）、清工作区、停 listener。

- [ ] **Step 1: 追加用例**

```ts
function listSessWorkspacePaths(root: string): Set<string> {
  // 收集 ai-workspaces 树内 sess_<12hex> 形态的路径（分析会话工作区命名）
  const out = new Set<string>()
  const walk = (dir: string) => {
    let entries: fs.Dirent[]
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = path.join(dir, e.name)
      if (/sess_[0-9a-f]{12}/.test(e.name)) out.add(p)
      else if (e.isDirectory()) walk(p)
    }
  }
  walk(root)
  return out
}

test('TC-TRACE-03 派发失败清理：diagnosis failed + 无孤儿会话行 + 工作区零残留', async ({ request }) => {
  test.setTimeout(240_000)
  const key = newId('tr3-')
  const sid = await seedPlainSession({ key })
  const wsRoot = resolveWorkspaceRoot()          // 来自 ../fit-seed（跨 spec 复用该 helper）
  const before = listSessWorkspacePaths(wsRoot)
  try {
    await restartBackend({ OPENCODE_BASE_URL: 'http://127.0.0.1:1' })
    const r = await api(request, 'post', `/ai/chat/admin/sessions/v2/${sid}/analyze`)
    expect(r.status).toBe(502)
    expect(r.json.error).toContain('OpenCode 会话创建失败')
    const d = dbSeed(`SELECT status, error_message FROM ai_execution_diagnoses WHERE target_session_id='${sid}' ORDER BY created_at DESC LIMIT 1`)
    expect(d).toHaveLength(1)
    expect(d[0][0]).toBe('failed')
    expect(String(d[0][1])).toContain('OpenCode 会话创建失败')
    expect(analysisRowCount(sid)).toBe(0)
    const grown = listSessWorkspacePaths(wsRoot).difference(before)   // Node 22+ Set.prototype.difference
    expect([...grown]).toEqual([])
  } finally {
    await restartBackend()
    cleanupSessionsByPrefix()
  }
})
```

（Node 版本若 <22 无 `Set.prototype.difference`，改为手动 filter。）

- [ ] **Step 2: 运行** — Expected: 4 passed。工作区快照差集窗口内若有其他真实 AI 活动可能误报——workers=1 且窗口仅数秒，若出现先核对差集路径归属再定性。
- [ ] **Step 3: Commit** — `git commit -am "test(e2e): 轨迹分析 TC-TRACE-03 派发失败三处残留核对（§22 P0-8）"`

### Task 16: TC-TRACE-05 @llm 分析闭环

**Files:** Modify `ai-trace-analysis.spec.ts`；文件头 helpers import 行补 `openChatSession`（`adminToken`/`API`/`authHeaders` Task 12 已引入）。

**实施注意**：`openChatSession` 直达 `?session=` 深链存在挂载竞态（记忆条目：会话列表加载但会话未打开的空态）——`composer.fill` 前若输入框不可见，reload 一次再等（参考 ai-harness-safety 的重试兜底手法）。

- [ ] **Step 1: 追加用例**

```ts
test('TC-TRACE-05 @llm 轨迹分析闭环：真实会话→触发→收敛→报告结构→抽屉历史', async ({ page, request }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  test.setTimeout(600_000)
  const tk = await adminToken()
  // 1) 真实会话真跑一轮（API 建会话改名 + UI 发消息，规避自动创建竞态）
  const create = await fetch(`${API}/ai/chat/sessions`, { method: 'POST', headers: { ...authHeaders(tk), 'Content-Type': 'application/json' }, body: '{}' })
  expect(create.status).toBeLessThan(300)
  const sid = (await create.json()).id
  const title = newId('AITEST-trace-live-')
  await fetch(`${API}/ai/chat/sessions/${sid}`, { method: 'PATCH', headers: authHeaders(tk), body: JSON.stringify({ title }) })
  await openChatSession(page, sid)
  const composer = page.getByPlaceholder(/给 AI 助手发消息/)
  await composer.fill('hello trace e2e')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.msg--assistant').first()).toBeVisible({ timeout: 180_000 })
  // 2) API 触发轨迹分析
  const r = await fetch(`${API}/ai/chat/admin/sessions/v2/${sid}/analyze`, { method: 'POST', headers: authHeaders(tk) })
  if (r.status === 502) return test.skip(true, 'LLM/MCP 链路不可用（analyze 502），冒烟跳过')
  expect(r.status).toBe(200)
  const { analysisId, analysisSessionId } = await r.json()
  try {
    // 3) 轮询至终态并要求 completed
    let final: any = null
    for (let i = 0; i < 96; i++) {
      const s = await (await fetch(`${API}/ai/chat/admin/analyses/${analysisId}`, { headers: authHeaders(tk) })).json()
      if (['completed', 'partial', 'failed'].includes(s.status)) { final = s; break }
      await page.waitForTimeout(5000)
    }
    expect(final?.status).toBe('completed')
    // 4) 报告结构完整（断言结构，不断言内容语义）
    const rep = await (await fetch(`${API}/ai/chat/admin/analyses/${analysisId}/report`, { headers: authHeaders(tk) })).json()
    expect(Object.keys(rep.report ?? {}).length).toBeGreaterThan(0)
    expect(analysisRowCount(sid)).toBe(1)
    expect(dbSeed(`SELECT count(*) FROM ai_chat_sessions WHERE id='${analysisSessionId}' AND kind='trace_analysis'`)[0][0]).toBe(1)
    // 5) 审计抽屉「轨迹分析历史」出现该行
    await gotoWithAuth(page, '/admin/ai-execution?tab=sessions')
    const row = page.locator('.el-table__row', { hasText: title }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    await row.locator('.el-dropdown').first().click()
    await page.locator('.el-dropdown-menu__item', { hasText: '执行审计' }).first().click()
    const drawer = page.locator('.el-drawer', { hasText: '执行合规审计' })
    await expect(drawer.getByText('轨迹分析历史')).toBeVisible({ timeout: 15_000 })
    await expect(drawer.locator('.analysis-row').first()).toBeVisible()
  } finally {
    // 定点清理：目标会话 + 分析会话 + 诊断行（按 id，防误删）
    dbSeed(`DELETE FROM ai_execution_diagnoses WHERE target_session_id='${sid}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${analysisSessionId}'`)
    dbSeed(`DELETE FROM ai_chat_sessions WHERE id='${sid}'`)
  }
})
```

- [ ] **Step 2: 运行** — Expected: passed（LLM 可用）或 skip（留证）。真实分析会话跑完通常 1-3 分钟；`completed` 是硬断言，failed 即红（那是要登记的真问题）。
- [ ] **Step 3: Batch 3 收尾回归**：`npx playwright test e2e/execution-audit.spec.ts e2e/ai-full/ai-governance-audit.spec.ts`，Expected: 全绿。
- [ ] **Step 4: Commit** — `git commit -am "test(e2e): 轨迹分析 TC-TRACE-05 @llm 分析闭环冒烟；Batch 3 完成"`

### Task 17: 判别力登记表 + 全量回归 + 准出

**Files:**
- Create: `docs/ai-testing/evidence/2026-10-06-ai-linked-correctness-判别力登记.md`

- [ ] **Step 1: 写判别力登记表**——每例一行：用例号 / 判别力点（预期抓取的缺陷）/ 基线结果（main 全绿）/ 验证方式。判别力点取自 spec §2-4 各表加粗项，至少覆盖：source_type 计算列优先级与 kind 过滤 SQL、关键词 EXISTS 路径、批控归档 409、uq 幂等 upsert、apply sha256-落盘自洽与回滚零虚假版本、runtime 不降级、MCP 三分支 fail-closed 零残留、报告契约形状。
- [ ] **Step 2: 全量回归**（防串扰，workers=1 串行约 60-70 分钟，分片跑）：

```bash
npx playwright test e2e/ai-full --shard=1/2
npx playwright test e2e/ai-full --shard=2/2
```
Expected: 既有 ~146 例 + 新增 16 例全绿（@llm 允许 skip 留证；模型行为类天然波动复跑一次）。
- [ ] **Step 3: Commit** — `git commit -am "docs(test): AI联动正确性套件判别力登记表 + 全量回归证据"`
- [ ] **Step 4: 汇报**——向用户提交结果摘要（每批 commit 号、全量回归数字、skip 留证），按用户惯例决定是否合 main。
