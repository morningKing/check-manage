# 客服主页现代化 — Stage ②（访客页两栏重构 + 区块渲染 + 提示气泡）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把访客页 `/kefu/:slug` 从「对话 + FAQ 抽屉」重构为现代化两栏布局：左对话（欢迎区含可点提示气泡）+ 右自助区块列（桌面常驻、手机抽屉），区块按 4 种类型渲染，端到端 Playwright 验证。

**Architecture:** 复用 Stage① 已就绪的后端（`GET /kefu/i/<slug>` 现返回 `guided_questions`+`panel_blocks`）。前端新增 4 个 Block 子组件 + `KefuServiceColumn` 分发器，重构 `KefuChatPage` 为两栏 + 提示气泡；`KefuBlockFaq` 复用现有 `KefuSelfServicePanel`（展开/埋点/转 AI），不重造。

**Tech Stack:** Vue 3 + Element Plus + TypeScript；`md-editor-v3` `MdPreview`；CSS grid + 媒体查询；Vitest + Playwright。

## Global Constraints

- 复用 Stage① 后端：公开配置 `GET /kefu/i/<slug>` 返回 `guided_questions`（字符串数组）与 `panel_blocks`（区块数组，每块 `{id,type,title?,enabled?,config}`，type ∈ `links|faq|richtext|contact`）。richtext config 键为 **`markdown`**（不是 `content`）。
- 访客页保持公开无鉴权（现有 `/kefu/:slug` `meta.public` 不变）；只用 `@/api/kefuPublic` 匿名端点。
- `KefuBlockFaq` 复用现有 `src/components/kefu/KefuSelfServicePanel.vue`（props `items:KefuFaqItem[]`；emits `click(id)`/`escalate(question)`），按 `config.limit`(默认5) 截断 items。
- `links.url` 渲染前做协议校验：仅允许 `http:`/`https:`/相对路径（`/` 或 `./` 开头），否则不渲染为链接（防 `javascript:` 注入）；链接用 `target="_blank" rel="noopener noreferrer"`。
- 桌面（≥992px）两栏常驻右列；移动（<992px）右列隐藏、头部「🗂 自助服务」按钮唤出 `el-drawer` 装同一个 `KefuServiceColumn`。
- 前端 `npm run build`（vue-tsc 严格 noUnusedLocals/noUnusedParameters）必须过；Vitest 绿。不改后端。提交用中文 `feat:`/`fix:` 前缀。

---

### Task 1: 类型 + 4 个 Block 子组件

**Files:**
- Modify: `src/api/kefuPublic.ts`（加 `PanelBlock` 类型 + `KefuConfig.panel_blocks`）
- Create: `src/components/kefu/KefuBlockLinks.vue`, `KefuBlockRichtext.vue`, `KefuBlockContact.vue`, `KefuBlockFaq.vue`
- Test: `src/components/kefu/__tests__/KefuBlocks.test.ts`

**Interfaces:**
- Produces: `PanelBlock` 类型；4 个组件。
  - `KefuBlockLinks` props `{ config: { items?: {icon?:string;label:string;url:string}[] } }`；渲染合法 url 的链接卡片。
  - `KefuBlockRichtext` props `{ config: { markdown?: string } }`；`MdPreview` 渲染。
  - `KefuBlockContact` props `{ config: { phone?:string;email?:string;hours?:string;wechat?:string } }`；有值才渲染对应行。
  - `KefuBlockFaq` props `{ items: KefuFaqItem[]; config: { limit?: number } }`；emits `click(id)`/`escalate(q)`；内部 `<KefuSelfServicePanel :items="limited" @click @escalate />`。
  - 导出 helper `isSafeUrl(u: string): boolean`（供 links 用与测试）。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/kefu/__tests__/KefuBlocks.test.ts
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuBlockLinks, { isSafeUrl } from '../KefuBlockLinks.vue'
import KefuBlockContact from '../KefuBlockContact.vue'
import KefuBlockFaq from '../KefuBlockFaq.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

describe('isSafeUrl', () => {
  it('allows http/https/relative, rejects javascript:', () => {
    expect(isSafeUrl('https://x.com')).toBe(true)
    expect(isSafeUrl('http://x.com')).toBe(true)
    expect(isSafeUrl('/docs')).toBe(true)
    expect(isSafeUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeUrl('data:text/html,x')).toBe(false)
  })
})

describe('KefuBlockLinks', () => {
  it('renders only safe-url items as anchors with rel/target', () => {
    const w = mount(KefuBlockLinks, { props: { config: { items: [
      { label: 'Docs', url: 'https://x.com/docs' },
      { label: 'Bad', url: 'javascript:alert(1)' },
    ] } } })
    const anchors = w.findAll('a')
    expect(anchors).toHaveLength(1)
    expect(anchors[0].attributes('rel')).toContain('noopener')
    expect(anchors[0].attributes('target')).toBe('_blank')
  })
})

describe('KefuBlockContact', () => {
  it('renders only provided fields', () => {
    const w = mount(KefuBlockContact, { props: { config: { phone: '123', hours: '9-6' } } })
    expect(w.text()).toContain('123')
    expect(w.text()).toContain('9-6')
    expect(w.text()).not.toContain('undefined')
  })
})

describe('KefuBlockFaq', () => {
  it('limits items and re-emits click/escalate', async () => {
    const items = [
      { id: 'a', question: 'Q1', answer: 'A1', category: null },
      { id: 'b', question: 'Q2', answer: 'A2', category: null },
      { id: 'c', question: 'Q3', answer: 'A3', category: null },
    ]
    const w = mount(KefuBlockFaq, { props: { items, config: { limit: 2 } },
      global: { stubs: { KefuSelfServicePanel: {
        props: ['items'], emits: ['click','escalate'],
        template: `<div><button class="c" @click="$emit('click','a')"/><button class="e" @click="$emit('escalate','Q1')"/></div>` } } } })
    // limit applied: the stub received 2 items
    expect((w.vm as any).limited).toHaveLength(2)
    await w.find('button.c').trigger('click')
    await w.find('button.e').trigger('click')
    expect(w.emitted('click')?.[0]).toEqual(['a'])
    expect(w.emitted('escalate')?.[0]).toEqual(['Q1'])
  })
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/components/kefu/__tests__/KefuBlocks.test.ts` → FAIL

- [ ] **Step 3: 加类型到 `src/api/kefuPublic.ts`**

```ts
export interface PanelBlock { id: string; type: 'links' | 'faq' | 'richtext' | 'contact'; title?: string; enabled?: boolean; config: any }
```
并在 `KefuConfig` 接口加：`panel_blocks?: PanelBlock[]`。

- [ ] **Step 4: 写 `KefuBlockLinks.vue`**

```vue
<template>
  <div class="kefu-block-links">
    <a v-for="(it, i) in safeItems" :key="i" class="link-card" :href="it.url" target="_blank" rel="noopener noreferrer">
      <span v-if="it.icon" class="ico">{{ it.icon }}</span><span class="lbl">{{ it.label }}</span>
    </a>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ config: { items?: { icon?: string; label: string; url: string }[] } }>()
export function isSafeUrl(u: string): boolean {
  if (!u) return false
  if (u.startsWith('/') || u.startsWith('./')) return true
  return /^https?:\/\//i.test(u)
}
const safeItems = computed(() => (props.config.items || []).filter(it => it.label && isSafeUrl(it.url)))
</script>
```
> `isSafeUrl` 需可被测试 import。Vue SFC 的 `<script setup>` 无法命名导出函数；因此把 `isSafeUrl` 放到 `src/components/kefu/urlSafe.ts` 导出，组件与测试都从它 import。调整：新建 `urlSafe.ts`（`export function isSafeUrl...`），`KefuBlockLinks.vue` `import { isSafeUrl } from './urlSafe'`，测试改为 `import { isSafeUrl } from '../urlSafe'`。

- [ ] **Step 5: 写 `urlSafe.ts` + 修正 import**

```ts
// src/components/kefu/urlSafe.ts
export function isSafeUrl(u: string): boolean {
  if (!u) return false
  if (u.startsWith('/') || u.startsWith('./')) return true
  return /^https?:\/\//i.test(u)
}
```
`KefuBlockLinks.vue` 改 `import { isSafeUrl } from './urlSafe'`（删组件内定义）。测试文件顶部 `import { isSafeUrl } from '../urlSafe'`（不再从 KefuBlockLinks import）。

- [ ] **Step 6: 写 `KefuBlockRichtext.vue`**

```vue
<template>
  <div class="kefu-block-richtext"><MdPreview :modelValue="config.markdown || ''" :code-foldable="false" /></div>
</template>
<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
defineProps<{ config: { markdown?: string } }>()
</script>
```

- [ ] **Step 7: 写 `KefuBlockContact.vue`**

```vue
<template>
  <div class="kefu-block-contact">
    <div v-if="config.phone" class="row">📞 {{ config.phone }}</div>
    <div v-if="config.email" class="row">✉️ {{ config.email }}</div>
    <div v-if="config.hours" class="row">🕒 {{ config.hours }}</div>
    <div v-if="config.wechat" class="row">💬 {{ config.wechat }}</div>
  </div>
</template>
<script setup lang="ts">
defineProps<{ config: { phone?: string; email?: string; hours?: string; wechat?: string } }>()
</script>
```

- [ ] **Step 8: 写 `KefuBlockFaq.vue`**

```vue
<template>
  <KefuSelfServicePanel :items="limited" @click="(id:string)=>emit('click',id)" @escalate="(q:string)=>emit('escalate',q)" />
</template>
<script setup lang="ts">
import { computed } from 'vue'
import KefuSelfServicePanel from './KefuSelfServicePanel.vue'
import type { KefuFaqItem } from '@/api/kefuPublic'
const props = defineProps<{ items: KefuFaqItem[]; config: { limit?: number } }>()
const emit = defineEmits<{ (e: 'click', id: string): void; (e: 'escalate', q: string): void }>()
const limited = computed(() => props.items.slice(0, props.config.limit || 5))
</script>
```

- [ ] **Step 9: 运行确认通过 + 构建** — `npx vitest run src/components/kefu/__tests__/KefuBlocks.test.ts` → PASS；`npm run build` → clean。

- [ ] **Step 10: 提交**

```bash
git add src/api/kefuPublic.ts src/components/kefu/KefuBlock*.vue src/components/kefu/urlSafe.ts src/components/kefu/__tests__/KefuBlocks.test.ts
git commit -m "feat(kefu): 访客自助区块子组件（links/faq/richtext/contact）"
```

---

### Task 2: 区块分发器 `KefuServiceColumn.vue`

**Files:**
- Create: `src/components/kefu/KefuServiceColumn.vue`
- Test: `src/components/kefu/__tests__/KefuServiceColumn.test.ts`

**Interfaces:**
- Consumes: 4 个 Block 组件 + `PanelBlock`/`KefuFaqItem` 类型。
- Props: `{ blocks: PanelBlock[]; faqItems: KefuFaqItem[] }`。Emits: `faqClick(id)`、`escalate(q)`。
- 只渲染 `enabled !== false` 的块；按 `type` 分发；块标题用 `block.title || 默认名`；faq 块把 `faqItems` 传下并冒泡 click/escalate。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/kefu/__tests__/KefuServiceColumn.test.ts
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuServiceColumn from '../KefuServiceColumn.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
const stubs = {
  KefuBlockLinks: { props: ['config'], template: '<div class="b-links" />' },
  KefuBlockRichtext: { props: ['config'], template: '<div class="b-rt" />' },
  KefuBlockContact: { props: ['config'], template: '<div class="b-contact" />' },
  KefuBlockFaq: { props: ['items','config'], emits: ['click','escalate'],
    template: `<div class="b-faq"><button @click="$emit('click','x')"/><button class="e" @click="$emit('escalate','Q')"/></div>` },
}

describe('KefuServiceColumn', () => {
  it('renders only enabled blocks dispatched by type', () => {
    const blocks = [
      { id: '1', type: 'links', enabled: true, config: {} },
      { id: '2', type: 'contact', enabled: false, config: {} },
      { id: '3', type: 'faq', enabled: true, config: { limit: 5 } },
    ]
    const w = mount(KefuServiceColumn, { props: { blocks, faqItems: [] }, global: { stubs } })
    expect(w.find('.b-links').exists()).toBe(true)
    expect(w.find('.b-contact').exists()).toBe(false)  // disabled
    expect(w.find('.b-faq').exists()).toBe(true)
  })

  it('bubbles faq click/escalate', async () => {
    const blocks = [{ id: '3', type: 'faq', enabled: true, config: {} }]
    const w = mount(KefuServiceColumn, { props: { blocks, faqItems: [] }, global: { stubs } })
    await w.find('.b-faq button').trigger('click')
    await w.find('.b-faq button.e').trigger('click')
    expect(w.emitted('faqClick')?.[0]).toEqual(['x'])
    expect(w.emitted('escalate')?.[0]).toEqual(['Q'])
  })
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/components/kefu/__tests__/KefuServiceColumn.test.ts` → FAIL

- [ ] **Step 3: 写 `KefuServiceColumn.vue`**

```vue
<template>
  <div class="kefu-service-column">
    <section v-for="b in visibleBlocks" :key="b.id" class="svc-block">
      <h3 class="svc-title">{{ b.title || defaultTitle(b.type) }}</h3>
      <KefuBlockLinks v-if="b.type==='links'" :config="b.config" />
      <KefuBlockFaq v-else-if="b.type==='faq'" :items="faqItems" :config="b.config"
        @click="(id:string)=>emit('faqClick',id)" @escalate="(q:string)=>emit('escalate',q)" />
      <KefuBlockRichtext v-else-if="b.type==='richtext'" :config="b.config" />
      <KefuBlockContact v-else-if="b.type==='contact'" :config="b.config" />
    </section>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import KefuBlockLinks from './KefuBlockLinks.vue'
import KefuBlockFaq from './KefuBlockFaq.vue'
import KefuBlockRichtext from './KefuBlockRichtext.vue'
import KefuBlockContact from './KefuBlockContact.vue'
import type { PanelBlock, KefuFaqItem } from '@/api/kefuPublic'
const props = defineProps<{ blocks: PanelBlock[]; faqItems: KefuFaqItem[] }>()
const emit = defineEmits<{ (e: 'faqClick', id: string): void; (e: 'escalate', q: string): void }>()
const visibleBlocks = computed(() => (props.blocks || []).filter(b => b.enabled !== false))
function defaultTitle(t: string) {
  return ({ links: '快捷入口', faq: '热点问题', richtext: '公告', contact: '联系我们' } as any)[t] || ''
}
</script>
```

- [ ] **Step 4: 运行确认通过 + 构建** — `npx vitest run src/components/kefu/__tests__/KefuServiceColumn.test.ts` → PASS；`npm run build` → clean。

- [ ] **Step 5: 提交**

```bash
git add src/components/kefu/KefuServiceColumn.vue src/components/kefu/__tests__/KefuServiceColumn.test.ts
git commit -m "feat(kefu): 自助区块分发列 KefuServiceColumn"
```

---

### Task 3: `KefuChatPage.vue` 两栏重构 + 提示气泡

**Files:**
- Modify: `src/views/kefu/KefuChatPage.vue`
- Test: `src/views/kefu/__tests__/KefuChatPage.test.ts`（扩现有）

**Interfaces:**
- Consumes: `KefuServiceColumn`、`config.guided_questions`、`config.panel_blocks`。
- 行为：桌面两栏（左对话主区 + 右 `KefuServiceColumn` 常驻）；移动经「🗂 自助服务」抽屉装同一个 `KefuServiceColumn`。欢迎区渲染 `guided_questions` 为 chip，点击 `askBubble(q)` = 发送该问题；发首条消息后欢迎区隐藏。区块 faqClick → `onFaqClick`，escalate → `onEscalate`（复用现有）。移除旧的直接内嵌 `KefuSelfServicePanel`（改由 KefuServiceColumn 的 faq 块承载）。

- [ ] **Step 1: 写失败测试**（扩 `KefuChatPage.test.ts`；现有 mock `@/api/kefuPublic` 需补 config 返回 guided_questions + panel_blocks）

```ts
// 在现有 KefuChatPage.test.ts 的 getKefuConfig mock 返回值补：
//   guided_questions: ['价格?'], panel_blocks: [{id:'1',type:'faq',enabled:true,config:{}}]
// 新增用例：
it('renders prompt bubbles from guided_questions and sends on click', async () => {
  const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
  await flushPromises()
  // bubble click calls sendKefuMessage with the bubble text
  await (w.vm as any).askBubble('价格?')
  expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '价格?')
})

it('passes panel_blocks + faq to KefuServiceColumn', async () => {
  const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
  await flushPromises()
  expect((w.vm as any).blocks).toHaveLength(1)
})
```
（`stubs` 里加 `KefuServiceColumn: { props:['blocks','faqItems'], template:'<div class=col />' }`。）

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → FAIL（askBubble/blocks 未定义）

- [ ] **Step 3: 重构 `KefuChatPage.vue`**

模板（替换现有 template 主体）：
```vue
<template>
  <div class="kefu-page" :class="{ 'with-column': hasBlocks }">
    <template v-if="!loadError">
      <div class="kefu-main">
        <header class="kefu-header">
          <span class="title">{{ config?.name || '在线客服' }}</span>
          <el-button v-if="hasBlocks" class="svc-toggle" size="small" @click="drawer = true">🗂 自助服务</el-button>
        </header>
        <main class="kefu-messages" ref="scroller">
          <div v-if="showWelcome" class="kefu-welcome">
            <MarkdownView v-if="config?.welcome_message" :text="config.welcome_message" />
            <div v-if="bubbles.length" class="bubbles">
              <button v-for="(b,i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
            </div>
          </div>
          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
            <span v-else class="user-text">{{ plainText(m.content) }}</span>
          </div>
          <div v-if="sending" class="typing">正在输入…</div>
        </main>
        <footer class="kefu-input">
          <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter="onEnter" />
          <el-button type="primary" :disabled="!draft.trim() || sending" @click="send">发送</el-button>
        </footer>
      </div>
      <aside v-if="hasBlocks" class="kefu-column">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalate" />
      </aside>
      <el-drawer v-model="drawer" title="自助服务" direction="rtl" size="360px">
        <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalateDrawer" />
      </el-drawer>
    </template>
    <div v-else class="kefu-error">客服暂不可用，请稍后再试</div>
  </div>
</template>
```
脚本增量：
```ts
import KefuServiceColumn from '@/components/kefu/KefuServiceColumn.vue'
import { computed } from 'vue'
// ...existing refs...
const bubbles = computed(() => config.value?.guided_questions || [])
const blocks = computed(() => config.value?.panel_blocks || [])
const hasBlocks = computed(() => blocks.value.some(b => b.enabled !== false))
const showWelcome = computed(() => messages.value.length === 0)
async function askBubble(q: string) { if (sending.value) return; draft.value = q; await send() }
async function onEscalateDrawer(q: string) { drawer.value = false; await onEscalate(q) }
```
> 保留现有 `send/onEnter/onFaqClick/onEscalate/reload/plainText/onMounted/SSE`。`onEscalate` 已 `drawer.value=false`（无害）。移除模板里旧的直接 `<KefuSelfServicePanel>`（其能力现由 KefuServiceColumn 的 faq 块提供）；`KefuSelfServicePanel` 仍被 `KefuBlockFaq` 使用，勿删该组件。`defineExpose` 增加 `askBubble, blocks, bubbles`。

样式（`<style scoped>` 增补，media query 两栏）：
```scss
.kefu-page { display: flex; height: 100vh; }
.kefu-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.kefu-column { width: 340px; border-left: 1px solid var(--el-border-color, #eee); overflow-y: auto; padding: 12px; }
.kefu-welcome .bubbles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.bubble { border: 1px solid var(--el-color-primary, #409eff); color: var(--el-color-primary, #409eff); background: transparent; border-radius: 16px; padding: 6px 14px; cursor: pointer; }
.svc-toggle { display: none; }
@media (max-width: 991px) {
  .kefu-column { display: none; }
  .svc-toggle { display: inline-flex; }
}
@media (min-width: 992px) {
  .svc-toggle { display: none; }  /* desktop uses persistent column, not drawer */
}
```

- [ ] **Step 4: 运行测试 + 构建** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → PASS；`npm run build` → vue-tsc clean（注意 `KefuSelfServicePanel` 若不再在 KefuChatPage import 需删除其 import，避免 noUnusedLocals 报错）。

- [ ] **Step 5: 提交**

```bash
git add src/views/kefu/KefuChatPage.vue src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客页两栏重构 + 提示气泡 + 自助区块列"
```

---

### Task 4: 端到端 Playwright + 文档

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

- [ ] **Step 1: Playwright 手测（必做）** — 后端 + OpenCode + MCP + vite 均在跑。管理员在 `/admin/kefu` 给某实例配置：2 条提示气泡 + 各类型区块（links 2 条、faq、richtext、contact）。然后**匿名**（清 auth localStorage）访问桌面宽度 `http://<host>:5173/kefu/<slug>`：
  1. 两栏布局：左对话（欢迎区显示气泡）+ 右自助区块列（各区块渲染）。
  2. 点提示气泡 → 作为消息发送，得流式回复。
  3. 右列 links 卡片是链接（target=_blank）；faq 块点击展开答案 + 埋点（DB 核对 click_count）+「问 AI」升级进对话；richtext/contact 正常显示。
  4. 缩窄到 <992px：右列隐藏，「🗂 自助服务」按钮出现，点开抽屉显示同样区块。
  截图存 `.playwright-mcp/kefu-home-visitor.png`（桌面两栏）与 `kefu-home-mobile.png`（移动抽屉）。

- [ ] **Step 2: 更新文档** — `docs/user-guide/ai/smart-customer-service.md` 把「访客主页」新版布局从 Stage② 待做改为已交付：两栏布局、提示气泡、右侧自助区块、移动抽屉。

- [ ] **Step 3: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 访客主页现代化布局（Stage②）文档"
```

---

## Self-Review

**Spec coverage（对照 design §7、§8、§9、§10 阶段②）：**
- §7.1 两栏 + 移动抽屉 → Task 3 ✓；§7.2 提示气泡（点击发送）→ Task 3 ✓；§7.3 KefuServiceColumn + 4 Block 子组件（faq 复用 KefuSelfServicePanel）→ Task 1+2 ✓；§8 安全（links url 协议校验、richtext/faq sanitize）→ Task 1（isSafeUrl + MdPreview）✓；§9 测试（Vitest + Playwright + 文档）→ Task 1-4 ✓；§10 阶段② → 本计划 ✓。

**Placeholder scan:** 无 TBD/TODO。Task 1 Step 4→5 明确把 `isSafeUrl` 从 SFC 内移到 `urlSafe.ts`（SFC `<script setup>` 不能命名导出）——是具体的实现修正，非占位。Playwright 步骤为人工验证清单，断言点具体。

**Type consistency:** `PanelBlock`（Task 1 定义）在 Task 2/3 一致使用；`KefuBlockFaq` props `{items,config}` + emits `click/escalate` 与 Task 2 `KefuServiceColumn` 调用一致；`KefuServiceColumn` emits `faqClick/escalate` 与 Task 3 `@faqClick=onFaqClick @escalate=onEscalate` 一致；`isSafeUrl` 从 `./urlSafe` 单一来源导入。

---

## 后续（Stage ② 之后）
- 逐字流式对话（消费 `message.part.updated`）。
- 可嵌入悬浮 widget。
- 主题色 `branding.themeColor` 深度应用（本期气泡用 primary，后续可读 branding）。
