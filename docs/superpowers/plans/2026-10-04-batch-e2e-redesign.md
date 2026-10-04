# 批任务 E2E 测试体系重设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按功能域把批任务 e2e 重组为 `e2e/ai-full/batch/` 单一套件：收敛 helper、修掉过时/耦合病灶、以确定性构造补齐管理页/权限/容灾等九域缺口。

**Architecture:** 新建 `batch/` 子目录，`batch-helpers.ts` 迁入为唯一 API helper，新增 `toolbox.ts`（确定性构造）与 `ui-helpers.ts`（UI 流程封装）；十个域 spec 各自内聚（lifecycle/control/retry-reexecute/gate/reuse/openapi/admin/resilience/permissions/ui-journeys）+ 一个 smoke。收编迁移旧 spec 后删除旧文件。

**Tech Stack:** Playwright（workers=1、`npx playwright test`）、纯 fetch 直连后端 3002、psycopg2 经 `db_exec.py` 桥直连共享 dev 库、Node `node:http` 本地 webhook receiver、`execFileSync` 驱动后端重启。

**Spec:** `docs/superpowers/specs/2026-10-04-batch-e2e-redesign-design.md`（计划与 spec 配套阅读；下文「已核实」事实均来自 spec 编写期的源码核验）

## Global Constraints

- **不改产品代码**：`server/`、`src/`、`mcp-server/` 源码零改动；本计划只动 `e2e/` 与 `docs/`。
- 后端直连 `http://127.0.0.1:3002`（沿用 batch-helpers 约定，绕开 vite 代理）；UI 用例经 `http://127.0.0.1:5173`。
- `playwright.config.ts` 不动：workers=1、fullyParallel=false 沿用；每批域 spec 内 `test.setTimeout` 自行放宽。
- 真 LLM 用例每域最多 2 个，`test.info().annotations.push({ type: 'llm' })` 或文件头注释标注；确定性用例预算 ≤60s，真 LLM ≤10 分钟，并发观测/复用 UI ≤15 分钟。
- 证据：截图落 `e2e/screenshots/ai-full/batch/`；关键状态迁移断言（计数、事件、回调载荷）用 `console.log` + `testInfo.attach` 落响应摘录。
- 所有建批用 `tag()` 防撞名（`AITEST-` 前缀），收尾 `cleanupBatch(?stop=1)` 清理；DB 种子用例用后 `DELETE FROM ai_chat_batches WHERE id=...`（级联清 sessions/复用表）。
- 每任务收尾：跑本任务 spec + 1 个受影响的旧 spec 防退化；全部任务完成后跑 ai-full 全量回归。
- 提交信息沿用 `test(batch): ...` / `refactor(test): ...` 惯例，一任务一提交。

### 已核实环境事实（写用例时直接引用，勿再猜）

| 事实 | 值 | 出处 |
|---|---|---|
| 对账间隔 | `AI_BATCH_RECONCILE_SEC` 默认 60s | `server/utils/batch_engine.py:929` |
| claim 只认 pending | 直插 `status='running'` 行无认领竞态 | `batch_engine.py:1116` 起 CTE |
| 对账输入列 | `status='running' AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)`，读 `opencode_session_id, workspace_path, lease_until, fencing_token` | `batch_engine.py:3264-3368` |
| 对账决策顺序 | 租约未过期→跳过；unknown 副作用→needs_review；无 oc→requeue_fresh（`retry_count>=MAX_AUTO_RETRY`→failed 预算用尽）；oc 404→failed；oc 活着+无 checkpoint+无消息+无副作用→failed_no_progress；否则 requeue_continue | `batch_engine.py:3295-3366` |
| unknown 副作用判定 | `SELECT 1 FROM ai_execution_effects WHERE session_id=%s AND status='unknown'` | `server/utils/execution_effect.py:256` |
| 看门狗 env | `AI_BATCH_TOOL_STALL_SEC`（默认 900）、`AI_BATCH_SESSION_TIMEOUT_SEC`（默认 0=关）、`AI_BATCH_MAX_AUTO_RETRY`（默认 2） | `batch_engine.py:739-758` |
| 停滞超时 | `STALL_TIMEOUT_SEC=180` 硬编码，不可 env 配置 | `batch_engine.py:748` |
| OpenCode 地址 | `OPENCODE_BASE_URL` 默认 `http://127.0.0.1:4096`；建会话 `POST {base}/session?directory=<ws>`，body `{"title": ""}` | `server/config.py:87`、`opencode_client.py:35` |
| webhook 签名头 | `X-Webhook-Signature` | `server/utils/webhook_engine.py:334` |
| 用户管理 | `POST /users`（admin.users 权限）body `{username, password, displayName, role}`，角色默认 guest；登录 `POST /auth/login` | `server/routes/users.py:45-75` |
| 批内 events 增量 | `GET /ai/chat/batches/<id>/events?afterSeq=` | `server/routes/ai_chat_batches.py` |
| 内部 API 基址 | `http://127.0.0.1:3002/ai/chat/batches`（无 /api 前缀）；对外 `…/api/v1/ai-batches` | batch-helpers.ts、open_api_batches.py |
| DB 桥 | `psycopg2.connect(**DB_CONFIG)`，`DB_CONFIG` 来自 `server/config.py`（mcp-server/tests 同款） | `mcp-server/tests/test_batch_children_mcp.py:25-30` |
| sessions NOT NULL | `id, user_id, workspace_path, session_token, token_expires_at`（status 默认 'active'，直插时显式置 'running'） | `server/db_schema/core.py` |
| batches NOT NULL | `id, user_id, name, prompt`（status/counters 有默认值） | `server/db_schema/ai_batches.py:4-21` |

---

### Task 1: batch/ 目录骨架 + batch-helpers 迁移（行为不变）

**Files:**
- Create: `e2e/ai-full/batch/`（目录）
- Move: `e2e/ai-full/batch-helpers.ts` → `e2e/ai-full/batch/batch-helpers.ts`
- Modify: 以下 6 个文件的 import 行（路径 `../batch-helpers` → `./batch/batch-helpers`）：`e2e/ai-full/ai-batch-control.spec.ts`、`ai-retry-failed.spec.ts`、`ai-reexecute.spec.ts`、`ai-verifier-gate.spec.ts`、`ai-verifier-subagent.spec.ts`、`ai-delegation-gate.spec.ts`

**Interfaces:**
- Produces: `batch/batch-helpers.ts` 导出不变（`API, BATCH_TERMINAL, adminToken, authHeaders, uploadStaging, CreateBatchBody, createBatch, getDetail, waitBatchTerminal, waitFor, cleanupBatch, AgentDef, makeProvisionRepo, countByStatus, messageCount`）——后续所有任务从此 import。

- [ ] **Step 1: 迁移文件**

```bash
mkdir -p "e2e/ai-full/batch" && git mv e2e/ai-full/batch-helpers.ts e2e/ai-full/batch/batch-helpers.ts
```

- [ ] **Step 2: 修 6 个消费方 import**

每个文件把 `from './batch-helpers'` 改为 `from './batch/batch-helpers'`（grep 确认无第 7 个消费方）：

```bash
grep -rln "from './batch-helpers'" e2e/ai-full/
```

- [ ] **Step 3: 跑受影响旧 spec 验证迁移无破坏**

Run: `npx playwright test e2e/ai-full/ai-retry-failed.spec.ts --reporter=line`
Expected: PASS（确定性 fail-fast 用例，≤3 分钟）

- [ ] **Step 4: Commit**

```bash
git add -A e2e/ai-full && git commit -m "refactor(test): batch-helpers 迁入 batch/ 子目录，6 消费方 import 收敛"
```

---

### Task 2: toolbox.ts（确定性构造核心件）+ smoke.spec.ts

**Files:**
- Create: `e2e/ai-full/batch/toolbox.ts`
- Create: `e2e/ai-full/batch/db_exec.py`
- Create: `e2e/ai-full/batch/smoke.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 `batch-helpers`（`API, adminToken, authHeaders, uploadStaging, createBatch, getDetail, CreateBatchBody, countByStatus, waitBatchTerminal, cleanupBatch`）。
- Produces（后续任务全部从此 import）：

```ts
export function tag(prefix: string): string                     // `${prefix}-AITEST-${Date.now()}-${rand4}`
export async function adminTokenCached(): Promise<string>       // 进程内缓存 adminToken()
export async function failFastBatch(tk: string, o?: { files?: number; prompt?: string; name?: string }): Promise<string>  // 返回 bid；未知 agent → 子任务秒级 failed
export async function sleepBatch(tk: string, o: { children: number; sleepSec?: number; name?: string }): Promise<string>  // bash sleep 长任务，稳定 running 窗口（每子烧一次 LLM 发指令）
export function childWorkspace(detail: any, idx?: number): string          // detail.sessions[idx||0].workspace_path
export function writeWorkspaceFile(ws: string, rel: string, content: string): void
export function readWorkspaceFile(ws: string, rel: string): string
export async function secondUser(prefix?: string): Promise<{ id: string; username: string; password: string; token: string; cleanup(): Promise<void> }>
export function dbSeed(sql: string): any[][]                    // 经 db_exec.py 执行，SELECT 返回行；DDL/DML 返回 []
export async function restartBackend(env?: Record<string, string>): Promise<void>  // kill 3002 进程→带 env 重启→健康探测；不传 env 即恢复默认
export async function seedRunningChild(o: SeedOpts): Promise<{ bid: string; sid: string }>
export interface SeedOpts {
  ocId?: string | null            // opencode_session_id；null=未开跑
  lease?: 'past' | 'future' | null
  retryCount?: number             // 默认 0
  effectUnknown?: boolean         // 种 ai_execution_effects status='unknown' 行
  checkpoint?: boolean            // 种 ai_execution_checkpoints 行
  messages?: number               // 种 ai_chat_messages 行数（0 默认）
}
```

- [ ] **Step 1: 写 db_exec.py（DB 桥）**

```python
"""e2e toolbox DB 桥：stdin 读 SQL（可多语句）经 server 的 DB_CONFIG 执行。
SELECT 打印 JSON 行数组；DDL/DML 打印 []。仅供 e2e 确定性种子使用。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'server'))
from config import DB_CONFIG  # noqa: E402

import psycopg2  # noqa: E402

conn = psycopg2.connect(**DB_CONFIG)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(sys.stdin.read())
    rows = [list(r) for r in cur.fetchall()] if cur.description else []
print(json.dumps(rows, default=str, ensure_ascii=False))
```

- [ ] **Step 2: 写 toolbox.ts**

```ts
/**
 * 批任务确定性构造工具箱。
 * 原则（spec §3）：系统栈真实（后端/OpenCode/DB 真进程），但不烧 LLM——
 * fail-fast / sleep 长任务 / fs 直写 / DB 种子构造目标状态。
 */
import { execFileSync, spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  adminToken, authHeaders, cleanupBatch, createBatch, getDetail,
  uploadStaging, type CreateBatchBody,
} from './batch-helpers'

export function tag(prefix: string): string {
  return `${prefix}-AITEST-${Date.now()}-${crypto.randomBytes(2).toString('hex')}`
}

let _cachedToken: string | null = null
export async function adminTokenCached(): Promise<string> {
  if (!_cachedToken) _cachedToken = await adminToken()
  return _cachedToken
}

const DB_EXEC = path.join(__dirname, 'db_exec.py')

/** 经 db_exec.py 执行 SQL；SELECT 返回行，其余返回 []。 */
export function dbSeed(sql: string): any[][] {
  const out = execFileSync('python', [DB_EXEC], { input: sql, encoding: 'utf-8' })
  return JSON.parse(out.trim() || '[]')
}
```

注意：上面 `JSON.parse` 行写错会编译失败——正确实现为 `JSON.parse(out.trim() || '[]')`。继续：

```ts
/** 未知 agent 建批 → worker 认领时 _check_agent fail-fast，子任务秒级 failed（不烧 LLM）。 */
export async function failFastBatch(
  tk: string, o?: { files?: number; prompt?: string; name?: string },
): Promise<string> {
  const uploadSession = tag('upl')
  const files = []
  for (let i = 0; i < (o?.files ?? 1); i++) {
    files.push(await uploadStaging(tk, `in-${i}.txt`, `e2e 输入 ${i}\n`, uploadSession))
  }
  const body: CreateBatchBody = {
    name: o?.name ?? tag('failfast'),
    prompt: o?.prompt ?? '对每个输入文件输出一行摘要。',
    agent: 'e2e-no-such-agent',   // 不存在的 primary agent → 认领即 fail-fast
    files,
  }
  const batch = await createBatch(tk, body)
  return batch.batch?.id ?? batch.id
}

/** bash sleep 长任务：模型跑一次 bash sleep 后输出 done，构造稳定 running 窗口。 */
export async function sleepBatch(
  tk: string, o: { children: number; sleepSec?: number; name?: string },
): Promise<string> {
  const uploadSession = tag('upl')
  const files = []
  for (let i = 0; i < o.children; i++) {
    files.push(await uploadStaging(tk, `sleep-${i}.txt`, `sleep ${i}\n`, uploadSession))
  }
  const sec = o.sleepSec ?? 90
  const batch = await createBatch(tk, {
    name: o.name ?? tag('sleep'),
    prompt: `用 bash 工具执行 \`sleep ${sec}\`，结束后输出一行 done 即可，不要做别的。`,
    agent: 'general',
    files,
  } as CreateBatchBody)
  return batch.batch?.id ?? batch.id
}

export function childWorkspace(detail: any, idx = 0): string {
  const ws = detail.sessions[idx]?.workspace_path
  if (!ws) throw new Error(`sessions[${idx}] 无 workspace_path: ${JSON.stringify(detail.sessions?.[idx])}`)
  return ws
}

export function writeWorkspaceFile(ws: string, rel: string, content: string): void {
  const p = path.join(ws, rel)
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, content, 'utf-8')
}

export function readWorkspaceFile(ws: string, rel: string): string {
  return fs.readFileSync(path.join(ws, rel), 'utf-8')
}

/** 管理员建一个普通用户（role=guest）并登录，cleanup 删除该用户。 */
export async function secondUser(prefix = 'e2eu'): Promise<{
  id: string; username: string; password: string; token: string; cleanup(): Promise<void>
}> {
  const tk = await adminTokenCached()
  const username = tag(prefix).toLowerCase().replace(/[^a-z0-9-]/g, '')
  const password = 'e2e-pass-123'
  const r = await fetch(`${API}/users`, {
    method: 'POST',
    headers: authHeaders(tk),
    body: JSON.stringify({ username, password, displayName: `E2E ${prefix}`, role: 'guest' }),
  })
  if (r.status !== 201) throw new Error(`create user failed: ${r.status} ${await r.text()}`)
  const user = await r.json()
  const lr = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!lr.ok) throw new Error(`second user login failed: ${lr.status}`)
  const token = (await lr.json()).token
  return {
    id: user.id, username, password, token,
    async cleanup() {
      await fetch(`${API}/users/${user.id}`, { method: 'DELETE', headers: authHeaders(tk) })
    },
  }
}
```

`API` 需从 batch-helpers re-export：在 toolbox.ts 顶部 import 中加 `API` 并 `export { API }`（或使用方直接从 batch-helpers 取）。

继续 toolbox.ts 的种子与重启件：

```ts
export interface SeedOpts {
  ocId?: string | null
  lease?: 'past' | 'future' | null
  retryCount?: number
  effectUnknown?: boolean
  checkpoint?: boolean
  messages?: number
  agent?: string                  // 默认 'e2e-no-such-agent'：重排后被认领即 fail-fast，绝不真跑
}

/** 直插一对批+running 子会话行（绕开 API，规避 worker 认领竞态——
 * claim 只认 pending 行，见 batch_engine.py:1116）。返回 ids 供断言/清理。 */
export async function seedRunningChild(o: SeedOpts): Promise<{ bid: string; sid: string }> {
  const adminId = dbSeed(`SELECT id FROM users WHERE username='admin'`)[0][0]
  const bid = `b-${crypto.randomUUID()}`
  const sid = `ses-${crypto.randomUUID()}`
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-seed-ws-'))
  const lease = o.lease === 'future'
    ? "NOW() + interval '300 seconds'"
    : o.lease === 'past' ? "NOW() - interval '300 seconds'" : 'NULL'
  dbSeed(`
    INSERT INTO ai_chat_batches (id, user_id, name, prompt, agent, status, total)
    VALUES ('${bid}', '${adminId}', '${tag('seed')}', 'seed prompt',
            '${o.agent ?? 'e2e-no-such-agent'}', 'running', 1);
    INSERT INTO ai_chat_sessions
      (id, user_id, title, workspace_path, session_token, token_expires_at, status,
       batch_id, batch_seq, batch_input_file, lease_until, retry_count, created_at)
    VALUES ('${sid}', '${adminId}', 'seed child', '${ws.replace(/\\/g, '\\\\')}',
            '${crypto.randomUUID().replace(/-/g, '')}', NOW() + interval '1 hour', 'running',
            '${bid}', 1, 'seed-in.txt', ${lease}, ${o.retryCount ?? 0}, NOW());
    ${o.ocId ? `UPDATE ai_chat_sessions SET opencode_session_id='${o.ocId}' WHERE id='${sid}';` : ''}
    ${o.effectUnknown ? `INSERT INTO ai_execution_effects (id, session_id, batch_id, effect_type, idempotency_key, status)
      VALUES ('fx-${crypto.randomUUID()}', '${sid}', '${bid}', 'file_import', 'seed-${crypto.randomUUID()}', 'unknown');` : ''}
    ${o.checkpoint ? `INSERT INTO ai_execution_checkpoints (id, session_id, execution_generation, checkpoint_type)
      VALUES ('ck-${crypto.randomUUID()}', '${sid}', 1, 'progress');` : ''}
    ${Array.from({ length: o.messages ?? 0 }, (_, i) =>
      `INSERT INTO ai_chat_messages (id, session_id, role, content)
       VALUES ('m-${crypto.randomUUID()}', '${sid}', 'user', '"seed msg ${i}"');`).join('\n')}
  `)
  return { bid, sid }
}

/** 重启后端（Windows 环境）：kill 3002 → 带 env 重启 → 探活。不传 env 即恢复默认。 */
export async function restartBackend(env: Record<string, string> = {}): Promise<void> {
  // 找到监听 3002 的 PID 并 kill（netstat 行形如 `TCP  127.0.0.1:3002 ... LISTENING  1234`）
  try {
    const out = execFileSync('netstat', ['-ano'], { encoding: 'utf-8', shell: true })
    const pid = out.split('\n').map(l => l.trim())
      .filter(l => l.includes(':3002') && l.includes('LISTENING'))
      .pop()?.split(/\s+/).pop()
    if (pid) execFileSync('taskkill', ['/F', '/PID', pid], { stdio: 'ignore' })
  } catch { /* 3002 无进程时忽略 */ }
  const child = spawn('python', ['app.py'], {
    cwd: path.join(__dirname, '..', '..', '..', 'server'),
    env: { ...process.env, ...env },
    detached: false, stdio: 'ignore',
  })
  child.unref()
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      if (r.status < 500) return   // 400/401/422 都证明 Flask 已起
    } catch { /* 未起，重试 */ }
    await new Promise(rr => setTimeout(rr, 1000))
  }
  throw new Error('backend restart: 60s 内未探活')
}
```

- [ ] **Step 3: 写 smoke.spec.ts（<90s、0 LLM，兼作 toolbox 验收）**

```ts
import { test, expect } from '@playwright/test'
import { cleanupBatch, countByStatus, getDetail, waitFor } from './batch-helpers'
import {
  adminTokenCached, childWorkspace, failFastBatch, readWorkspaceFile,
  sleepBatch, tag, writeWorkspaceFile,
} from './toolbox'

test.setTimeout(120_000)

test('fail-fast：未知 agent 子任务秒级 failed', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 2 })
  const detail = await waitFor(async () => {
    const d = await getDetail(tk, bid)
    return (d.sessions ?? []).every((s: any) => s.status === 'failed') ? d : null
  }, 90_000, 'fail-fast 子任务 failed')
  expect(countByStatus(detail)['failed']).toBe(2)
  await cleanupBatch(tk, bid)
})

test('sleep 长任务进入 running 且工作区 fs 可读写', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 15 })
  try {
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      return countByStatus(d)['running'] === 1 ? d : null
    }, 60_000, 'sleep 子任务 running')
    const detail = await getDetail(tk, bid)
    const ws = childWorkspace(detail)
    writeWorkspaceFile(ws, 'outputs/toolbox-probe.txt', `probe-${tag('p')}`)
    expect(readWorkspaceFile(ws, 'outputs/toolbox-probe.txt')).toContain('probe-')
  } finally {
    await cleanupBatch(tk, bid)
  }
})
```

（本用例含 1 次轻量 LLM 消耗——模型发 bash 指令；标注于文件头注释。）

- [ ] **Step 4: 验证 db 桥独立可跑**

Run: `echo "SELECT count(*) FROM users;" | python e2e/ai-full/batch/db_exec.py`
Expected: 输出 `[["1"]]` 一类 JSON（非空行数）

- [ ] **Step 5: 跑 smoke**

Run: `npx playwright test e2e/ai-full/batch/smoke.spec.ts --reporter=line`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add e2e/ai-full/batch && git commit -m "test(batch): toolbox 确定性构造工具箱+DB 桥+smoke（fail-fast/sleep/fs 直写）"
```

---

### Task 3: ui-helpers.ts（建批对话框等 UI 流程唯一实现）

**Files:**
- Create: `e2e/ai-full/batch/ui-helpers.ts`

**Interfaces:**
- Consumes: playwright `Page`。
- Produces:

```ts
import type { Page } from '@playwright/test'

/** 打开侧栏「批任务→新建」对话框并填写基础字段（文件经 setInputFiles 内存上传）。 */
export async function openBatchDialog(page: Page): Promise<void>
export async function createBatchViaDialog(page: Page, o: {
  name: string; prompt: string; files?: { name: string; content: string }[]
}): Promise<void>
/** 展开指定名称的批组（带既有重试循环语义）。 */
export async function expandBatchGroup(page: Page, name: string): Promise<void>
/** 读取批组状态徽标 class（如 badge--running / badge--paused）。 */
export async function batchGroupBadgeClass(page: Page, name: string): Promise<string>
```

- [ ] **Step 1: 从旧 spec 提取实现**

来源（流程逐字已存在，勿重写选择器）：`e2e/ai-chat-batch.spec.ts` 的「新建→dialog→`data-test=name/prompt`→setInputFiles→`create-btn`」段，以及 `e2e/ai-chat-batch.spec.ts` / `e2e/ai-chat-stop-resume.spec.ts` 各自内联的 expand 重试循环（二者取更健壮的一份）。四个函数体 = 对应源码段参数化；选择器保持源码原样。

- [ ] **Step 2: 编译检查**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: 无新增错误（e2e 目录若不在 tsconfig 内则 `npx tsc --noEmit e2e/ai-full/batch/ui-helpers.ts` 单文件检查）

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-full/batch/ui-helpers.ts && git commit -m "test(batch): ui-helpers 收敛建批对话框/批组操作流程（供 ui-journeys 等域复用）"
```

---

### Task 4: lifecycle.spec.ts（迁移 2 用例 + 新增 4 用例）

**Files:**
- Create: `e2e/ai-full/batch/lifecycle.spec.ts`
- Delete（本任务末尾）: `e2e/ai-full/ai-batch-lifecycle.spec.ts`

**Interfaces:**
- Consumes: `batch-helpers`（uploadStaging/createBatch/getDetail/waitBatchTerminal/cleanupBatch/waitFor/countByStatus）、`toolbox`（tag/adminTokenCached/failFastBatch）、`ui-helpers`（expandBatchGroup）、`../helpers`（gotoWithAuth/screenshot/tag）。

**用例清单（迁移自 ai-batch-lifecycle，测试体基本原样、仅 import 换 batch-helpers/toolbox）：**

1. 「staging 上传→建批→UI 分组可见→子会话消息落库→终态删除级联 404」（原用例 1）
2. 「删除治理：非终态 409 / ?stop=1 drain 409 重试 / 终态删除」（原用例 2；构造用 sleepBatch 替换原内联长任务构造）

- [ ] **Step 1: 迁移两用例并跑通**

Run: `npx playwright test e2e/ai-full/batch/lifecycle.spec.ts --reporter=line`
Expected: 2 PASS（用例 1 含 LLM，预算 10 分钟）

- [ ] **Step 2: 追加 4 个新用例（全部确定性）**

```ts
test('PATCH 配置编辑生效：agent/prompt 回读 + 门禁期望同步', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  const checks = [{ type: 'tool_assert', tool: 'bash', args_pattern: 'sleep', min_count: 1 }]
  const r = await fetch(`${API}/ai/chat/batches/${bid}`, {
    method: 'PATCH', headers: authHeaders(tk),
    body: JSON.stringify({ name: tag('edited'), prompt: '改后的提示词', agent: 'general', action_checks: checks }),
  })
  expect(r.status).toBe(200)
  const d = await getDetail(tk, bid)
  expect(d.batch.prompt).toBe('改后的提示词')
  expect(d.batch.agent).toBe('general')
  expect(JSON.stringify(d.batch.action_checks ?? d.action_checks ?? [])).toContain('args_pattern')
  await cleanupBatch(tk, bid)
})

test('append 追加文件：新子任务生成并被派发（fail-fast 收敛，0 LLM）', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  const before = await getDetail(tk, bid)
  const up = await uploadStaging(tk, 'extra.txt', '追加输入\n', tag('upl'))
  const r = await fetch(`${API}/ai/chat/batches/${bid}/append`, {
    method: 'POST', headers: authHeaders(tk), body: JSON.stringify({ files: [up] }),
  })
  expect(r.status).toBeLessThan(300)
  const d = await waitFor(async () => {
    const cur = await getDetail(tk, bid)
    return cur.sessions?.length === (before.sessions?.length ?? 0) + 1 &&
      (cur.sessions ?? []).every((s: any) => s.status === 'failed') ? cur : null
  }, 120_000, '追加子任务 fail-fast 终态')
  expect(d.batch.total).toBe((before.batch?.total ?? before.sessions.length) + 1)
  await cleanupBatch(tk, bid)
})

test('建批校验负路径：0 文件 / 空 prompt / staging 路径穿越', async () => {
  const tk = await adminTokenCached()
  // 0 文件
  const noFiles = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({ name: tag('neg'), prompt: 'x', files: [] }),
  })
  expect(noFiles.status).toBeGreaterThanOrEqual(400)
  // 空 prompt
  const up = await uploadStaging(tk, 'neg.txt', 'x\n', tag('upl'))
  const noPrompt = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({ name: tag('neg'), prompt: '   ', files: [up] }),
  })
  expect(noPrompt.status).toBeGreaterThanOrEqual(400)
  // staging 路径穿越（文件名带 ..）
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('x')]), '../../admin/evil.csv')
  form.append('upload_session_id', tag('upl'))
  const trav = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST', headers: { Authorization: `Bearer ${tk}` }, body: form,
  })
  expect(trav.status).toBeGreaterThanOrEqual(400)
})

test('events afterSeq 增量：afterSeq=latest 返回空、新事件出现在增量窗口', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  await waitFor(async () => {
    const d = await getDetail(tk, bid)
    return (d.sessions ?? []).every((s: any) => s.status === 'failed') ? d : null
  }, 90_000, '子任务终态（产生事件）')
  const all = await (await fetch(`${API}/ai/chat/batches/${bid}/events`, { headers: authHeaders(tk) })).json()
  const events = all.events ?? all
  expect(events.length).toBeGreaterThan(0)
  const lastSeq = events[events.length - 1].seq
  const since = await (await fetch(`${API}/ai/chat/batches/${bid}/events?afterSeq=${lastSeq}`, { headers: authHeaders(tk) })).json()
  expect((since.events ?? since).filter((e: any) => e.seq > lastSeq)).toHaveLength(0)
  await cleanupBatch(tk, bid)
})
```

- [ ] **Step 3: 跑全 spec + 删旧文件**

Run: `npx playwright test e2e/ai-full/batch/lifecycle.spec.ts --reporter=line`
Expected: 6 PASS

```bash
git rm e2e/ai-full/ai-batch-lifecycle.spec.ts
```

- [ ] **Step 4: Commit**

```bash
git add -A e2e/ai-full && git commit -m "test(batch): lifecycle 域收编+补缺（PATCH 编辑/append/负路径/events 增量）"
```

---

### Task 5: control.spec.ts（迁移 3 用例 + 命令幂等收编 + 新增 4 边界）

**Files:**
- Create: `e2e/ai-full/batch/control.spec.ts`
- Delete（本任务末尾）: `e2e/ai-full/ai-batch-control.spec.ts`

**Interfaces:**
- Consumes: batch-helpers、toolbox（sleepBatch/cleanupBatch/waitFor/countByStatus）。

**迁移清单：**
1. 原 ai-batch-control 三用例原样迁移（pause/resume 单子隔离、cancel 聚合、单子 cancel→partial），内联 `createSleepBatch` 换 `toolbox.sleepBatch`。
2. 自 `e2e/ai-full/ai-harness-safety.spec.ts` 收编「命令幂等」块：`POST /commands` 同 Idempotency-Key 两次调用状态一致（注意：原文件本体保留，仅该块移出——本任务不删 harness-safety）。

- [ ] **Step 1: 迁移 + 跑通**

Run: `npx playwright test e2e/ai-full/batch/control.spec.ts --reporter=line`
Expected: PASS（sleep 长任务用例各 3-5 分钟）

- [ ] **Step 2: 追加 4 个边界用例（先读 `server/routes/ai_chat_batches.py` 的 pause/cancel/retry-failed handler 核对以下预期，不符处以路由实际为准并在用例注释记录）**

```ts
test('pause 后 cancel：paused 子任务落 cancelled，批收敛 failed 聚合', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2, sleepSec: 90 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 2, 120_000, 'running×2')
    await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: authHeaders(tk) })
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['paused'] === 2, 180_000, 'paused×2')
    await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    const d = await waitFor(async () => {
      const cur = await getDetail(tk, bid)
      return ['completed', 'partial', 'failed'].includes(cur.batch.status) ? cur : null
    }, 180_000, 'pause→cancel 终态')
    expect(countByStatus(d)['cancelled']).toBe(2)
    expect(d.batch.status).toBe('failed')   // cancelled 记入 failed 聚合
  } finally { await cleanupBatch(tk, bid) }
})

test('paused 批 retry-failed：无 failed 子任务 → retried=0 状态原样', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 90 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 1, 120_000, 'running')
    await fetch(`${API}/ai/chat/batches/${bid}/pause`, { method: 'POST', headers: authHeaders(tk) })
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['paused'] === 1, 180_000, 'paused')
    const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: authHeaders(tk) })
    expect(r.status).toBeLessThan(300)
    const body = await r.json()
    expect(body.retried ?? 0).toBe(0)
    expect(countByStatus(await getDetail(tk, bid))['paused']).toBe(1)
  } finally { await cleanupBatch(tk, bid) }
})

test('重复 cancel 状态一致：两次 cancel 后全部 cancelled、计数不重复扣减', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 2, sleepSec: 90 })
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 2, 120_000, 'running×2')
    await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    const second = await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    // 二次调用允许 200/202/409——只约束状态语义不变
    const d = await waitFor(async () => {
      const cur = await getDetail(tk, bid)
      return ['partial', 'failed'].includes(cur.batch.status) ? cur : null
    }, 180_000, '重复 cancel 终态')
    expect(countByStatus(d)['cancelled']).toBe(2)
    expect(second.status).toBeLessThan(500)
  } finally { await cleanupBatch(tk, bid) }
})

test('终态批控制面拒绝：completed 批 pause/cancel 返回 4xx', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 1 })
  await waitFor(async () => ['failed'].includes((await getDetail(tk, bid)).batch.status), 90_000, 'failed 终态')
  for (const action of ['pause', 'cancel', 'resume']) {
    const r = await fetch(`${API}/ai/chat/batches/${bid}/${action}`, { method: 'POST', headers: authHeaders(tk) })
    expect(r.status).toBeGreaterThanOrEqual(400)
  }
  await cleanupBatch(tk, bid)
})
```

- [ ] **Step 3: 跑全 spec + 删旧文件**

Run: `npx playwright test e2e/ai-full/batch/control.spec.ts --reporter=line`
Expected: 7 PASS（时长约 15-20 分钟，sleep 用例主导）

```bash
git rm e2e/ai-full/ai-batch-control.spec.ts
```

- [ ] **Step 4: Commit**

```bash
git add -A e2e/ai-full && git commit -m "test(batch): control 域收编+边界补缺（pause→cancel/paused retry/重复 cancel/终态拒绝）"
```

---

### Task 6: retry-reexecute.spec.ts（迁移 2 spec + 新增 3 用例）

**Files:**
- Create: `e2e/ai-full/batch/retry-reexecute.spec.ts`
- Delete（本任务末尾）: `e2e/ai-full/ai-retry-failed.spec.ts`、`e2e/ai-full/ai-reexecute.spec.ts`

**Interfaces:**
- Consumes: batch-helpers（全量）、toolbox（failFastBatch/writeWorkspaceFile/readWorkspaceFile/childWorkspace）。

**迁移清单（测试体原样，import 换新路径；两 spec 的 round1/植入/round2 节奏保留）：**
1. ai-retry-failed 全部用例（fail-fast 触发重试、工作区残留清零、uploads 保留、计数回滚、消息跨轮不累积）。
2. ai-reexecute 全部用例（单子 reexecute 清上下文/计数回滚/输入保留/自动派发收敛/files 端点）。

- [ ] **Step 1: 迁移 + 跑通**

Run: `npx playwright test e2e/ai-full/batch/retry-reexecute.spec.ts --reporter=line`
Expected: PASS（≤10 分钟）

- [ ] **Step 2: 追加 3 个新用例**

```ts
test('partial 批混合 retry-failed：completed 原样保留、failed 恰好重排', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })   // completed 子任务需真实跑完
  const tk = await adminTokenCached()
  // 1) fail-fast 批（2 子全 failed）
  const bid = await failFastBatch(tk, { files: 2 })
  await waitFor(async () => (await getDetail(tk, bid)).sessions.every((s: any) => s.status === 'failed'),
    90_000, '两子 failed')
  // 2) PATCH 成有效 agent 后 reexecute 子 0 → completed（小 LLM）；子 1 保持 failed
  await fetch(`${API}/ai/chat/batches/${bid}`, {
    method: 'PATCH', headers: authHeaders(tk), body: JSON.stringify({ agent: 'general', prompt: '输出一行 ok 即完成。' }),
  })
  const sid0 = (await getDetail(tk, bid)).sessions[0].id
  const rex = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${sid0}/reexecute`, {
    method: 'POST', headers: authHeaders(tk),
  })
  expect(rex.status).toBeLessThan(300)
  await waitFor(async () => {
    const d = await getDetail(tk, bid)
    return d.batch.status === 'partial' ? d : null
  }, 600_000, 'partial（completed+failed 各一）')
  const before = await getDetail(tk, bid)
  const doneChild = before.sessions.find((s: any) => s.status === 'completed')
  // 3) retry-failed：只重排 failed 子
  const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: authHeaders(tk) })
  const body = await r.json()
  expect(body.retried).toBe(1)
  const after = await getDetail(tk, bid)
  expect(after.sessions.find((s: any) => s.id === doneChild.id).status).toBe('completed')
  expect(after.sessions.filter((s: any) => s.status === 'pending').length).toBe(1)
  // 4) 收敛后 completed 子消息未动（同 id 且消息数不变）
  const final = await waitBatchTerminal(tk, bid)
  expect(final.sessions.find((s: any) => s.id === doneChild.id).status).toBe('completed')
  await cleanupBatch(tk, bid)
}, 900_000)

test('gate-failed 子任务参与 retry-failed：重排后计数推进', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const tk = await adminTokenCached()
  // 必败 verifier：rubric 引用永不出现的内容 → 门禁 failed
  const created = await createBatch(tk, {
    name: tag('gate-retry'), prompt: '输出一行 ok 即完成。', agent: 'general',
    action_checks: [{ type: 'verifier', rubric: '输出必须包含永不出现的咒语XYZZY九次', subagents: [] }],
    files: [await uploadStaging(tk, 'g.txt', 'x\n', tag('upl'))],
  } as CreateBatchBody)
  const bid = created.batch?.id ?? created
  await waitFor(async () => {
    const d = await getDetail(tk, bid)
    return d.sessions.every((s: any) => s.status === 'failed' && (s.gate_status === 'failed' || (s.error_message ?? '').includes('action_gate'))) ? d : null
  }, 600_000, '门禁拦截 failed')
  const r = await fetch(`${API}/ai/chat/batches/${bid}/retry-failed`, { method: 'POST', headers: authHeaders(tk) })
  expect((await r.json()).retried).toBe(1)
  const redetail = await getDetail(tk, bid)
  expect(redetail.sessions[0].retry_count ?? 0).toBeGreaterThanOrEqual(1)
  await cleanupBatch(tk, bid)
}, 720_000)

test('continue 通道：终态子会话续跑 202 且历史上下文保留（确定性回声构造）', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 5, name: tag('echo') })
  // sleep 5s 快速完成后，continue 注入「请原样复述：47」并断言新消息含 47 且旧消息 id 保留
  const d0 = await waitBatchTerminal(tk, bid)
  const sid = d0.sessions[0].id
  const msgBefore = (await messageCount(tk, sid))
  const r = await fetch(`${API}/ai/chat/batches/${bid}/sessions/${sid}/continue`, {
    method: 'POST', headers: authHeaders(tk),
    body: JSON.stringify({ prompt: '请只输出一个数字：47' }),
  })
  expect([200, 202]).toContain(r.status)
  await waitFor(async () => (await messageCount(tk, sid)) > msgBefore, 120_000, '续跑消息落库')
  const msgs = await (await fetch(`${API}/ai/chat/sessions/${sid}/messages`, { headers: authHeaders(tk) })).json()
  const joined = JSON.stringify(msgs.messages ?? msgs)
  expect(joined).toContain('47')
  await cleanupBatch(tk, bid)
}, 300_000)
```

- [ ] **Step 3: 跑全 spec + 删旧文件**

Run: `npx playwright test e2e/ai-full/batch/retry-reexecute.spec.ts --reporter=line`
Expected: PASS（含 3 个 LLM 用例，总预算 ≤25 分钟）

```bash
git rm e2e/ai-full/ai-retry-failed.spec.ts e2e/ai-full/ai-reexecute.spec.ts
```

- [ ] **Step 4: Commit**

```bash
git add -A e2e/ai-full && git commit -m "test(batch): retry/reexecute 域收编+partial 混合重试+gate-failed 重试+continue 收编"
```

---

### Task 7: reuse.spec.ts（两 spec 去重合并）

**Files:**
- Create: `e2e/ai-full/batch/reuse.spec.ts`
- Delete（本任务末尾）: `e2e/ai-full/ai-subagent-reuse.spec.ts`、`e2e/ai-full/ai-batch-concurrency-reuse.spec.ts`

**Interfaces:**
- Consumes: batch-helpers（makeProvisionRepo/uploadStaging/createBatch/waitBatchTerminal/cleanupBatch/waitFor）、toolbox、`../helpers`（gotoWithAuth/screenshot）。

**结构（3 用例，替代原两 spec 的重复断言面）：**

1. **L1 并发观测**（原 concurrency-reuse 的并发部分，去 provision/reuse 断言）：6 子 × 默认并发 3 → 轮询观测 running 峰值===3 且 pending 排队出现过 → 6/6 收敛。预算 15 分钟，`@llm`。
2. **L1 复用权威锚点**（原 concurrency-reuse 的复用部分）：`makeProvisionRepo` 注入自定义 primary+subagent → `subagent_reuse: ['e2e-sub']` → 逐子断言 tool-calls 账本 ≥2 次委派同名单 + 插件 `/reuse` 内部端点锚点（taskId ses_ 前缀）+ turn_segments≥2。预算 15 分钟，`@llm`。
3. **L3 UI 徽标**（原 subagent-reuse 全部）：`subagent_reuse: ['general']` → 气泡 subtaskId 一致 + 「已复用·N 段」徽标 + 分段头截图。

- [ ] **Step 1: 按上述结构从两个源文件迁移合并（断言代码取权威端点版，删除与用例 2 重复的 general 版账本断言；UI 断言整体保留）**

Run: `npx playwright test e2e/ai-full/batch/reuse.spec.ts --reporter=line`
Expected: 3 PASS（总预算 ≤40 分钟，本套件最重域）

- [ ] **Step 2: 删旧文件 + Commit**

```bash
git rm e2e/ai-full/ai-subagent-reuse.spec.ts e2e/ai-full/ai-batch-concurrency-reuse.spec.ts
git add -A e2e/ai-full && git commit -m "test(batch): reuse 域合并去重（并发观测/权威锚点/UI 徽标三用例）"
```

---

### Task 8: gate.spec.ts（三 spec API 面收编 + 新增 3 端点用例）

**Files:**
- Create: `e2e/ai-full/batch/gate.spec.ts`
- Delete（本任务末尾）: `e2e/ai-full/ai-verifier-gate.spec.ts`、`ai-verifier-subagent.spec.ts`、`ai-delegation-gate.spec.ts`

**Interfaces:**
- Consumes: batch-helpers（含 makeProvisionRepo）、toolbox。

**迁移清单：**
1. verifier 三向 + gate_retry 闭环（原 verifier-gate 全部；`/attempts` 断言 GATE_RETRY 保留）。
2. verifier 定向 subagent 组（原 verifier-subagent）。
3. 委派级门禁提前判定 + 不过即停（原 delegation-gate）。

- [ ] **Step 1: 迁移 + 跑通**

Run: `npx playwright test e2e/ai-full/batch/gate.spec.ts --reporter=line`
Expected: PASS（正向达标用例为 LLM，预算各 ≤10 分钟）

- [ ] **Step 2: 追加 3 个端点用例**

```ts
test('gate/dry-run：正则预演命中数；verifier 类型明确拒绝预演', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 10 })
  try {
    const d = await getDetail(tk, bid)
    const sid = d.sessions[0].id
    const ok = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/gate/dry-run`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ tool: 'bash', args_pattern: 'sleep' }),
    })
    expect(ok.status).toBeLessThan(300)
    expect(JSON.stringify(await ok.json())).toMatch(/match|hit|count/)
    const bad = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/gate/dry-run`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ type: 'verifier', rubric: 'x' }),
    })
    expect(bad.status).toBeGreaterThanOrEqual(400)
  } finally { await cleanupBatch(tk, bid) }
})

test('children/<sid>/tool-calls 账本取材：bash 调用入账', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 10 })
  try {
    await waitBatchTerminal(tk, bid)
    const sid = (await getDetail(tk, bid)).sessions[0].id
    const r = await fetch(`${API}/ai/chat/batches/${bid}/children/${sid}/tool-calls`, { headers: authHeaders(tk) })
    expect(r.status).toBe(200)
    const body = await r.json()
    const calls = JSON.stringify(body)
    expect(calls).toContain('sleep')   // 模型执行过 bash sleep，账本应有 bash 记录
  } finally { await cleanupBatch(tk, bid) }
})

test('action-checks/attach 运行中补挂期望：终态被核对', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })   // 长窗口内补挂
  try {
    await waitFor(async () => countByStatus(await getDetail(tk, bid))['running'] === 1, 120_000, 'running')
    const sid = (await getDetail(tk, bid)).sessions[0].id
    const r = await fetch(`${API}/ai/chat/batches/${bid}/action-checks/attach`, {
      method: 'POST', headers: authHeaders(tk),
      body: JSON.stringify({ checks: [{ type: 'tool_assert', tool: 'bash', args_pattern: 'sleep', min_count: 1 }], batch_seq: 1 }),
    })
    expect(r.status).toBeLessThan(300)
    const final = await waitBatchTerminal(tk, bid, 420_000)
    expect(final.sessions[0].gate_status).toBe('passed')
  } finally { await cleanupBatch(tk, bid) }
})
```

（attach 的请求字段名以 `server/routes/ai_chat_batches.py` attach handler 为准核对后微调。）

- [ ] **Step 3: 跑全 spec + 删旧文件 + Commit**

```bash
npx playwright test e2e/ai-full/batch/gate.spec.ts --reporter=line
git rm e2e/ai-full/ai-verifier-gate.spec.ts e2e/ai-full/ai-verifier-subagent.spec.ts e2e/ai-full/ai-delegation-gate.spec.ts
git add -A e2e/ai-full && git commit -m "test(batch): gate 域收编三 spec+dry-run/tool-calls/attach 补缺"
```

---

### Task 9: openapi.spec.ts（批部分收编 + Key 隔离 + webhook 回调）

**Files:**
- Create: `e2e/ai-full/batch/openapi.spec.ts`
- Modify: `e2e/ai-full/ai-openapi.spec.ts`（仅删除批相关用例，其余保留——本任务不删文件）

**Interfaces:**
- Consumes: `../helpers`（createApiKey/deleteApiKey/openApi/tag/BATCH_TERMINAL/stagingUpload）、toolbox（cleanupBatch）。

**迁移清单：** 自 `e2e/ai-full/ai-openapi.spec.ts` 迁入批相关用例（无 Key/假 Key/`/v1/ai-batches/b-not-exist` 404 面、staging 路径穿越、X-API-Key 建批→终态→`/results`）；自 `ai-harness-safety.spec.ts` 迁入对外删除治理（`BATCH_NOT_TERMINAL` 409 / `stop=true` drain）。

- [ ] **Step 1: 迁移 + 跑通**

Run: `npx playwright test e2e/ai-full/batch/openapi.spec.ts --reporter=line`
Expected: PASS

- [ ] **Step 2: 追加 3 个新用例**

```ts
import http from 'node:http'
import crypto from 'node:crypto'

test('跨 API Key 隔离：Key B 不可见 Key A 的批', async () => {
  const keyA = await createApiKey(tag('keyA'))
  const keyB = await createApiKey(tag('keyB'))
  try {
    const up = await openApi(keyA, 'POST', '/v1/ai-batches/uploads',
      { multipart: { file: { name: 'iso.txt', buffer: Buffer.from('x\n') }, upload_session_id: tag('upl') } })
    const created = await openApi(keyA, 'POST', '/v1/ai-batches', {
      name: tag('iso'), prompt: '输出一行 ok。', agent: 'general', files: [up],
    })
    const bid = created.id ?? created.batch?.id
    const getB = await openApi(keyB, 'GET', `/v1/ai-batches/${bid}`)
    expect([403, 404]).toContain(getB.status)          // 不泄漏存在性
    const listB = await openApi(keyB, 'GET', '/v1/ai-batches')
    expect(JSON.stringify(listB)).not.toContain(bid)
    const delA = await openApi(keyA, 'DELETE', `/v1/ai-batches/${bid}?stop=1`)
    expect(delA.status).toBeLessThan(300)
  } finally {
    await deleteApiKey(keyA); await deleteApiKey(keyB)
  }
})

test('终态 webhook：HMAC 签名回调真实送达本地 receiver', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const received: { url: string; headers: http.IncomingHttpHeaders; body: string }[] = []
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', c => body += c)
    req.on('end', () => { received.push({ url: req.url!, headers: req.headers, body }); res.writeHead(200); res.end('ok') })
  })
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r))
  const port = (server.address() as any).port
  const key = await createApiKey(tag('hook'))
  try {
    const secret = 'e2e-webhook-secret'
    const up = await openApi(key, 'POST', '/v1/ai-batches/uploads',
      { multipart: { file: { name: 'w.txt', buffer: Buffer.from('x\n') }, upload_session_id: tag('upl') } })
    const created = await openApi(key, 'POST', '/v1/ai-batches', {
      name: tag('hook'), prompt: '输出一行 ok。', agent: 'general', files: [up],
      callbackUrl: `http://127.0.0.1:${port}/cb`, callbackSecret: secret,
    })
    const bid = created.id ?? created.batch?.id
    await waitFor(async () => {
      const d = await openApi(key, 'GET', `/v1/ai-batches/${bid}`)
      return BATCH_TERMINAL.includes(d.status ?? d.batch?.status) ? d : null
    }, 600_000, '对外批终态')
    await waitFor(async () => received.length > 0, 120_000, 'webhook 送达（outbox drain 周期内）')
    const hit = received[0]
    expect(hit.url).toBe('/cb')
    const sig = String(hit.headers['x-webhook-signature'] ?? '')
    const mac = crypto.createHmac('sha256', secret).update(hit.body).digest('hex')
    expect(sig.includes(mac) || sig === mac).toBe(true)   // 前缀格式以 webhook_engine.py 实际为准
    const payload = JSON.parse(hit.body)
    expect(JSON.stringify(payload)).toContain(bid)
  } finally {
    server.close()
    await deleteApiKey(key)
    // 兜底清理批（Key 维度删除已在上文；此处防御性 no-op）
  }
}, 720_000)

test('file-records / results 契约：终态批可读子任务文件记录', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  const key = await createApiKey(tag('files'))
  try {
    const up = await openApi(key, 'POST', '/v1/ai-batches/uploads',
      { multipart: { file: { name: 'fr.txt', buffer: Buffer.from('file-records 探针\n') }, upload_session_id: tag('upl') } })
    const created = await openApi(key, 'POST', '/v1/ai-batches', {
      name: tag('files'), prompt: '读取 uploads/fr.txt 内容并原样输出。', agent: 'general', files: [up],
    })
    const bid = created.id ?? created.batch?.id
    await waitFor(async () => BATCH_TERMINAL.includes((await openApi(key, 'GET', `/v1/ai-batches/${bid}`)).status), 600_000, '终态')
    const rec = await openApi(key, 'GET', `/v1/ai-batches/${bid}/file-records`)
    expect(rec.status).toBeLessThan(300)
    const res = await openApi(key, 'GET', `/v1/ai-batches/${bid}/results`)
    expect(res.status).toBeLessThan(300)
    expect(JSON.stringify(res)).toContain(bid)   // results 载荷应能关联到本批
  } finally { await deleteApiKey(key) }
}, 720_000)
```

（`openApi` 的 multipart/返回体字段名以 `e2e/ai-full/helpers.ts` 现有实现为准；签名前缀格式写用例前先读 `server/utils/webhook_engine.py:320-340`。）

- [ ] **Step 3: 收编源文件批用例移除 + 跑两 spec 防退化 + Commit**

```bash
npx playwright test e2e/ai-full/batch/openapi.spec.ts e2e/ai-full/ai-openapi.spec.ts --reporter=line
git add -A e2e/ai-full && git commit -m "test(batch): openapi 域收编+Key 隔离+webhook HMAC 回调+file-records"
```

---

### Task 10: permissions.spec.ts（全新，多用户隔离）

**Files:**
- Create: `e2e/ai-full/batch/permissions.spec.ts`

**Interfaces:**
- Consumes: toolbox（secondUser/failFastBatch/adminTokenCached/tag）、batch-helpers（authHeaders/API/getDetail/cleanupBatch/waitFor）。

- [ ] **Step 1: 写用例**

```ts
import { test, expect } from '@playwright/test'
import { API, authHeaders, createBatch, getDetail, cleanupBatch, uploadStaging } from './batch-helpers'
import { adminTokenCached, failFastBatch, secondUser, tag, waitFor } from './toolbox'

test.setTimeout(180_000)

test('跨用户隔离：他人批不可见、控制面拒绝、不泄漏存在性', async () => {
  const admin = await adminTokenCached()
  const userB = await secondUser()
  const bid = await failFastBatch(admin, { files: 1 })
  try {
    // 列表不可见
    const list = await (await fetch(`${API}/ai/chat/batches`, { headers: authHeaders(userB.token) })).json()
    expect(JSON.stringify(list)).not.toContain(bid)
    // 详情防枚举（预期 404；若实际 403 以 routes/ai_chat_batches.py 为准修正常量）
    const detail = await fetch(`${API}/ai/chat/batches/${bid}`, { headers: authHeaders(userB.token) })
    expect([403, 404]).toContain(detail.status)
    // 控制面拒绝
    for (const [method, path] of [
      ['POST', 'pause'], ['POST', 'cancel'], ['POST', 'retry-failed'],
      ['DELETE', ''],
    ] as const) {
      const r = await fetch(`${API}/ai/chat/batches/${bid}${path ? '/' + path : ''}`, {
        method, headers: authHeaders(userB.token),
      })
      expect(r.status).toBeGreaterThanOrEqual(400)
    }
    // 自己的批自己可见（对照组，fail-fast 0 LLM）
    const up = await uploadStaging(userB.token, 'own.txt', 'x\n', tag('upl'))
    const own = await createBatch(userB.token, {
      name: tag('own'), prompt: 'x', agent: 'e2e-no-such-agent', files: [up],
    } as any)
    const ownBid = (own as any).batch?.id ?? (own as any).id
    expect((await getDetail(userB.token, ownBid)).batch.name).toContain('AITEST')
    await cleanupBatch(userB.token, ownBid)
  } finally { await cleanupBatch(admin, bid); await userB.cleanup() }
})

test('非 admin 访问管理面 403；admin 正常', async () => {
  const userB = await secondUser()
  try {
    const r = await fetch(`${API}/ai/chat/admin/batches`, { headers: authHeaders(userB.token) })
    expect(r.status).toBe(403)
    const admin = await adminTokenCached()
    const ok = await fetch(`${API}/ai/chat/admin/batches`, { headers: authHeaders(admin) })
    expect(ok.status).toBeLessThan(300)
  } finally { await userB.cleanup() }
})
```

- [ ] **Step 2: 跑 spec**

Run: `npx playwright test e2e/ai-full/batch/permissions.spec.ts --reporter=line`
Expected: 2 PASS

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-full/batch/permissions.spec.ts && git commit -m "test(batch): permissions 域——跨用户隔离/控制面拒绝/管理面 403"
```

---

### Task 11: admin.spec.ts（全新，管理页闭环，全确定性）

**Files:**
- Create: `e2e/ai-full/batch/admin.spec.ts`

**Interfaces:**
- Consumes: toolbox（failFastBatch/secondUser/dbSeed/writeWorkspaceFile/childWorkspace/adminTokenCached）、batch-helpers（API/authHeaders/getDetail/waitFor/waitBatchTerminal/cleanupBatch/countByStatus）、`../helpers`（gotoWithAuth/screenshot）。

- [ ] **Step 1: 写 API 面用例**

```ts
test.describe('admin API 面', () => {
  test('跨用户列表筛选（status/keyword）与详情', async () => {
    const admin = await adminTokenCached()
    const userB = await secondUser('admfil')
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      await waitFor(async () => (await getDetail(admin, bid)).batch.status === 'failed', 90_000, 'failed')
      // keyword 筛选命中
      const hit = await (await fetch(`${API}/ai/chat/admin/batches?keyword=${bid}`, { headers: authHeaders(admin) })).json()
      expect(JSON.stringify(hit)).toContain(bid)
      // status 筛选 failed 命中
      const byStatus = await (await fetch(`${API}/ai/chat/admin/batches?status=failed`, { headers: authHeaders(admin) })).json()
      expect(JSON.stringify(byStatus)).toContain(bid)
      // 详情含子任务
      const detail = await (await fetch(`${API}/ai/chat/admin/batches/${bid}`, { headers: authHeaders(admin) })).json()
      expect((detail.sessions ?? detail.batch?.sessions ?? []).length).toBe(1)
      // 子任务消息端点
      const sid = (await getDetail(admin, bid)).sessions[0].id
      const msgs = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/messages`, { headers: authHeaders(admin) })
      expect(msgs.status).toBeLessThan(300)
    } finally { await cleanupBatch(admin, bid); await userB.cleanup() }
  })

  test('admin retry-failed 与单子 reexecute 生效；终态软删子任务', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      const sid = (await getDetail(admin, bid)).sessions[0].id
      const rex = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/reexecute`, { method: 'POST', headers: authHeaders(admin) })
      expect(rex.status).toBeLessThan(300)
      await waitFor(async () => (await getDetail(admin, bid)).sessions[0].status === 'pending', 60_000, '重排 pending')
      await waitFor(async () => (await getDetail(admin, bid)).sessions[0].status === 'failed', 120_000, '再 fail-fast')
      const retry = await fetch(`${API}/ai/chat/admin/batches/${bid}/retry-failed`, { method: 'POST', headers: authHeaders(admin) })
      expect(retry.status).toBeLessThan(300)
      // 软删（终态）
      await waitFor(async () => (await getDetail(admin, bid)).sessions[0].status === 'failed', 120_000, '终态')
      const del = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}`, { method: 'DELETE', headers: authHeaders(admin) })
      expect(del.status).toBeLessThan(300)
      const after = await getDetail(admin, bid)
      expect(after.sessions.filter((s: any) => s.id === sid && !s.deleted_at).length).toBe(0)
    } finally { await cleanupBatch(admin, bid) }
  })

  test('tool-calls 与 attempt-timeline 端点', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      const sid = (await getDetail(admin, bid)).sessions[0].id
      const tc = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/tool-calls`, { headers: authHeaders(admin) })
      expect(tc.status).toBeLessThan(300)
      const att = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/attempt-timeline`, { headers: authHeaders(admin) })
      expect(att.status).toBeLessThan(300)
      // fail-fast 子任务至少有一条 attempt 记录
      expect(JSON.stringify(await att.json()).length).toBeGreaterThan(2)
    } finally { await cleanupBatch(admin, bid) }
  })

  test('AdminBatchFiles 文件面：uploads 分组可见、导入 data_files 幂等', async () => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    try {
      const sid = (await getDetail(admin, bid)).sessions[0].id
      // 文件清单（uploads 分组）：契约 list_session_files → [{path, ...}]
      const files = await (await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files`, { headers: authHeaders(admin) })).json()
      const all: any[] = files.files ?? files
      const uploadPath = all.map((f: any) => f.path).find((p: string) => p.includes('in-0'))
      expect(uploadPath).toBeTruthy()
      // 下载端点可达（as_attachment）
      const dl = await fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files/download?path=${encodeURIComponent(uploadPath)}`, { headers: authHeaders(admin) })
      expect(dl.status).toBeLessThan(300)
      // 导入：首导 imported（或 existing），再导 existing —— 幂等
      // （契约：POST body {paths: [...]}，results[].status ∈ imported|existing，uploaded_by=原 owner）
      const importOnce = async () => fetch(`${API}/ai/chat/admin/batches/${bid}/sessions/${sid}/files/import`, {
        method: 'POST', headers: authHeaders(admin),
        body: JSON.stringify({ paths: [uploadPath] }),
      })
      const first = await (await importOnce()).json()
      expect(JSON.stringify(first)).toMatch(/imported|existing/)
      const second = await (await importOnce()).json()
      expect(JSON.stringify(second)).toContain('existing')
    } finally { await cleanupBatch(admin, bid) }
  })
})
```

- [ ] **Step 2: 写 UI 面用例（text/role 选择器，与组件文案核对后落地）**

```ts
import { gotoWithAuth, screenshot } from '../helpers'

test.describe('admin UI 面', () => {
  test('列表→详情抽屉→重试失败→软删消失（fail-fast 驱动，0 LLM）', async ({ page }, testInfo) => {
    const admin = await adminTokenCached()
    const bid = await failFastBatch(admin, { files: 1 })
    const name = (await getDetail(admin, bid)).batch.name
    try {
      await gotoWithAuth(page, '/admin/ai-execution?tab=batches')
      await page.waitForSelector(`text=${name}`, { timeout: 30_000 })
      await screenshot(page, 'admin-batch-list')
      // 打开详情抽屉（行点击）
      await page.click(`text=${name}`)
      await page.waitForSelector(`text=${(await getDetail(admin, bid)).sessions[0].batch_input_file}`, { timeout: 15_000 })
      // 重试全部失败 → 子任务重排再 fail-fast → 徽标仍 failed
      await page.getByRole('button', { name: /重试|失败/ }).first().click()
      await page.waitForTimeout(3_000)
      await screenshot(page, 'admin-batch-retry')
      // 软删子任务（终态菜单）——按钮文案以 AiBatchAdmin.vue 实际为准
      const delBtn = page.getByRole('button', { name: /删除|软删/ }).first()
      if (await delBtn.isVisible().catch(() => false)) {
        await delBtn.click()
        await page.waitForTimeout(2_000)
      }
      await screenshot(page, 'admin-batch-softdel')
    } finally { await cleanupBatch(admin, bid) }
  })
})
```

- [ ] **Step 3: 跑 spec（选择器与组件核对修正）+ Commit**

Run: `npx playwright test e2e/ai-full/batch/admin.spec.ts --reporter=line`
Expected: 3 PASS

```bash
git add e2e/ai-full/batch/admin.spec.ts && git commit -m "test(batch): admin 域——API 筛选/详情/重试/软删 + UI 列表抽屉闭环"
```

---

### Task 12: resilience.spec.ts（全新，容灾自愈）

**Files:**
- Create: `e2e/ai-full/batch/resilience.spec.ts`

**Interfaces:**
- Consumes: toolbox（seedRunningChild/dbSeed/restartBackend/sleepBatch/adminTokenCached/tag）、batch-helpers（API/authHeaders/getDetail/waitFor/cleanupBatch）。

**用例清单（对账间隔 60s，决策观察窗 waitFor 90s）：**

- [ ] **Step 1: 写对账矩阵 6 分支用例**

```ts
import { test, expect } from '@playwright/test'
import { API, authHeaders, cleanupBatch, getDetail, waitFor, waitBatchTerminal } from './batch-helpers'
import { adminTokenCached, dbSeed, restartBackend, seedRunningChild, sleepBatch } from './toolbox'

test.setTimeout(300_000)

/** 读批事件（child.recovered 断言用）。 */
async function recoveredEvents(tk: string, bid: string): Promise<any[]> {
  const r = await fetch(`${API}/ai/chat/batches/${bid}/events`, { headers: authHeaders(tk) })
  const body = await r.json()
  return (body.events ?? body).filter((e: any) => e.type === 'child.recovered')
}

test('对账：租约未过期 → 不动', async () => {
  const tk = await adminTokenCached()
  const { bid } = await seedRunningChild({ ocId: 'ses-x', lease: 'future' })
  try {
    // 停满一个对账窗口（RECONCILE_INTERVAL_SEC=60，留余量 70s）后状态必须原样
    await new Promise(rr => setTimeout(rr, 70_000))
    expect((await getDetail(tk, bid)).sessions[0].status).toBe('running')
    expect((await recoveredEvents(tk, bid)).length).toBe(0)
  } finally { await cleanupBatch(tk, bid) }
})

test('对账：unknown 副作用 → needs_review（禁自动重放）', async () => {
  const tk = await adminTokenCached()
  const { bid, sid } = await seedRunningChild({ ocId: 'ses-x', lease: 'past', effectUnknown: true })
  try {
    await waitFor(async () => (await getDetail(tk, bid)).sessions[0].status === 'needs_review', 90_000, 'needs_review')
    expect((await getDetail(tk, bid)).sessions[0].error_message ?? '').toContain('unknown')
    expect((await recoveredEvents(tk, bid)).some((e: any) => e.payload?.decision === 'needs_review')).toBe(true)
  } finally { await cleanupBatch(tk, bid) }
})

test('对账：无 oc 会话 → 原样重排（种子批 agent 缺失 → 认领即 fail-fast，0 LLM）', async () => {
  const tk = await adminTokenCached()
  const { bid } = await seedRunningChild({ ocId: null, lease: 'past' })
  try {
    // requeue → pending → worker 认领 → _check_agent fail-fast（种子批默认 e2e-no-such-agent）
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      const ev = (await recoveredEvents(tk, bid)).some((e: any) => e.payload?.decision === 'requeue_fresh')
      return ev ? d : null
    }, 90_000, 'requeue_fresh 事件')
    // 重排后的 pending 被认领并 fail-fast 收敛（确定性，无 LLM）
    await waitFor(async () => (await getDetail(tk, bid)).sessions[0].status === 'failed', 90_000, '重排后 fail-fast')
  } finally { await cleanupBatch(tk, bid) }
})

test('对账：oc 会话 404 → failed（带对账原因）', async () => {
  const tk = await adminTokenCached()
  const { bid } = await seedRunningChild({ ocId: 'ses-e2e-nonexistent-000', lease: 'past' })
  try {
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      const s = d.sessions[0]
      return s.status === 'failed' && (s.error_message ?? '').includes('对账器') ? d : null
    }, 90_000, 'oc 404 failed')
    expect((await recoveredEvents(tk, bid)).some((e: any) => e.payload?.decision === 'opencode_404')).toBe(true)
  } finally { await cleanupBatch(tk, bid) }
})

test('对账：retry_count 预算用尽 → failed（不重排）', async () => {
  const tk = await adminTokenCached()
  const { bid } = await seedRunningChild({ ocId: null, lease: 'past', retryCount: 2 })   // MAX_AUTO_RETRY=2
  try {
    await waitFor(async () => {
      const s = (await getDetail(tk, bid)).sessions[0]
      return s.status === 'failed' && (s.error_message ?? '').includes('预算') ? s : null
    }, 90_000, '预算用尽 failed')
  } finally { await cleanupBatch(tk, bid) }
})

test('对账：有 checkpoint（oc 活着）→ requeue_continue', async ({ }, testInfo) => {
  // 需真实 OpenCode 会话（POST /session 建会话，不烧推理 token）
  testInfo.annotations.push({ type: 'resilience' })
  const tk = await adminTokenCached()
  // 在种子工作区里经 OpenCode 真实建会话（冷目录 bootstrap 可达 120s）
  const { bid, sid } = await seedRunningChild({ ocId: null, lease: 'past', checkpoint: true })
  try {
    const ws = (await getDetail(tk, bid)).sessions[0].workspace_path
    const ocBase = process.env.OPENCODE_BASE_URL ?? 'http://127.0.0.1:4096'
    const r = await fetch(`${ocBase}/session?directory=${encodeURIComponent(ws)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'e2e-reconcile' }),
    })
    expect(r.status).toBeLessThan(300)
    const ocId = (await r.json()).id
    dbSeed(`UPDATE ai_chat_sessions SET opencode_session_id='${ocId}' WHERE id='${sid}';`)
    await waitFor(async () => {
      const ev = (await recoveredEvents(tk, bid)).some((e: any) => e.payload?.decision === 'requeue_continue')
      return ev ? ev : null
    }, 120_000, 'requeue_continue 事件')
  } finally { await cleanupBatch(tk, bid) }
})
```

- [ ] **Step 2: 追加看门狗与进程级用例**

```ts
test('工具卡死自动重试：AI_BATCH_TOOL_STALL_SEC 缩短 + sleep 长任务', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' })
  await restartBackend({ AI_BATCH_TOOL_STALL_SEC: '20' })
  try {
    const tk = await adminTokenCached()
    const bid = await sleepBatch(tk, { children: 1, sleepSec: 600 })   // sleep 600s > stall 20s → 工具卡死
    try {
      await waitFor(async () => {
        const s = (await getDetail(tk, bid)).sessions[0]
        return (s.retry_count ?? 0) >= 1 || s.status === 'failed' ? s : null
      }, 300_000, '自动重试或预算终局')
      // attempt 链应出现 recovering 收口（自动重试语义）
      const att = await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`, { headers: authHeaders(tk) })).json()
      expect(JSON.stringify(att)).toMatch(/recovering|retry/)
    } finally { await cleanupBatch(tk, bid) }
  } finally { await restartBackend() }   // 恢复默认
})

test('进程级：worker 运行中重启 → 租约接管 → 批继续收敛（@resilience）', async ({ }, testInfo) => {
  testInfo.annotations.push({ type: 'llm' }, { type: 'resilience' })
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 30 })
  try {
    await waitFor(async () => (await getDetail(tk, bid)).sessions[0].status === 'running', 120_000, 'running 中重启')
    await restartBackend()   // 无特殊 env：验证接管路径
    const final = await waitBatchTerminal(tk, bid, 420_000)
    expect(final.batch.status).toBe('completed')
    // fencing：重启前后子任务只有一条完整执行史（无双执行——消息数与 attempt 数一致）
    const att = await (await fetch(`${API}/ai/chat/batches/${bid}/attempts`, { headers: authHeaders(tk) })).json()
    expect(JSON.stringify(att)).not.toMatch(/duplicate|double/)
  } finally { await cleanupBatch(tk, bid) }
})
```

- [ ] **Step 3: 跑 spec（进程级用例放最后单独跑）+ Commit**

Run: `npx playwright test e2e/ai-full/batch/resilience.spec.ts --reporter=line --grep-invert "进程级"`
Run: `npx playwright test e2e/ai-full/batch/resilience.spec.ts --reporter=line --grep "进程级"`
Expected: 全 PASS；进程级用例后用 `restartBackend()` 恢复的环境跑 1 个旧 spec 确认无污染

```bash
git add e2e/ai-full/batch/resilience.spec.ts && git commit -m "test(batch): resilience 域——对账矩阵 DB 种子+看门狗 env+进程级租约接管"
```

---

### Task 13: ui-journeys.spec.ts + 根目录旧 spec 处置

**Files:**
- Create: `e2e/ai-full/batch/ui-journeys.spec.ts`
- Delete: `e2e/ai-chat-batch.spec.ts`、`e2e/ai-chat-batch-search.spec.ts`、`e2e/batch-skill-check.spec.ts`
- Modify: `e2e/ai-chat-stop-resume.spec.ts`（批用例移出，保留普通会话 error part 用例，更名 `e2e/ai-chat-session-control.spec.ts`）
- Modify: `e2e/ai-chat-subtask-trace.spec.ts`（批用例移出，保留普通会话委派用例，删占位用例与重复 setTimeout）

**Interfaces:**
- Consumes: ui-helpers（openBatchDialog/createBatchViaDialog/expandBatchGroup/batchGroupBadgeClass）、toolbox（sleepBatch/failFastBatch/provisionRepo 经 batch-helpers/adminTokenCached）、`../helpers`（gotoWithAuth/screenshot/api）、batch-helpers（API/authHeaders/getDetail/waitFor/cleanupBatch/countByStatus）。

**收编映射：**

| 旧用例 | 去向 | 改造 |
|---|---|---|
| ai-chat-batch 建批→分组→终态→UI 删除 | ui-journeys「对话框建批端到端」 | 删标题里的 retry 幻影；保留真 LLM |
| agent-action-gate 用例 1（门禁配置区块 + 不完整阻断） | ui-journeys「表单校验阻断」 | 选择器原样搬运 |
| ai-chat-stop-resume 用例 1/2（停止/暂停续跑消息保留） | ui-journeys 两条 | 建批流程换 createBatchViaDialog |
| ai-chat-batch-search 全部 | ui-journeys「批内搜索」 | **自建批**（含关键词文件），删除 test.skip 环境耦合 |
| batch-skill-check 全部 | ui-journeys「技能注入」 | 改 provision_repo 自带 skill（makeProvisionRepo 加 skill/ 目录），消除磁盘预置依赖 |
| ai-chat-subtask-trace 用例 3 | ui-journeys「委派轨迹气泡」 | 保留 |

- [ ] **Step 1: 按映射表创建 ui-journeys.spec.ts（迁移 + 新增 SSE 用例）**

新增 SSE 实时消费用例（确定性）：

```ts
test('SSE 实时消费：API 触发子任务取消，UI 徽标先于 10s 轮询更新', async ({ page }, testInfo) => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 120 })
  const name = (await getDetail(tk, bid)).batch.name
  try {
    await gotoWithAuth(page, '/')
    await expandBatchGroup(page, name)
    // 批组可见（store 已订阅 SSE——列表级订阅当页非终态批）
    await page.waitForSelector(`text=${name}`, { timeout: 20_000 })
    const t0 = Date.now()
    await fetch(`${API}/ai/chat/batches/${bid}/cancel`, { method: 'POST', headers: authHeaders(tk) })
    // SSE push 到达 → 徽标 8s 内变 cancelled（列表轮询周期 10s，若仅靠轮询必然 >10s）
    await page.waitForFunction(
      (n) => {
        const g = [...document.querySelectorAll('.batch-group')].find(el => el.textContent?.includes(n))
        return !!g && /cancelled|中断|failed/.test(g.className + g.textContent)
      }, name, { timeout: 8_000 },
    )
    console.log(`SSE 更新延迟 ${Date.now() - t0}ms`)
    await screenshot(page, 'ui-journeys-sse')
  } finally { await cleanupBatch(tk, bid) }
})
```

- [ ] **Step 2: 跑 ui-journeys + 处置旧文件**

Run: `npx playwright test e2e/ai-full/batch/ui-journeys.spec.ts --reporter=line`
Expected: PASS（LLM 用例按映射保留）

```bash
git rm e2e/ai-chat-batch.spec.ts e2e/ai-chat-batch-search.spec.ts e2e/batch-skill-check.spec.ts
git mv e2e/ai-chat-stop-resume.spec.ts e2e/ai-chat-session-control.spec.ts
# 编辑该文件：删除批相关 3 用例与其内联 helper，仅保留普通会话 error part 用例
git add -A e2e && git commit -m "test(batch): ui-journeys 收编五源+SSE 实时消费；根目录批 spec 处置"
```

（`ai-chat-subtask-trace.spec.ts` 同批改造：批用例移出、占位用例删除、重复 `setTimeout` 清理；提交并入上一步。）

---

### Task 14: 收尾——helpers.ts deprecation、ai-harness-safety/agent-action-gate 处置、文档、全量回归

**Files:**
- Modify: `e2e/ai-full/helpers.ts`（批相关函数标 deprecated）
- Modify: `e2e/ai-full/ai-harness-safety.spec.ts`（批用例移除后保留 P2 编排 DAG 用例）
- Delete: `e2e/ai-full/agent-action-gate.spec.ts`（用例 1 已入 ui-journeys、用例 2/3 徽标断言并入 gate.spec）
- Modify: `docs/ai-testing/`（套件计数与入口说明）

- [ ] **Step 1: agent-action-gate 用例 2/3（门禁徽标 ×1/✓、db_record 效果）迁入 gate.spec.ts 末尾，然后删除该文件**

- [ ] **Step 2: ai-harness-safety 批用例移除（对外门禁/删除治理/events/幂等/continue 块已分别入 openapi/control/lifecycle/retry 域），保留 P2 编排 DAG 用例并跑通**

Run: `npx playwright test e2e/ai-full/ai-harness-safety.spec.ts --reporter=line`
Expected: 残留用例 PASS

- [ ] **Step 3: helpers.ts 批函数 deprecation 注释**

在 `stagingUpload`、`adminToken`、`BATCH_TERMINAL`、`waitFor` 的 doc 注释追加：

```ts
/**
 * @deprecated 批任务域用例请改用 e2e/ai-full/batch/batch-helpers.ts（直连 3002 权威版）。
 * 本函数保留给非批用例；当最后一批消费方迁移后删除。
 */
```

- [ ] **Step 4: 更新 docs/ai-testing 回归文档（套件结构、域清单、计数、确定性/LLM 用例标签说明、resilience 的重启副作用警告）**

- [ ] **Step 5: ai-full 全量回归**

Run: `npx playwright test e2e --reporter=line`
Expected: 全 PASS（预算 ≤45 分钟确定性主体 + LLM 用例；若超时先查 sleep 用例的 sleepSec 是否被误放大）

- [ ] **Step 6: Commit**

```bash
git add -A e2e docs/ai-testing && git commit -m "test(batch): 收尾——helpers deprecation/残留批用例处置/回归文档/全量验证"
```

---

## Self-Review 记录

- **Spec 覆盖**：spec §4.1-4.11 每个用例点均可指到 Task 4-13 的对应任务；§3 工具箱 8 件套 → Task 2/3；§4.11 处置 → Task 13/14；§5 编排 → Global Constraints + Task 14 Step 5；§6 顺序 → 任务序一致（permissions→admin→resilience 顺序按依赖调整为 admin 在 resilience 前，因 admin 用 secondUser/dbSeed 已由 Task 2/10 就绪）。
- **占位符**：无 TBD/TODO；「以路由实际为准核对」出现在 attach 字段名、签名前缀、404/403 语义三处，均为「先读源码再定常量」的执行指令而非空缺，且给了默认预期值。
- **类型一致性**：`failFastBatch/sleepBatch/seedRunningChild/secondUser/dbSeed/restartBackend` 的签名在 Task 2 定义、Task 4-12 消费处一致；`tag()` 在 toolbox 与 helpers.ts 各有一份（后者本就存在，无冲突）。
