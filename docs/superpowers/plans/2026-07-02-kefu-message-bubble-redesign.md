# 客服访客页 消息气泡重新设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把客服访客聊天页的无样式消息重构为「双向气泡」消费级客服风（访客右/主色气泡，客服左/浅色气泡+首字头像+时间戳）。

**Architecture:** 抽出纯展示组件 `KefuMessageBubble.vue` 承担单条消息渲染（头像/气泡/时间戳/附件 chip），`KefuChatPage.vue` 只负责循环传参并统一欢迎语/输入动画风格。发送/上传/SSE/失败恢复逻辑零改动。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + Element Plus CSS 变量 + Vitest（jsdom）+ `@vue/test-utils`。

## Global Constraints

- 所有颜色用 Element Plus CSS 变量（`--el-color-primary` / `--el-fill-color-light` / `--el-text-color-primary` / `--el-text-color-secondary` 等），自动适配深浅主题。
- `KefuMessage` 类型：`{ id: string; role: string; content: any; createdAt: string | null }`。`content` 是 part 数组（`{type:'text',text}` / `{type:'file',name,path}`），也可能是裸字符串 → 用 `normalize()` 容错。
- 访客消息 `role==='user'`；客服消息 `role==='assistant'`。
- 本地乐观消息 `createdAt===null` → 不显示时间戳。
- 前端测试命令：`npx vitest run <file>`；类型检查：`npm run build`（vue-tsc）。
- 不改后端、不加后端字段；头像色由 `agentName` 哈希派生，logo 复用 `branding.logo`。
- 提交仅在用户明确要求时进行——各任务的 "Commit" 步骤先写好命令，但**是否执行由用户在执行阶段决定**。

---

### Task 1: KefuMessageBubble 组件（TDD）

**Files:**
- Create: `src/components/kefu/KefuMessageBubble.vue`
- Test: `src/components/kefu/__tests__/KefuMessageBubble.test.ts`

**Interfaces:**
- Consumes: `KefuMessage`（from `@/api/kefuPublic`）、`MarkdownView`（from `@/components/ai-chat/MarkdownView.vue`）。
- Produces: 组件 `KefuMessageBubble`，props `{ message: KefuMessage; agentName?: string; agentLogo?: string }`。根元素 class：访客 `kmb kmb--user`，客服 `kmb kmb--agent`。客服头像元素 class `kmb__avatar`（有 logo 渲染 `<img>`，否则文本首字）。时间戳元素 class `kmb__time`。附件 chip class `kmb__file`。

- [ ] **Step 1: 写失败测试**

`src/components/kefu/__tests__/KefuMessageBubble.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuMessageBubble from '../KefuMessageBubble.vue'
import type { KefuMessage } from '@/api/kefuPublic'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

const stubs = { MarkdownView: { template: '<div class="md"><slot/>{{ text }}</div>', props: ['text'] } }
const msg = (o: Partial<KefuMessage>): KefuMessage =>
  ({ id: 'm', role: 'assistant', content: [], createdAt: null, ...o })

describe('KefuMessageBubble', () => {
  it('visitor message: user class, right side, no avatar', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'user', content: [{ type: 'text', text: '你好' }] }) },
      global: { stubs },
    })
    expect(w.classes()).toContain('kmb--user')
    expect(w.find('.kmb__avatar').exists()).toBe(false)
    expect(w.text()).toContain('你好')
  })

  it('agent message: avatar shows name initial, content via MarkdownView', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'assistant', content: [{ type: 'text', text: '**hi**' }] }), agentName: '客服小助手' },
      global: { stubs },
    })
    expect(w.classes()).toContain('kmb--agent')
    expect(w.find('.kmb__avatar').text()).toBe('客')
    expect(w.find('.md').exists()).toBe(true)
  })

  it('agent avatar uses logo image when provided', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'assistant', content: [] }), agentName: '客服', agentLogo: 'https://x/logo.png' },
      global: { stubs },
    })
    const img = w.find('.kmb__avatar img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('https://x/logo.png')
  })

  it('renders HH:mm when createdAt present, hides when null', () => {
    const iso = new Date(2026, 6, 2, 14, 32).toISOString()  // 本地 14:32 → 组件解析回本地
    const w1 = mount(KefuMessageBubble, { props: { message: msg({ createdAt: iso }) }, global: { stubs } })
    expect(w1.find('.kmb__time').text()).toBe('14:32')
    const w2 = mount(KefuMessageBubble, { props: { message: msg({ createdAt: null }) }, global: { stubs } })
    expect(w2.find('.kmb__time').exists()).toBe(false)
  })

  it('renders file chips for file parts', () => {
    const w = mount(KefuMessageBubble, {
      props: { message: msg({ role: 'user', content: [{ type: 'text', text: '看这个' }, { type: 'file', name: 'a.txt', path: 'uploads/a.txt' }] }) },
      global: { stubs },
    })
    const chips = w.findAll('.kmb__file')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('a.txt')
    expect(w.text()).toContain('看这个')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuMessageBubble.test.ts`
Expected: FAIL — `Failed to resolve import "../KefuMessageBubble.vue"`（组件尚不存在）。

- [ ] **Step 3: 写组件实现**

`src/components/kefu/KefuMessageBubble.vue`:

```vue
<!-- src/components/kefu/KefuMessageBubble.vue -->
<template>
  <div class="kmb" :class="isUser ? 'kmb--user' : 'kmb--agent'">
    <div v-if="!isUser" class="kmb__avatar" :style="avatarStyle">
      <img v-if="agentLogo" :src="agentLogo" alt="" />
      <span v-else>{{ initial }}</span>
    </div>
    <div class="kmb__col">
      <div class="kmb__bubble">
        <MarkdownView v-if="!isUser" :text="text" />
        <span v-else-if="text" class="kmb__text">{{ text }}</span>
        <span v-for="(f, i) in files" :key="i" class="kmb__file">📎 {{ f.name }}</span>
      </div>
      <div v-if="time" class="kmb__time">{{ time }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import type { KefuMessage } from '@/api/kefuPublic'

const props = defineProps<{ message: KefuMessage; agentName?: string; agentLogo?: string }>()

const AVATAR_COLORS = ['#5b8def', '#e6795e', '#42b883', '#b06ab3', '#e0913a', '#3aa5c2']

function normalize(content: any) {
  return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }]
}

const isUser = computed(() => props.message.role === 'user')
const text = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join(''))
const files = computed(() =>
  normalize(props.message.content).filter((p: any) => p.type === 'file'))
const initial = computed(() => ((props.agentName || '客服').trim().charAt(0) || '客'))
const avatarStyle = computed(() => {
  const name = props.agentName || '客服'
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return { background: AVATAR_COLORS[h % AVATAR_COLORS.length] }
})
const time = computed(() => {
  if (!props.message.createdAt) return ''
  const d = new Date(props.message.createdAt)
  if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})
</script>

<style scoped>
.kmb { display: flex; gap: 8px; margin-bottom: 16px; align-items: flex-start; }
.kmb--user { flex-direction: row-reverse; }
.kmb__avatar {
  flex-shrink: 0; width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 15px; font-weight: 600; overflow: hidden;
}
.kmb__avatar img { width: 100%; height: 100%; object-fit: cover; }
.kmb__col { display: flex; flex-direction: column; max-width: 76%; }
.kmb--user .kmb__col { align-items: flex-end; }
.kmb__bubble {
  padding: 8px 12px; border-radius: 12px; line-height: 1.5;
  word-break: break-word; overflow-wrap: anywhere;
}
.kmb--agent .kmb__bubble {
  background: var(--el-fill-color-light, #f4f4f5);
  color: var(--el-text-color-primary, #303133);
  border-top-left-radius: 4px;
}
.kmb--user .kmb__bubble {
  background: var(--el-color-primary, #409eff);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.kmb__file {
  display: inline-block; margin: 4px 4px 0 0; padding: 2px 8px;
  border-radius: 10px; font-size: 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border: 1px solid var(--el-color-primary-light-7, #c6e2ff);
}
.kmb--user .kmb__file {
  background: rgba(255, 255, 255, 0.22); border-color: rgba(255, 255, 255, 0.35); color: #fff;
}
.kmb__time { margin-top: 4px; font-size: 11px; color: var(--el-text-color-secondary, #909399); }
/* MarkdownView 首末段去掉多余外边距，贴合气泡 */
.kmb--agent .kmb__bubble :deep(p:first-child) { margin-top: 0; }
.kmb--agent .kmb__bubble :deep(p:last-child) { margin-bottom: 0; }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run src/components/kefu/__tests__/KefuMessageBubble.test.ts`
Expected: PASS（5 个用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add src/components/kefu/KefuMessageBubble.vue src/components/kefu/__tests__/KefuMessageBubble.test.ts
git commit -m "feat(kefu): 访客页消息气泡组件 KefuMessageBubble（双向气泡/头像/时间戳/附件）"
```

---

### Task 2: 接入 KefuChatPage + 统一欢迎语/输入动画

**Files:**
- Modify: `src/views/kefu/KefuChatPage.vue`（消息循环 17-23、helpers 76-78、`sending` 提示 24、欢迎语 11-16、`defineExpose` 151）
- Test: `src/views/kefu/__tests__/KefuChatPage.test.ts`（现有，应保持全绿）

**Interfaces:**
- Consumes: Task 1 的 `KefuMessageBubble`（props `{ message, agentName, agentLogo }`）。
- Produces: `KefuChatPage` 使用 bubble 组件渲染消息；`normalize`/`plainText`/`fileParts` 从本文件移除；`defineExpose` 去掉 `fileParts`。

- [ ] **Step 1: 替换消息循环模板**

在 `src/views/kefu/KefuChatPage.vue` 把 17-23 行的整个消息 `v-for` 块：

```html
          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
            <template v-else>
              <span v-if="plainText(m.content)" class="user-text">{{ plainText(m.content) }}</span>
              <span v-for="(f,i) in fileParts(m.content)" :key="i" class="file-chip">📎 {{ f.name }}</span>
            </template>
          </div>
```

替换为：

```html
          <KefuMessageBubble
            v-for="m in messages" :key="m.id"
            :message="m"
            :agent-name="config?.name || '在线客服'"
            :agent-logo="config?.branding?.logo" />
```

- [ ] **Step 1b: 欢迎语改用气泡组件渲染（带头像）**

把 11-16 行欢迎语块里的 `MarkdownView` 换成 `KefuMessageBubble`（喂一条合成的客服消息），引导问题 pill 保留不变。原块：

```html
          <div v-if="showWelcome" class="kefu-welcome">
            <MarkdownView v-if="config?.welcome_message" :text="config.welcome_message" />
            <div v-if="bubbles.length" class="bubbles">
              <button v-for="(b,i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
            </div>
          </div>
```

替换为：

```html
          <div v-if="showWelcome" class="kefu-welcome">
            <KefuMessageBubble
              v-if="config?.welcome_message"
              :message="{ id: 'welcome', role: 'assistant', content: [{ type: 'text', text: config.welcome_message }], createdAt: null }"
              :agent-name="config?.name || '在线客服'"
              :agent-logo="config?.branding?.logo" />
            <div v-if="bubbles.length" class="bubbles">
              <button v-for="(b,i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
            </div>
          </div>
```

这样 `MarkdownView` 在本页不再被使用（Step 3 可安全删除其 import）。

- [ ] **Step 2: 替换「正在输入」为三点动画气泡**

把 24 行：

```html
          <div v-if="sending" class="typing">正在输入…</div>
```

替换为：

```html
          <div v-if="sending" class="typing-row">
            <span class="typing-bubble"><i></i><i></i><i></i></span>
          </div>
```

- [ ] **Step 3: 导入组件、移除迁走的 helpers、清理 expose**

在 `<script setup>` 顶部 import 区，把：

```typescript
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
```

改为（`MarkdownView` 不再在本页直接用，删掉它，新增 bubble 组件 import）：

```typescript
import KefuMessageBubble from '@/components/kefu/KefuMessageBubble.vue'
```

删除 76-78 行三个已迁走的纯函数：

```typescript
function normalize(content: any) { return Array.isArray(content) ? content : [{ type: 'text', text: String(content ?? '') }] }
function plainText(content: any) { return normalize(content).filter((p: any) => p.type === 'text').map((p: any) => p.text).join('') }
function fileParts(content: any) { return normalize(content).filter((p: any) => p.type === 'file') }
```

把 151 行 `defineExpose` 里的 `fileParts` 去掉：

```typescript
defineExpose({ sessionId, onEscalate, messages, sending, askBubble, blocks, bubbles, pending, onPickFiles, send, draft })
```

- [ ] **Step 4: 欢迎语气泡化 + 三点动画样式**

在 `<style scoped>` 里追加（`.msg`/`.user-text` 等旧类本就无定义，无需删除；`.typing` 旧类也无定义）：

```css
.kefu-welcome { margin-bottom: 16px; }
.typing-row { display: flex; margin-bottom: 16px; }
.typing-bubble {
  display: inline-flex; gap: 4px; padding: 10px 12px; border-radius: 12px;
  border-top-left-radius: 4px; background: var(--el-fill-color-light, #f4f4f5);
}
.typing-bubble i {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--el-text-color-secondary, #909399);
  animation: kmb-blink 1.2s infinite ease-in-out both;
}
.typing-bubble i:nth-child(2) { animation-delay: 0.2s; }
.typing-bubble i:nth-child(3) { animation-delay: 0.4s; }
@keyframes kmb-blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
```

- [ ] **Step 5: 运行现有页面测试 + 类型检查**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: PASS（8 个用例，含上一任务的失败恢复用例；断言基于 `defineExpose` 方法，模板重构不影响）。

Run: `npm run build`
Expected: vue-tsc 无错误（确认删掉 `MarkdownView`/`fileParts` 后无遗留引用、`KefuMessageBubble` 类型正确）。

- [ ] **Step 6: Commit**

```bash
git add src/views/kefu/KefuChatPage.vue
git commit -m "feat(kefu): 访客页接入气泡组件 + 三点输入动画 + 欢迎语气泡化"
```

---

### Task 3: 文档同步 + Playwright 实机验证

**Files:**
- Modify: 客服相关用户指南（定位命令见下）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 定位并更新用户指南**

Run: `ls docs/user-guide/**/ | grep -i kefu` 或 `grep -rl "客服\|kefu" docs/user-guide/`
在命中的客服访客页文档里补充「消息气泡外观」说明（访客右/客服左、头像、时间戳），若涉及界面配一张新截图。若无对应文档，在 `docs/user-guide/` 合适子目录新建并从 `docs/user-guide/README.md` 链接。

- [ ] **Step 2: 确认服务在运行**

前端 `http://localhost:5173`、后端 `http://localhost:3002` 应已在跑（本会话已启动 `npm run dev:all`）。若未运行则重启。前端改动由 Vite HMR 自动生效。

- [ ] **Step 3: Playwright 走真实对话流**

（需先重连 Playwright MCP。）用 Playwright 打开 `http://localhost:5173/kefu/demo`：
1. 等待会话建立、欢迎语气泡显示。
2. 输入一句话点发送 → 断言出现**右对齐主色**访客气泡。
3. 等待客服 SSE 回复 → 断言出现**左对齐浅色 + 头像**客服气泡；发送中出现三点动画。
4. 上传一个附件并发送 → 断言访客气泡内出现 `📎 文件名` chip。
5. 断言气泡下方显示 `HH:mm` 时间戳（后端已落库的消息）。

- [ ] **Step 4: 截图存档**

把关键结果截图保存到 `.playwright-mcp/kefu-bubble-redesign.png`，并在总结里写明验证了什么。

- [ ] **Step 5: Commit（文档）**

```bash
git add docs/user-guide
git commit -m "docs(kefu): 访客页消息气泡外观说明 + 截图"
```

---

## Self-Review

**Spec coverage:**
- 双向气泡视觉 → Task 1（组件 + 样式）✓
- 客服头像首字/logo → Task 1 Step 3 `avatarStyle`/`initial`/`img` ✓
- 时间戳 `HH:mm`、乐观消息不显 → Task 1 `time` computed + 测试 ✓
- 附件 chip → Task 1 `files` + `.kmb__file`（含主色气泡可读性覆盖）✓
- 接入 `KefuChatPage`、移除迁走 helpers、清 `defineExpose` → Task 2 ✓
- 欢迎语气泡化 + 三点输入动画 → Task 2 Step 2/4 ✓
- 发送/上传/SSE/失败恢复零改动 → 未触碰 `send()`/`onFileChange`/SSE，仅换展示层 ✓
- 新增 bubble 单测 + 现有页面测试保持绿 → Task 1/Task 2 Step 5 ✓
- 文档同步 + Playwright 验证 → Task 3 ✓

**Placeholder scan:** 无 TBD/TODO；所有 code step 均含完整代码；测试含具体断言。✓

**Type consistency:** props `{ message, agentName?, agentLogo? }` 在 Task 1 定义、Task 2 以 `:message`/`:agent-name`/`:agent-logo` 传入一致；class 名 `kmb--user`/`kmb--agent`/`kmb__avatar`/`kmb__time`/`kmb__file` 在组件与测试间一致；`KefuMessage` 类型与 `@/api/kefuPublic` 一致。✓
