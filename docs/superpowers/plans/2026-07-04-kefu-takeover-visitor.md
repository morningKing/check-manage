# 人工接管 子项目 B1：访客转人工 + 轮询 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 访客页加「转人工」入口，接管期间靠 3s 轮询接收人工回复，消费已上线的后端接口。

**Architecture:** `kefuPublic.ts` 加 `requestHuman`；`KefuChatPage.vue` 加 `humanMode` 状态 + 轮询 + 转人工按钮 + 提示条 + header 提示，并在 `send()` 收到 `humanTakeover:true` 时立即结束发送态。纯前端，不改后端/SSE。

**Tech Stack:** Vue 3 `<script setup>` + Element Plus + Vitest（`vi.useFakeTimers` 测轮询）。

## Global Constraints

- **纯前端**，不改后端、不拆 SSE（接管期它自然空转）。
- 「转人工」按钮在 **header 右侧**；轮询间隔 **3000ms**；**不做**访客侧「转回 AI / 结束人工」。
- 人工态提示文案用**独立 DOM 元素**（`v-if="humanMode"` 的 banner），**不**塞进 `messages`——`reload()` 轮询会整体覆盖 `messages`。
- `send()`：成功响应含 `humanTakeover:true`（或已 `humanMode`）→ 立即 `sending=false` + `enterHumanMode()`；否则行为**逐字不变**（仍靠 SSE `onIdle` 清 sending）。失败恢复逻辑不变。
- `onBeforeUnmount` 停轮询。`defineExpose` 增 `requestHuman` + `humanMode`（供测试与父组件）。
- 现有 `KefuChatPage.test.ts` 8 用例保持绿；`npm run build`（vue-tsc）无错误。测试从 repo 根 `npx vitest run <file>`。提交只 `git add` 该任务确切文件。

---

### Task 1: 访客转人工 + 轮询（`kefuPublic.ts` + `KefuChatPage.vue`）

**Files:**
- Modify: `src/api/kefuPublic.ts`（加 `requestHuman`；`sendKefuMessage` 返回补 `humanTakeover?`）
- Modify: `src/views/kefu/KefuChatPage.vue`（humanMode + 轮询 + 按钮 + 提示条 + header + send 调整 + expose）
- Modify: `src/views/kefu/__tests__/KefuChatPage.test.ts`（mock 加 `requestHuman`；加 3 个用例）

**Interfaces:**
- Produces: `requestHuman(sid: string): Promise<{ needsHuman: boolean }>`；`sendKefuMessage(...) => Promise<{ messageId: string; humanTakeover?: boolean }>`；`KefuChatPage` 暴露 `requestHuman()`/`humanMode`。

- [ ] **Step 1: 写失败测试**

在 `src/views/kefu/__tests__/KefuChatPage.test.ts`：先在 `vi.mock('@/api/kefuPublic', () => ({ ... }))` 对象里加一行 mock：

```typescript
  requestHuman: vi.fn().mockResolvedValue({ needsHuman: true }),
```

然后在 `describe` 末尾追加 3 个用例：

```typescript
  it('requestHuman enters humanMode and starts 3s polling', async () => {
    vi.useFakeTimers()
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()                       // onMounted: getKefuHistory called once
    vi.mocked(api.getKefuHistory).mockClear()
    await (w.vm as any).requestHuman()
    expect(api.requestHuman).toHaveBeenCalledWith('sess_1')
    expect((w.vm as any).humanMode).toBe(true)
    vi.advanceTimersByTime(3000)
    await flushPromises()
    expect(api.getKefuHistory).toHaveBeenCalled()  // polled once at 3s
    w.unmount()
    vi.useRealTimers()
  })

  it('send that returns humanTakeover clears sending immediately + humanMode', async () => {
    vi.mocked(api.sendKefuMessage).mockResolvedValueOnce({ messageId: 'm1', humanTakeover: true } as any)
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    ;(w.vm as any).draft = '我要人工'
    await (w.vm as any).send()
    expect((w.vm as any).sending).toBe(false)
    expect((w.vm as any).humanMode).toBe(true)
  })

  it('stops polling on unmount', async () => {
    vi.useFakeTimers()
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).requestHuman()
    vi.mocked(api.getKefuHistory).mockClear()
    w.unmount()
    vi.advanceTimersByTime(6000)
    await flushPromises()
    expect(api.getKefuHistory).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: FAIL（`api.requestHuman` 未在被测组件调用 / `humanMode` 未定义 / 未轮询）。

- [ ] **Step 3: `kefuPublic.ts` 加 API**

在 `src/api/kefuPublic.ts`：把 `sendKefuMessage` 的返回类型改为含 `humanTakeover`，并新增 `requestHuman`。找到：

```typescript
export function sendKefuMessage(sid: string, content: string, attachments: string[] = []) { return post<{ messageId: string }>(`/kefu/sessions/${sid}/messages`, { content, attachments }, vh()) }
```

替换为：

```typescript
export function sendKefuMessage(sid: string, content: string, attachments: string[] = []) { return post<{ messageId: string; humanTakeover?: boolean }>(`/kefu/sessions/${sid}/messages`, { content, attachments }, vh()) }
export function requestHuman(sid: string) { return post<{ needsHuman: boolean }>(`/kefu/sessions/${sid}/request-human`, {}, vh()) }
```

- [ ] **Step 4: `KefuChatPage.vue` 脚本改动**

在 `<script setup>`：

(a) 状态——在 `let closeStream: (() => void) | null = null` 之后加：

```typescript
const humanMode = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null
```

(b) 函数——在 `removePending` 之后、`send` 之前加：

```typescript
function startPolling() { if (!pollTimer) pollTimer = setInterval(() => { reload() }, 3000) }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }
function enterHumanMode() {
  if (humanMode.value) return
  humanMode.value = true
  sending.value = false
  startPolling()
}
async function requestHuman() {
  if (humanMode.value) return
  try { await api.requestHuman(sessionId.value); enterHumanMode() }
  catch { ElMessage.error('转人工失败，请稍后重试') }
}
```

(c) `send()`——把 `try { await api.sendKefuMessage(sessionId.value, text, atts) }` 改为捕获响应并处理接管：

```typescript
  try {
    const resp = await api.sendKefuMessage(sessionId.value, text, atts)
    if (resp?.humanTakeover || humanMode.value) { sending.value = false; enterHumanMode() }
  }
```

（`catch { ... }` 块保持不变。）

(d) 卸载——把 `onBeforeUnmount(() => { closeStream?.() })` 改为：

```typescript
onBeforeUnmount(() => { closeStream?.(); stopPolling() })
```

(e) `defineExpose`——在暴露对象里加 `requestHuman, humanMode`：

```typescript
defineExpose({ sessionId, onEscalate, messages, sending, askBubble, blocks, bubbles, pending, onPickFiles, send, draft, requestHuman, humanMode })
```

- [ ] **Step 5: `KefuChatPage.vue` 模板改动**

(a) header——把：

```html
          <header class="kefu-header">
            <div class="kefu-header__id">
              <div class="kefu-avatar" :style="{ background: headerAvatarColor }">
                <img v-if="config?.branding?.logo" :src="config.branding.logo" alt="" />
                <span v-else>{{ headerAvatarInitial }}</span>
              </div>
              <div class="kefu-header__meta">
                <span class="kefu-header__name">{{ config?.name || '在线客服' }}</span>
                <span class="kefu-header__status"><i class="dot" /> 在线</span>
              </div>
            </div>
            <el-button v-if="hasBlocks" class="svc-toggle" size="small" @click="drawer = true">🗂 自助服务</el-button>
          </header>
```

替换为：

```html
          <header class="kefu-header">
            <div class="kefu-header__id">
              <div class="kefu-avatar" :style="{ background: headerAvatarColor }">
                <img v-if="config?.branding?.logo" :src="config.branding.logo" alt="" />
                <span v-else>{{ headerAvatarInitial }}</span>
              </div>
              <div class="kefu-header__meta">
                <span class="kefu-header__name">{{ config?.name || '在线客服' }}</span>
                <span class="kefu-header__status"><i class="dot" /> {{ humanMode ? '人工客服接入中' : '在线' }}</span>
              </div>
            </div>
            <div class="kefu-header__actions">
              <el-button v-if="!humanMode" size="small" @click="requestHuman">🙋 转人工</el-button>
              <el-button v-if="hasBlocks" class="svc-toggle" size="small" @click="drawer = true">🗂 自助服务</el-button>
            </div>
          </header>
```

(b) 消息区提示条——把 `<main class="kefu-messages" ref="scroller">` 之后紧接着加一行 banner（在 `showWelcome` 那个 div 之前）：

```html
          <main class="kefu-messages" ref="scroller">
            <div v-if="humanMode" class="kefu-human-banner">已为你转接人工客服，请稍候…</div>
```

- [ ] **Step 6: `KefuChatPage.vue` 样式追加**

在 `<style scoped>` 内追加：

```css
.kefu-header__actions { display: flex; align-items: center; gap: 8px; }
.kefu-human-banner {
  margin: 0 0 12px; padding: 8px 12px; border-radius: 8px;
  background: var(--kefu-accent-soft, #eef1fe); color: var(--kefu-accent, #4f6ef2);
  font-size: 13px; text-align: center;
}
```

- [ ] **Step 7: 运行测试 + 构建**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: PASS（原 8 + 新 3 = 11 用例）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 8: Commit**

```bash
git add src/api/kefuPublic.ts src/views/kefu/KefuChatPage.vue src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客转人工按钮 + 接管态轮询接收人工回复"
```

---

### Task 2: 文档 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（第 10 节人工接管补访客侧）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 确认服务在运行**

前端 `:5173`（`npm run dev`）、后端 `:3002`、OpenCode `:4096`。前端改动由 Vite HMR 生效。

- [ ] **Step 2: Playwright 走访客转人工闭环**

Playwright：
1. 访客打开 `http://localhost:5173/kefu/demo`（新访客 id）→ 点 header「🙋 转人工」→ 断言：出现提示条「已为你转接人工客服…」、header 状态变「人工客服接入中」、「转人工」按钮消失。
2. 取该会话 sid（可从 `localStorage` 或先前 create-session 响应；或用一个已知访客 id 走 curl 建会话再在浏览器用同 id）。用 admin JWT curl：`POST /admin/kefu/sessions/<sid>/takeover` 然后 `POST /admin/kefu/sessions/<sid>/messages` 回复「您好，人工客服为您服务」。
3. 等待 ≤3s（轮询）→ 断言访客页出现该人工回复气泡。
4. 访客在接管态发一条消息 → 断言不卡「正在输入」、该消息进入历史。
5. 截图存 `.playwright-mcp/kefu-visitor-takeover.png`。事后清理该测试会话（`DELETE FROM ai_chat_sessions WHERE visitor_id='<测试id>'`）。

> 提示：为让 curl 与浏览器操作同一会话，可在浏览器 devtools 里读 `localStorage['kefu:visitor_id']`，用该 id 作为 curl 的 `X-Visitor-Id` 查询会话；或直接查 DB 取最近该 visitor 的 sid。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` 第 10 节「人工接管」补一小段「访客侧」：对话页 header 有「🙋 转人工」按钮，点击后进入人工接入态（顶部提示 + 「人工客服接入中」），此后人工回复经**轮询**（约 3s）到达；管理端接管控制台属子项目 B2（后续）。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 人工接管访客侧（转人工 + 轮询）说明"
```

---

## Self-Review

**Spec coverage:**
- `requestHuman` API + `sendKefuMessage` 返回补 `humanTakeover` → Task 1 Step 3 ✓
- 转人工按钮（header 右）+ humanMode + header 提示 → Task 1 Step 4/5 ✓
- 独立提示条（不进 messages，轮询安全）→ Task 1 Step 5(b) + 6 ✓
- 3s 轮询 + `onBeforeUnmount` 停 → Task 1 Step 4(a,b,d) ✓
- `send()` 接管态立即清 sending + enterHumanMode；非接管不变 → Task 1 Step 4(c) ✓
- 测试（requestHuman/轮询/接管 send/卸载停轮询）+ 现有绿 → Task 1 Step 1/7 ✓
- 文档 + Playwright → Task 2 ✓

**Placeholder scan:** 无 TBD/TODO；每个 code step 含完整代码；测试含具体断言（含 fake timers）。✓

**Type consistency:**
- `requestHuman(sid): Promise<{needsHuman}>`（Task 1 Step 3 定义）与 `KefuChatPage` 内 `api.requestHuman(sessionId.value)`（Step 4）一致 ✓
- `sendKefuMessage` 返回 `{messageId; humanTakeover?}`（Step 3）与 `send()` 里 `resp?.humanTakeover`（Step 4c）一致 ✓
- `humanMode`/`enterHumanMode`/`startPolling`/`stopPolling`/`pollTimer` 命名在 script、模板、测试间一致 ✓
- `defineExpose` 增 `requestHuman`/`humanMode` 与测试 `(vm as any).requestHuman()/.humanMode` 一致 ✓
