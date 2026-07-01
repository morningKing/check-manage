# 智能客服热问/自助面板 — Stage ②（访客页 + 自助抽屉 + 转 AI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 交付访客侧最小全页 `/kefu/:slug`：以 AI 对话为主，自助服务面板作抽屉；点击热问内联展开 Markdown 预写答案（点击埋点），「没解决？问 AI」把问题发进对话；端到端 Playwright 验证。

**Architecture:** 新增公开、无鉴权的顶层路由 `/kefu/:slug`（`meta.public`）。前端调用 Phase 1/Stage① 已有的公开匿名 API（`kefu_public_bp`，`X-Visitor-Id`）——不复用依赖 auth store/Bearer 的内部聊天组件，改用轻量 `src/api/kefuPublic.ts`。对话采用 SSE 监听 `session.idle` 后重载历史（配合「正在输入…」指示）——不重造逐字流式累积（YAGNI；后续可增强）。

**Tech Stack:** Vue 3 + Element Plus + TypeScript；`md-editor-v3` 的 `MdPreview`（经现有 `src/components/ai-chat/MarkdownView.vue` 复用）；原生 `EventSource`；Vitest + Playwright。

## Global Constraints

- 访客页无鉴权：路由放进 `src/router/index.ts` 的 `staticRoutes`（与 `/login` 同级，不在 `/` 认证布局的 children 内），`meta: { public: true, title: '在线客服' }`。全局守卫遇 `meta.public` 直接放行（`router/index.ts:205`）。
- 匿名凭证：localStorage key `kefu:visitor_id`，无则 `crypto.randomUUID()` 生成；所有 REST 请求带 `X-Visitor-Id` 头；SSE 用 `?visitor_id=<vid>` 查询参（EventSource 不能设头）。
- 复用已有公开端点（勿改后端）：`GET /kefu/i/<slug>`（配置）、`POST /kefu/i/<slug>/sessions`、`GET /kefu/sessions/<sid>/messages`、`POST /kefu/sessions/<sid>/messages`、`GET /kefu/sessions/<sid>/events`（SSE）、`GET /kefu/i/<slug>/faq`、`POST /kefu/i/<slug>/faq/<fid>/click`。前端路径带 `/api` 前缀（vite 重写）。
- 公开列表 FAQ 形状为 `{items:[{id,question,answer,category}]}`；点击返回静默 204。
- 前端 `npm run build`（vue-tsc 严格：noUnusedLocals/noUnusedParameters）必须过。Vitest 绿。
- 不改后端、不做可嵌入 widget（Phase 3 之后）。提交用中文 `feat:`/`test:` 前缀。

---

### Task 1: 公开 API 模块 `kefuPublic.ts`（REST + SSE）

**Files:**
- Create: `src/api/kefuPublic.ts`
- Test: `src/api/__tests__/kefuPublic.test.ts`

**Interfaces:**
- Produces:
  - `getVisitorId(): string`（localStorage `kefu:visitor_id`，无则生成并存）
  - `getKefuConfig(slug): Promise<{slug,name,welcome_message,guided_questions,branding,enabled}>`
  - `createKefuSession(slug): Promise<{id,title}>`
  - `getKefuHistory(sid): Promise<{messages:Array<{id,role,content,createdAt}>}>`
  - `sendKefuMessage(sid, content): Promise<{messageId}>`
  - `getKefuFaq(slug): Promise<{items:Array<{id,question,answer,category}>}>`
  - `clickKefuFaq(slug, fid): Promise<void>`（fire-and-forget，吞错）
  - `createKefuEventStream(sid, handlers): () => void`（EventSource，监听 `session.idle`/`message.updated`/`session.error`，自动重连；返回关闭函数）

- [ ] **Step 1: 写失败测试**（模型参照 `src/api/__tests__/aiChat.test.ts` 的 FakeEventSource）

```ts
// src/api/__tests__/kefuPublic.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string; listeners: Record<string, (e: any) => void> = {}
  onerror: any = null; onopen: any = null
  constructor(url: string) { this.url = url; FakeEventSource.last = this }
  addEventListener(name: string, cb: (e: any) => void) { this.listeners[name] = cb }
  close() {}
  emit(name: string, data: any) { this.listeners[name]?.({ data: JSON.stringify(data) }) }
}

beforeEach(() => {
  ;(globalThis as any).EventSource = FakeEventSource
  localStorage.clear()
})
afterEach(() => { vi.restoreAllMocks() })

describe('getVisitorId', () => {
  it('generates and persists a visitor id', async () => {
    const { getVisitorId } = await import('../kefuPublic')
    const a = getVisitorId()
    expect(a).toBeTruthy()
    expect(getVisitorId()).toBe(a)  // stable
    expect(localStorage.getItem('kefu:visitor_id')).toBe(a)
  })
})

describe('createKefuEventStream', () => {
  it('opens EventSource with visitor_id query param and routes session.idle', async () => {
    const { createKefuEventStream } = await import('../kefuPublic')
    const onIdle = vi.fn()
    createKefuEventStream('sess_1', { onIdle, onError: () => {} })
    expect(FakeEventSource.last!.url).toContain('/api/kefu/sessions/sess_1/events')
    expect(FakeEventSource.last!.url).toContain('visitor_id=')
    FakeEventSource.last!.emit('session.idle', { ok: true })
    expect(onIdle).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/api/__tests__/kefuPublic.test.ts` → FAIL（模块不存在）

- [ ] **Step 3: 写实现**

```ts
// src/api/kefuPublic.ts
import { get, post } from '@/utils/request'

const VID_KEY = 'kefu:visitor_id'

export function getVisitorId(): string {
  let v = localStorage.getItem(VID_KEY)
  if (!v) { v = (crypto as any).randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2); localStorage.setItem(VID_KEY, v) }
  return v
}

function vh() { return { headers: { 'X-Visitor-Id': getVisitorId() } } }

export interface KefuConfig { slug: string; name: string; welcome_message: string | null; guided_questions: string[]; branding: Record<string, any>; enabled: boolean }
export interface KefuFaqItem { id: string; question: string; answer: string; category: string | null }
export interface KefuMessage { id: string; role: string; content: any; createdAt: string | null }

export function getKefuConfig(slug: string) { return get<KefuConfig>(`/kefu/i/${slug}`) }
export function createKefuSession(slug: string) { return post<{ id: string; title: string }>(`/kefu/i/${slug}/sessions`, {}, vh()) }
export function getKefuHistory(sid: string) { return get<{ messages: KefuMessage[] }>(`/kefu/sessions/${sid}/messages`, undefined, vh()) }
export function sendKefuMessage(sid: string, content: string) { return post<{ messageId: string }>(`/kefu/sessions/${sid}/messages`, { content }, vh()) }
export function getKefuFaq(slug: string) { return get<{ items: KefuFaqItem[] }>(`/kefu/i/${slug}/faq`) }
export function clickKefuFaq(slug: string, fid: string) { return post(`/kefu/i/${slug}/faq/${fid}/click`, {}, vh()).catch(() => {}) }

const RECONNECT_MS = [1000, 2000, 5000, 10000]
export interface KefuStreamHandlers { onIdle: () => void; onError: (e: any) => void; onStatus?: (s: string) => void }

export function createKefuEventStream(sid: string, h: KefuStreamHandlers): () => void {
  const url = `/api/kefu/sessions/${encodeURIComponent(sid)}/events?visitor_id=${encodeURIComponent(getVisitorId())}`
  let es: EventSource | null = null, closed = false, attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const open = () => {
    if (closed) return
    es = new EventSource(url)
    es.addEventListener('session.idle', () => h.onIdle())
    es.addEventListener('session.error', (e: MessageEvent) => h.onError(e))
    es.onerror = (err) => {
      h.onError(err); es?.close(); if (closed) return
      h.onStatus?.('reconnecting')
      timer = setTimeout(open, RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)]); attempt += 1
    }
    es.onopen = () => { attempt = 0; h.onStatus?.('open') }
  }
  open()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}
```

- [ ] **Step 4: 运行确认通过** — `npx vitest run src/api/__tests__/kefuPublic.test.ts` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/kefuPublic.ts src/api/__tests__/kefuPublic.test.ts
git commit -m "feat(kefu): 访客公开 API 模块 kefuPublic（REST + SSE）"
```

---

### Task 2: 自助服务面板 `KefuSelfServicePanel.vue`

**Files:**
- Create: `src/components/kefu/KefuSelfServicePanel.vue`
- Test: `src/components/kefu/__tests__/KefuSelfServicePanel.test.ts`

**Interfaces:**
- Consumes: `kefuPublic.KefuFaqItem`.
- Props: `items: KefuFaqItem[]`.
- Emits: `click(id: string)`（展开某条时触发埋点）、`escalate(question: string)`（「问 AI」）。
- Internal: 文本过滤 `filter`（匹配 question）；分类标签筛选 `activeTag`（`null`=全部）；展开态 `expandedId`。计算 `categories`（去重非空 category）、`visible`（按 tag + filter 过滤）。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/kefu/__tests__/KefuSelfServicePanel.test.ts
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuSelfServicePanel from '../KefuSelfServicePanel.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

const items = [
  { id: 'a', question: '如何部署', answer: '**A**', category: '部署' },
  { id: 'b', question: '价格套餐', answer: 'B', category: '定价' },
  { id: 'c', question: '部署环境要求', answer: 'C', category: '部署' },
]
const stubs = {
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  'el-tag': { template: `<button @click="$emit('click')"><slot/></button>` },
  MdPreview: { template: `<div class="md"><slot/></div>`, props: ['modelValue'] },
}

describe('KefuSelfServicePanel', () => {
  it('filters by text over question', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).filter = '部署'
    await w.vm.$nextTick()
    expect((w.vm as any).visible.map((i: any) => i.id)).toEqual(['a', 'c'])
  })

  it('filters by category tag', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).activeTag = '定价'
    await w.vm.$nextTick()
    expect((w.vm as any).visible.map((i: any) => i.id)).toEqual(['b'])
  })

  it('emits click on expand and escalate on ask-AI', async () => {
    const w = mount(KefuSelfServicePanel, { props: { items }, global: { stubs } })
    ;(w.vm as any).toggle('a')
    ;(w.vm as any).askAi(items[0])
    expect(w.emitted('click')?.[0]).toEqual(['a'])
    expect(w.emitted('escalate')?.[0]).toEqual(['如何部署'])
  })
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/components/kefu/__tests__/KefuSelfServicePanel.test.ts` → FAIL

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/kefu/KefuSelfServicePanel.vue -->
<template>
  <div class="kefu-ssp">
    <el-input v-model="filter" placeholder="搜索常见问题" clearable />
    <div class="tags">
      <el-tag :effect="activeTag===null?'dark':'plain'" @click="activeTag=null">全部</el-tag>
      <el-tag v-for="c in categories" :key="c" :effect="activeTag===c?'dark':'plain'" @click="activeTag=c">{{ c }}</el-tag>
    </div>
    <div v-if="visible.length===0" class="empty">暂无匹配的问题</div>
    <div v-for="it in visible" :key="it.id" class="faq">
      <div class="q" @click="toggle(it.id)">▸ {{ it.question }}</div>
      <div v-if="expandedId===it.id" class="a">
        <MdPreview :modelValue="it.answer" :code-foldable="false" />
        <el-button size="small" @click="askAi(it)">没解决？问 AI</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import type { KefuFaqItem } from '@/api/kefuPublic'

const props = defineProps<{ items: KefuFaqItem[] }>()
const emit = defineEmits<{ (e: 'click', id: string): void; (e: 'escalate', q: string): void }>()

const filter = ref('')
const activeTag = ref<string | null>(null)
const expandedId = ref<string | null>(null)

const categories = computed(() => Array.from(new Set(props.items.map(i => i.category).filter((c): c is string => !!c))))
const visible = computed(() => props.items.filter(i =>
  (activeTag.value === null || i.category === activeTag.value) &&
  (filter.value === '' || i.question.includes(filter.value))))

function toggle(id: string) {
  expandedId.value = expandedId.value === id ? null : id
  if (expandedId.value === id) emit('click', id)
}
function askAi(it: KefuFaqItem) { emit('escalate', it.question) }
</script>
```

- [ ] **Step 4: 运行确认通过** — `npx vitest run src/components/kefu/__tests__/KefuSelfServicePanel.test.ts` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/components/kefu/KefuSelfServicePanel.vue src/components/kefu/__tests__/KefuSelfServicePanel.test.ts
git commit -m "feat(kefu): 自助服务面板组件（筛选/展开/转 AI）"
```

---

### Task 3: 访客全页 `KefuChatPage.vue` + 路由

**Files:**
- Create: `src/views/kefu/KefuChatPage.vue`
- Modify: `src/router/index.ts`（`staticRoutes` 加公开路由）
- Test: 逻辑 Vitest `src/views/kefu/__tests__/KefuChatPage.test.ts`（mock `@/api/kefuPublic`）+ Playwright（Task 4）

**Interfaces:**
- Consumes: `@/api/kefuPublic.*`、`KefuSelfServicePanel`、`MarkdownView`（`src/components/ai-chat/MarkdownView.vue` 复用作助手 Markdown 渲染）。
- Behavior: 挂载时 `getKefuConfig(slug)` → `createKefuSession(slug)` → `getKefuFaq(slug)`；`createKefuEventStream(sid, {onIdle})`：`onIdle` → 重载历史 + 关「正在输入」。发送：`sendKefuMessage` → 202 → 置 `sending=true`（显示「正在输入…」）→ 等 `onIdle` 重载。抽屉 `escalate(q)` → 关抽屉 + 走发送流程。

- [ ] **Step 1: 写失败测试**

```ts
// src/views/kefu/__tests__/KefuChatPage.test.ts
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
vi.mock('@/api/kefuPublic', () => ({
  getVisitorId: () => 'v1',
  getKefuConfig: vi.fn().mockResolvedValue({ slug: 's', name: 'KF', welcome_message: 'hi', guided_questions: [], branding: {}, enabled: true }),
  createKefuSession: vi.fn().mockResolvedValue({ id: 'sess_1', title: 't' }),
  getKefuFaq: vi.fn().mockResolvedValue({ items: [{ id: 'a', question: 'Q', answer: 'A', category: null }] }),
  getKefuHistory: vi.fn().mockResolvedValue({ messages: [] }),
  sendKefuMessage: vi.fn().mockResolvedValue({ messageId: 'm1' }),
  clickKefuFaq: vi.fn().mockResolvedValue(undefined),
  createKefuEventStream: vi.fn().mockReturnValue(() => {}),
}))
import * as api from '@/api/kefuPublic'
import KefuChatPage from '../KefuChatPage.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
const stubs = { 'el-drawer': { template: '<div v-if="modelValue"><slot/></div>', props: ['modelValue'] },
  'el-button': { template: `<button @click="$emit('click')"><slot/></button>` },
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  MarkdownView: { template: '<div />', props: ['text'] },
  KefuSelfServicePanel: { template: '<div class="ssp" />', props: ['items'] } }

describe('KefuChatPage', () => {
  it('bootstraps config + session + faq on mount', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    expect(api.getKefuConfig).toHaveBeenCalledWith('s')
    expect(api.createKefuSession).toHaveBeenCalledWith('s')
    expect(api.getKefuFaq).toHaveBeenCalledWith('s')
    expect((w.vm as any).sessionId).toBe('sess_1')
  })

  it('escalate sends the question as a message', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).onEscalate('如何部署')
    expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '如何部署')
  })
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → FAIL

- [ ] **Step 3: 写 `KefuChatPage.vue`**

```vue
<!-- src/views/kefu/KefuChatPage.vue -->
<template>
  <div class="kefu-page">
    <header class="kefu-header">
      <span class="title">{{ config?.name || '在线客服' }}</span>
      <el-button size="small" @click="drawer = true">🗂 自助服务</el-button>
    </header>
    <main class="kefu-messages" ref="scroller">
      <div v-if="config?.welcome_message" class="msg assistant"><MarkdownView :text="config.welcome_message" /></div>
      <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
        <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
        <span v-else class="user-text">{{ plainText(m.content) }}</span>
      </div>
      <div v-if="sending" class="typing">正在输入…</div>
    </main>
    <footer class="kefu-input">
      <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter.prevent="send" />
      <el-button type="primary" :disabled="!draft.trim() || sending" @click="send">发送</el-button>
    </footer>
    <el-drawer v-model="drawer" title="自助服务" direction="rtl" size="360px">
      <KefuSelfServicePanel :items="faq" @click="onFaqClick" @escalate="onEscalate" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import KefuSelfServicePanel from '@/components/kefu/KefuSelfServicePanel.vue'
import * as api from '@/api/kefuPublic'
import type { KefuConfig, KefuFaqItem, KefuMessage } from '@/api/kefuPublic'

const props = defineProps<{ slug: string }>()
const config = ref<KefuConfig | null>(null)
const sessionId = ref('')
const faq = ref<KefuFaqItem[]>([])
const messages = ref<KefuMessage[]>([])
const draft = ref('')
const sending = ref(false)
const drawer = ref(false)
const scroller = ref<HTMLElement | null>(null)
let closeStream: (() => void) | null = null

function normalize(content: any) { return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }] }
function plainText(content: any) { return normalize(content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join('') }
async function scrollDown() { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight }

async function reload() { messages.value = (await api.getKefuHistory(sessionId.value)).messages; await scrollDown() }

async function send() {
  const text = draft.value.trim(); if (!text || sending.value) return
  draft.value = ''; sending.value = true
  messages.value.push({ id: 'local_' + Date.now(), role: 'user', content: [{ type: 'text', text }], createdAt: null })
  await scrollDown()
  try { await api.sendKefuMessage(sessionId.value, text) } catch { sending.value = false }
}

function onFaqClick(id: string) { api.clickKefuFaq(props.slug, id) }
async function onEscalate(question: string) { drawer.value = false; draft.value = question; await send() }

onMounted(async () => {
  config.value = await api.getKefuConfig(props.slug)
  const s = await api.createKefuSession(props.slug); sessionId.value = s.id
  faq.value = (await api.getKefuFaq(props.slug)).items
  await reload()
  closeStream = api.createKefuEventStream(sessionId.value, {
    onIdle: async () => { await reload(); sending.value = false },
    onError: () => {},
  })
})
onBeforeUnmount(() => { closeStream?.() })
defineExpose({ sessionId, onEscalate, messages, sending })
</script>
```

- [ ] **Step 4: 注册公开路由** — 在 `src/router/index.ts` 的 `staticRoutes` 数组里（`/login` 之后）加：

```ts
  {
    path: '/kefu/:slug',
    name: 'KefuChat',
    component: () => import('@/views/kefu/KefuChatPage.vue'),
    props: true,
    meta: { title: '在线客服', public: true },
  },
```

- [ ] **Step 5: 运行测试 + 类型检查** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → PASS；`npm run build` → vue-tsc clean。

- [ ] **Step 6: 提交**

```bash
git add src/views/kefu/KefuChatPage.vue src/router/index.ts src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客全页 KefuChatPage + 公开路由 /kefu/:slug"
```

---

### Task 4: 端到端 Playwright + 文档

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

- [ ] **Step 1: Playwright 手测（必做）** — 确保后端（含 Stage① FAQ 路由，需重启加载）+ OpenCode + MCP + vite 均在跑。管理员先建一个客服实例并加 2 条热问（可用 Stage① 的 `/admin/kefu` UI 或 admin API）。然后**匿名**（不登录、清 localStorage 里的 auth）浏览器访问 `http://<host>:5173/kefu/<slug>`：
  1. 页面加载，显示欢迎语；无鉴权跳转（`meta.public` 生效）。
  2. 打开「🗂 自助服务」抽屉 → 见热问平铺；按分类标签筛选；文本搜索。
  3. 点击一条热问 → 内联展开 Markdown 答案；DB 交叉核对该条 `click_count` 自增。
  4. 点「没解决？问 AI」→ 抽屉关闭，问题作为消息发进对话，出现「正在输入…」，`session.idle` 后助手回复渲染（Markdown）。
  5. 直接在输入框发一条消息也能得到回复。
  截图存 `.playwright-mcp/kefu-visitor-e2e.png`。

- [ ] **Step 2: 更新文档** — 在 `docs/user-guide/ai/smart-customer-service.md` 把「访客自助体验（Stage ②）」从「后续」改为已交付：说明访客链接 `/<host>/kefu/<slug>`、自助抽屉用法（筛选/展开/转 AI）、对话为主的布局。

- [ ] **Step 3: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 访客自助面板（Stage ②）使用文档"
```

---

## Self-Review

**Spec coverage（对照 design §7、§9、§10 阶段②）：**
- §7 访客全页 `/kefu/:slug`、`kefuPublic.ts`（visitor_id/无 JWT）→ Task 1、Task 3 ✓
- §7 对话（复用公开 API + SSE）→ Task 3（SSE `session.idle`→重载历史 + 「正在输入」；YAGNI：不做逐字流式，已在 Architecture 标注）✓
- §7 自助抽屉（平铺 + 标签筛选 + 内联 MdPreview + 点击埋点 + 转 AI）→ Task 2 + Task 3 ✓
- §7 匿名凭证 localStorage `kefu:visitor_id` → Task 1 ✓
- §9 测试（前端 Vitest + Playwright + 文档）→ Task 1-4 ✓
- §10 阶段② 范围 → 本计划 ✓；可嵌入 widget 仍不做（Phase 3）。

**Placeholder scan:** 无 TBD/TODO。SSE 采用「idle→重载历史」是明确的 YAGNI 决策（非占位），逐字流式列为后续增强。Playwright 步骤是人工验证清单（前端 flow 无法纯单测），断言点具体。

**Type consistency:** `KefuConfig`/`KefuFaqItem`/`KefuMessage` 在 `kefuPublic.ts` 定义并被 Task 2/3 一致使用；`createKefuEventStream(sid, {onIdle,onError})` 签名在 Task 1 定义、Task 3 调用一致；面板 emits `click(id)`/`escalate(question)` 与 Task 3 的 `onFaqClick`/`onEscalate` 对应；路由 `meta.public` 与守卫 `router/index.ts:205` 一致。

---

## 后续（Stage ② 之后）

- 逐字流式（消费 `message.part.updated` 文本增量）作为对话体验增强。
- 可嵌入悬浮 widget（独立构建 entry + iframe）——原 Phase 3 范围。
- 文件上传入访客对话（复用 `POST /kefu/sessions/<sid>/files`）。
