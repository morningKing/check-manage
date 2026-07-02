# 客服访客页 整页视觉重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/kefu/:slug` 访客页从无样式骨架重构为「现代 SaaS 居中悬浮卡片 + 柔和品牌蓝」的整页视觉，纯展示层、零业务逻辑改动。

**Architecture:** 抽出头像纯函数 `avatar.ts` 与受控组件 `KefuComposer.vue`；`KefuChatPage.vue` 重做外壳/顶栏/消息区/编排/响应式/状态并在根作用域定义品牌蓝变量；`KefuMessageBubble.vue` 访客气泡改用该变量；`KefuServiceColumn.vue` 右列卡片化。发送/上传/SSE/失败恢复逻辑保持不变。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + Element Plus（`@element-plus/icons-vue` 的 `Paperclip`/`Promotion`）+ Vitest（jsdom）+ `@vue/test-utils`。

## Global Constraints

- 强调色**仅作用于 `.kefu-page`**：在其根定义 `--kefu-accent:#4f6ef2`、`--kefu-accent-hover:#3f5fe0`、`--kefu-accent-soft:#eef1fe`、`--kefu-accent-contrast:#ffffff`。**不得修改全局 `--el-color-primary`**（近黑 `#1a1d21`），不得让蓝色泄漏到 `.kefu-page` 之外。
- 其余颜色用 Element Plus 主题变量（`--el-bg-color` / `--el-bg-color-page` / `--el-border-color*` / `--el-text-color*` / `--el-fill-color*`），自动适配主题。
- **零业务逻辑改动**：`send()` 的空判与失败恢复、`onPickFiles`、SSE（`createKefuEventStream`/`reload`/`onIdle`/`onError`）、FAQ 埋点 `onFaqClick`、`askBubble`/`onEscalate` 全部保持行为不变。
- `KefuMessage` 类型：`{ id: string; role: string; content: any; createdAt: string | null }`。访客 `role==='user'`，客服 `role==='assistant'`。
- Enter 发送必须 IME 安全：`e.isComposing` 或 `e.shiftKey` 时不发送。
- 前端测试：`npx vitest run <file>`（**repo 根目录**运行，不是 `server/`）；类型检查/构建：`npm run build`（vue-tsc）。
- 提交仅在执行阶段按流程进行；各任务「Commit」步骤写好命令，`git add` 只加该任务的确切文件，禁止 `git add -A`。

---

### Task 1: 头像工具 `avatar.ts` + 气泡改用它 + 访客气泡强调色

**Files:**
- Create: `src/components/kefu/avatar.ts`
- Create: `src/components/kefu/__tests__/avatar.test.ts`
- Modify: `src/components/kefu/KefuMessageBubble.vue`（`AVATAR_COLORS`+`initial`+`avatarStyle` 改为从 `avatar.ts` 导入；`.kmb--user .kmb__bubble` 背景改 `--kefu-accent`）

**Interfaces:**
- Produces: `avatarInitial(name?: string): string`、`avatarColor(name?: string): string`、`AVATAR_COLORS: string[]`（from `@/components/kefu/avatar`）。

- [ ] **Step 1: 写 avatar.ts 的失败测试**

`src/components/kefu/__tests__/avatar.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { avatarInitial, avatarColor, AVATAR_COLORS } from '../avatar'

describe('avatarInitial', () => {
  it('returns first char of name', () => {
    expect(avatarInitial('演示客服')).toBe('演')
    expect(avatarInitial('Acme')).toBe('A')
  })
  it('falls back to 客 when empty/undefined', () => {
    expect(avatarInitial('')).toBe('客')
    expect(avatarInitial(undefined)).toBe('客')
    expect(avatarInitial('   ')).toBe('客')
  })
})

describe('avatarColor', () => {
  it('returns a palette color', () => {
    expect(AVATAR_COLORS).toContain(avatarColor('演示客服'))
  })
  it('is stable for the same name', () => {
    expect(avatarColor('演示客服')).toBe(avatarColor('演示客服'))
  })
  it('falls back deterministically for empty name', () => {
    expect(AVATAR_COLORS).toContain(avatarColor(''))
    expect(avatarColor('')).toBe(avatarColor(undefined))
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/avatar.test.ts`
Expected: FAIL — `Failed to resolve import "../avatar"`.

- [ ] **Step 3: 写 avatar.ts**

`src/components/kefu/avatar.ts`:

```typescript
// Intentional theme-invariant identity palette (per-agent-name color, like
// Slack/Gmail avatars — stays constant across light/dark).
export const AVATAR_COLORS = ['#5b8def', '#e6795e', '#42b883', '#b06ab3', '#e0913a', '#3aa5c2']

export function avatarInitial(name?: string): string {
  return (name || '客服').trim().charAt(0) || '客'
}

export function avatarColor(name?: string): string {
  const n = name || '客服'
  let h = 0
  for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/kefu/__tests__/avatar.test.ts`
Expected: PASS（6 用例）。

- [ ] **Step 5: 重构 KefuMessageBubble 使用 avatar.ts + 访客气泡改强调色**

在 `src/components/kefu/KefuMessageBubble.vue` 的 `<script setup>`：删除本地 `AVATAR_COLORS` 常量与其注释行；`import` 段新增：

```typescript
import { avatarInitial, avatarColor } from './avatar'
```

把 `initial`/`avatarStyle` 两个 computed 改为：

```typescript
const initial = computed(() => avatarInitial(props.agentName))
const avatarStyle = computed(() => ({ background: avatarColor(props.agentName) }))
```

在 `<style scoped>` 把访客气泡背景改为品牌蓝变量（其余不变）：

```css
.kmb--user .kmb__bubble {
  background: var(--kefu-accent, #4f6ef2);
  color: var(--el-color-white, #fff);
  border-bottom-right-radius: 4px;
}
```

- [ ] **Step 6: 运行气泡测试确认仍 5/5**

Run: `npx vitest run src/components/kefu/__tests__/KefuMessageBubble.test.ts`
Expected: PASS 5/5（行为不变：首字仍「演」、logo/时间/附件断言不受影响）。

- [ ] **Step 7: Commit**

```bash
git add src/components/kefu/avatar.ts src/components/kefu/__tests__/avatar.test.ts src/components/kefu/KefuMessageBubble.vue
git commit -m "refactor(kefu): 抽出头像工具 avatar.ts + 访客气泡改用品牌蓝 --kefu-accent"
```

---

### Task 2: 受控输入组件 `KefuComposer.vue`

**Files:**
- Create: `src/components/kefu/KefuComposer.vue`
- Create: `src/components/kefu/__tests__/KefuComposer.test.ts`

**Interfaces:**
- Produces: 组件 `KefuComposer`，Props `{ draft: string; pending: { name: string; path: string }[]; sending: boolean }`；Emits `update:draft(value)`、`pickFiles(files: File[])`、`removePending(index)`、`send()`。根 class `.composer`；发送按钮 `.composer__send`；附件按钮 `.composer__attach`；文件 input `input[type=file].composer__file`；待发 chip `.composer__chip`。

- [ ] **Step 1: 写失败测试**

`src/components/kefu/__tests__/KefuComposer.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuComposer from '../KefuComposer.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })

const stubs = {
  'el-input': {
    template: `<textarea class="ci" :value="modelValue"
      @input="$emit('update:modelValue',$event.target.value)"
      @keydown="$emit('keydown',$event)" @focus="$emit('focus')" @blur="$emit('blur')" />`,
    props: ['modelValue'],
  },
  'el-icon': { template: '<i><slot/></i>' },
}
const mountC = (props: any = {}) =>
  mount(KefuComposer, { props: { draft: '', pending: [], sending: false, ...props }, global: { stubs } })

describe('KefuComposer', () => {
  it('emits update:draft on input', async () => {
    const w = mountC()
    await w.find('textarea.ci').setValue('你好')
    expect(w.emitted('update:draft')![0]).toEqual(['你好'])
  })

  it('Enter (no shift, not composing) emits send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter')
    expect(w.emitted('send')).toBeTruthy()
  })

  it('Shift+Enter does NOT emit send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter', { shiftKey: true })
    expect(w.emitted('send')).toBeFalsy()
  })

  it('IME composing Enter does NOT emit send', async () => {
    const w = mountC({ draft: '你好' })
    await w.find('textarea.ci').trigger('keydown.enter', { isComposing: true })
    expect(w.emitted('send')).toBeFalsy()
  })

  it('Enter with empty draft does NOT emit send', async () => {
    const w = mountC({ draft: '   ' })
    await w.find('textarea.ci').trigger('keydown.enter')
    expect(w.emitted('send')).toBeFalsy()
  })

  it('file pick emits pickFiles with File[]', async () => {
    const w = mountC()
    const input = w.find('input[type=file]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [new File(['x'], 'a.txt')] })
    await w.find('input[type=file]').trigger('change')
    expect(w.emitted('pickFiles')![0][0]).toHaveLength(1)
  })

  it('renders pending chips and ✕ emits removePending', async () => {
    const w = mountC({ pending: [{ name: 'a.txt', path: 'uploads/a.txt' }] })
    const chip = w.find('.composer__chip')
    expect(chip.text()).toContain('a.txt')
    await chip.find('b').trigger('click')
    expect(w.emitted('removePending')![0]).toEqual([0])
  })

  it('send button disabled when empty or sending', () => {
    expect(mountC({ draft: '' }).find('.composer__send').attributes('disabled')).toBeDefined()
    expect(mountC({ draft: 'x', sending: true }).find('.composer__send').attributes('disabled')).toBeDefined()
    expect(mountC({ draft: 'x' }).find('.composer__send').attributes('disabled')).toBeUndefined()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuComposer.test.ts`
Expected: FAIL — `Failed to resolve import "../KefuComposer.vue"`.

- [ ] **Step 3: 写组件**

`src/components/kefu/KefuComposer.vue`:

```vue
<!-- src/components/kefu/KefuComposer.vue -->
<template>
  <div class="composer">
    <div v-if="pending.length" class="composer__pending">
      <span v-for="(p, i) in pending" :key="i" class="composer__chip">
        📎 {{ p.name }} <b @click="emit('removePending', i)">✕</b>
      </span>
    </div>
    <div class="composer__row" :class="{ 'is-focused': focused }">
      <button class="composer__attach" type="button" title="上传文件" @click="fileInput?.click()">
        <el-icon><Paperclip /></el-icon>
      </button>
      <input ref="fileInput" type="file" multiple class="composer__file" @change="onFileChange" />
      <el-input
        class="composer__input"
        :model-value="draft"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 5 }"
        resize="none"
        placeholder="输入你的问题…"
        @update:model-value="emit('update:draft', $event)"
        @keydown.enter="onEnter"
        @focus="focused = true"
        @blur="focused = false" />
      <button class="composer__send" type="button" :disabled="!canSend" title="发送" @click="emit('send')">
        <el-icon><Promotion /></el-icon>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Paperclip, Promotion } from '@element-plus/icons-vue'

const props = defineProps<{ draft: string; pending: { name: string; path: string }[]; sending: boolean }>()
const emit = defineEmits<{
  (e: 'update:draft', value: string): void
  (e: 'pickFiles', files: File[]): void
  (e: 'removePending', index: number): void
  (e: 'send'): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const focused = ref(false)
const canSend = computed(() => (props.draft.trim().length > 0 || props.pending.length > 0) && !props.sending)

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length) emit('pickFiles', Array.from(input.files))
  input.value = ''
}

function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return          // IME candidate selection — not a submit
  if (e.shiftKey) return             // Shift+Enter = newline
  e.preventDefault()
  if (!canSend.value) return
  emit('send')
}
</script>

<style scoped>
.composer { padding: 12px 16px 16px; }
.composer__pending { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.composer__chip {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
  background: var(--el-fill-color-light, #f5f7fa); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 12px; padding: 2px 8px; color: var(--el-text-color-regular, #606266);
}
.composer__chip b { cursor: pointer; font-weight: normal; color: var(--el-text-color-secondary, #909399); }
.composer__chip b:hover { color: var(--el-color-danger, #f56c6c); }
.composer__row {
  display: flex; align-items: flex-end; gap: 8px;
  border: 1px solid var(--el-border-color, #dcdfe6); border-radius: 14px;
  padding: 6px 6px 6px 10px; background: var(--el-bg-color, #fff);
  transition: border-color .15s ease, box-shadow .15s ease;
}
.composer__row.is-focused {
  border-color: var(--kefu-accent, #4f6ef2);
  box-shadow: 0 0 0 3px var(--kefu-accent-soft, #eef1fe);
}
.composer__file { display: none; }
.composer__attach {
  flex-shrink: 0; width: 34px; height: 34px; border-radius: 9px; cursor: pointer;
  background: none; border: none; color: var(--el-text-color-secondary, #909399);
  display: inline-flex; align-items: center; justify-content: center; font-size: 18px;
}
.composer__attach:hover { background: var(--el-fill-color-light, #f5f7fa); color: var(--kefu-accent, #4f6ef2); }
.composer__input { flex: 1; }
/* el-input textarea: borderless, transparent, no inner shadow — the row is the frame */
.composer__input :deep(.el-textarea__inner) {
  box-shadow: none !important; background: transparent !important; padding: 6px 2px; resize: none;
}
.composer__send {
  flex-shrink: 0; width: 34px; height: 34px; border-radius: 50%; cursor: pointer; border: none;
  background: var(--kefu-accent, #4f6ef2); color: var(--kefu-accent-contrast, #fff);
  display: inline-flex; align-items: center; justify-content: center; font-size: 17px;
  transition: background .15s ease;
}
.composer__send:hover:not(:disabled) { background: var(--kefu-accent-hover, #3f5fe0); }
.composer__send:disabled { background: var(--el-fill-color-dark, #e6e8eb); color: var(--el-text-color-disabled, #c0c4cc); cursor: not-allowed; }
</style>
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/kefu/__tests__/KefuComposer.test.ts`
Expected: PASS（8 用例）。

- [ ] **Step 5: Commit**

```bash
git add src/components/kefu/KefuComposer.vue src/components/kefu/__tests__/KefuComposer.test.ts
git commit -m "feat(kefu): 受控输入组件 KefuComposer（附件/自增高/纸飞机发送/聚焦描边）"
```

---

### Task 3: `KefuChatPage.vue` 外壳/顶栏/消息区/编排/响应式/状态

**Files:**
- Modify: `src/views/kefu/KefuChatPage.vue`（整段 `<template>`、`<style>`、`<script>` 局部）
- Modify: `src/views/kefu/__tests__/KefuChatPage.test.ts`（stubs 加 `KefuComposer`/`KefuMessageBubble`）

**Interfaces:**
- Consumes: `KefuComposer`（Task 2）、`avatarInitial`/`avatarColor`（Task 1 的 `avatar.ts`）、`KefuMessageBubble`（现有）、`KefuServiceColumn`（现有）。
- Produces: 页面根 `.kefu-page`（定义 `--kefu-accent*` 变量）、`.kefu-card` 外壳。

- [ ] **Step 1: 替换 `<template>` 全段**

把 `src/views/kefu/KefuChatPage.vue` 的 `<template>…</template>`（第 2–51 行）整体替换为：

```html
<template>
  <div class="kefu-page">
    <div class="kefu-card" :class="{ 'is-plain': loading || loadError }">
      <div v-if="loading" class="kefu-skeleton">
        <div class="sk-header"><div class="sk-avatar" /><div class="sk-lines"><i /><i /></div></div>
        <div class="sk-body"><span /><span /><span /></div>
      </div>
      <div v-else-if="loadError" class="kefu-error">
        <div class="kefu-error__icon">💬</div>
        <p>客服暂不可用，请稍后再试</p>
      </div>
      <template v-else>
        <div class="kefu-main">
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
          <main class="kefu-messages" ref="scroller">
            <div v-if="showWelcome" class="kefu-welcome">
              <KefuMessageBubble
                v-if="config?.welcome_message"
                :message="{ id: 'welcome', role: 'assistant', content: [{ type: 'text', text: config.welcome_message }], createdAt: null }"
                :agent-name="config?.name || '在线客服'"
                :agent-logo="config?.branding?.logo" />
              <div v-if="bubbles.length" class="bubbles">
                <button v-for="(b, i) in bubbles" :key="i" class="bubble" @click="askBubble(b)">{{ b }}</button>
              </div>
            </div>
            <KefuMessageBubble
              v-for="m in messages" :key="m.id"
              :message="m"
              :agent-name="config?.name || '在线客服'"
              :agent-logo="config?.branding?.logo" />
            <div v-if="sending" class="typing-row" role="status" aria-label="正在输入">
              <span class="typing-bubble"><i></i><i></i><i></i></span>
            </div>
          </main>
          <KefuComposer
            :draft="draft" :pending="pending" :sending="sending"
            @update:draft="draft = $event"
            @pick-files="onPickFiles"
            @remove-pending="removePending"
            @send="send" />
        </div>
        <aside v-if="hasBlocks" class="kefu-column">
          <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalate" />
        </aside>
        <el-drawer v-if="hasBlocks" v-model="drawer" title="自助服务" direction="rtl" size="360px">
          <KefuServiceColumn :blocks="blocks" :faqItems="faq" @faqClick="onFaqClick" @escalate="onEscalateDrawer" />
        </el-drawer>
      </template>
    </div>
  </div>
</template>
```

- [ ] **Step 2: 调整 `<script setup>`**

在 import 段新增（`KefuComposer` + 头像工具）：

```typescript
import KefuComposer from '@/components/kefu/KefuComposer.vue'
import { avatarInitial, avatarColor } from '@/components/kefu/avatar'
```

删除已迁入 composer 的 `fileInput` ref、`onFileChange`、`onEnter` 三处（它们现在属于 `KefuComposer`）。具体删除：

```typescript
const fileInput = ref<HTMLInputElement | null>(null)
```
```typescript
async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) await onPickFiles(Array.from(input.files))
  input.value = ''
}
```
```typescript
function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return          // IME candidate selection — not a submit
  if (e.shiftKey) return             // Shift+Enter = newline
  e.preventDefault()
  send()
}
```

新增 `loading` 状态与顶栏头像 computed（放在其它 ref/computed 附近）：

```typescript
const loading = ref(true)
const headerAvatarInitial = computed(() => avatarInitial(config.value?.name))
const headerAvatarColor = computed(() => avatarColor(config.value?.name))
```

在 `onMounted` 的 `try/catch` 上补 `finally` 收起 loading（其余不动）：

```typescript
onMounted(async () => {
  try {
    config.value = await api.getKefuConfig(props.slug)
    const s = await api.createKefuSession(props.slug); sessionId.value = s.id
    faq.value = (await api.getKefuFaq(props.slug)).items
    await reload()
    closeStream = api.createKefuEventStream(sessionId.value, {
      onIdle: async () => { await reload(); sending.value = false },
      onError: () => { sending.value = false; ElMessage.error('客服暂时无法回复，请稍后重试') },
    })
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
})
```

`defineExpose` 不变（`onPickFiles`/`removePending`/`send`/`draft`/`pending`/`sending` 等仍暴露；`onEnter`/`onFileChange` 本就未暴露）。

- [ ] **Step 3: 替换 `<style scoped>` 全段**

把 `<style scoped>…</style>` 整体替换为：

```html
<style scoped>
.kefu-page {
  /* brand accent — SCOPED to this page only; does NOT touch global --el-color-primary */
  --kefu-accent: #4f6ef2;
  --kefu-accent-hover: #3f5fe0;
  --kefu-accent-soft: #eef1fe;
  --kefu-accent-contrast: #ffffff;
  height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--el-bg-color-page, #f5f7fa); padding: 24px; box-sizing: border-box;
}
.kefu-card {
  display: flex; width: min(1080px, 94vw); height: min(880px, 92vh);
  background: var(--el-bg-color, #fff); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 16px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.08); overflow: hidden;
}
.kefu-card.is-plain { align-items: center; justify-content: center; }
.kefu-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

/* header */
.kefu-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.kefu-header__id { display: flex; align-items: center; gap: 10px; }
.kefu-avatar {
  width: 40px; height: 40px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 16px; font-weight: 600;
}
.kefu-avatar img { width: 100%; height: 100%; object-fit: cover; }
.kefu-header__meta { display: flex; flex-direction: column; line-height: 1.3; }
.kefu-header__name { font-weight: 600; color: var(--el-text-color-primary, #303133); }
.kefu-header__status { font-size: 12px; color: var(--el-text-color-secondary, #909399); display: inline-flex; align-items: center; gap: 4px; }
.kefu-header__status .dot { width: 7px; height: 7px; border-radius: 50%; background: #22c55e; display: inline-block; }

/* messages */
.kefu-messages { flex: 1; overflow-y: auto; padding: 20px 24px; }
.kefu-messages::-webkit-scrollbar { width: 6px; }
.kefu-messages::-webkit-scrollbar-thumb { background: var(--el-border-color, #dcdfe6); border-radius: 3px; }
.kefu-welcome { margin-bottom: 16px; }
.kefu-welcome .bubbles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.bubble {
  border: 1px solid var(--kefu-accent-soft, #eef1fe); background: var(--kefu-accent-soft, #eef1fe);
  color: var(--kefu-accent, #4f6ef2); border-radius: 16px; padding: 6px 14px; cursor: pointer;
  font-size: 13px; transition: background .15s ease;
}
.bubble:hover { background: #e3e8fd; }

/* typing */
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

/* right column */
.kefu-column { width: 320px; border-left: 1px solid var(--el-border-color-lighter, #ebeef5); overflow-y: auto; padding: 16px; background: var(--el-bg-color-page, #f9fafb); }

/* skeleton + error */
.kefu-skeleton { width: 100%; max-width: 420px; padding: 24px; }
.kefu-skeleton .sk-header { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.kefu-skeleton .sk-avatar { width: 40px; height: 40px; border-radius: 50%; background: var(--el-fill-color, #f0f2f5); }
.kefu-skeleton .sk-lines { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.kefu-skeleton .sk-lines i { height: 10px; border-radius: 5px; background: var(--el-fill-color, #f0f2f5); }
.kefu-skeleton .sk-lines i:first-child { width: 40%; }
.kefu-skeleton .sk-lines i:last-child { width: 24%; }
.kefu-skeleton .sk-body { display: flex; flex-direction: column; gap: 12px; }
.kefu-skeleton .sk-body span { height: 44px; border-radius: 12px; background: var(--el-fill-color-light, #f5f7fa); }
.kefu-skeleton .sk-body span:nth-child(1) { width: 70%; }
.kefu-skeleton .sk-body span:nth-child(2) { width: 55%; align-self: flex-end; background: var(--kefu-accent-soft, #eef1fe); }
.kefu-skeleton .sk-body span:nth-child(3) { width: 60%; }
.kefu-error { text-align: center; color: var(--el-text-color-secondary, #909399); }
.kefu-error__icon { font-size: 40px; margin-bottom: 8px; }

/* responsive: full-bleed card + drawer under 992px */
.svc-toggle { display: none; }
@media (max-width: 991px) {
  .kefu-page { padding: 0; }
  .kefu-card { width: 100vw; height: 100vh; border-radius: 0; border: none; box-shadow: none; }
  .kefu-column { display: none; }
  .svc-toggle { display: inline-flex; }
}
</style>
```

- [ ] **Step 4: 更新页面测试 stubs（保持 8/8）**

在 `src/views/kefu/__tests__/KefuChatPage.test.ts` 的 `stubs` 对象里补两个极简 stub（与现有 `el-*` stub 并列），避免真实子组件图标/渲染副作用：

```typescript
  KefuComposer: { template: '<div class="composer-stub" />', props: ['draft', 'pending', 'sending'] },
  KefuMessageBubble: { template: '<div class="bubble-stub" />', props: ['message', 'agentName', 'agentLogo'] },
```

- [ ] **Step 5: 运行页面测试 + 构建**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: PASS 8/8（断言基于 `defineExpose` 的方法/状态，子组件已 stub、不受影响）。

Run: `npm run build`
Expected: vue-tsc 无错误（确认 `KefuComposer`/`avatar` 导入正确、删除 `onEnter`/`onFileChange`/`fileInput` 后无悬挂引用）。

- [ ] **Step 6: Commit**

```bash
git add src/views/kefu/KefuChatPage.vue src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客页外壳/顶栏/消息区/composer 集成/响应式/加载态（SaaS 卡片）"
```

---

### Task 4: 右侧自助区卡片化 `KefuServiceColumn.vue`

**Files:**
- Modify: `src/components/kefu/KefuServiceColumn.vue`（新增 `<style scoped>`，模板/逻辑不动）

**Interfaces:**
- Consumes: 无新增；沿用现有 props/emits。

- [ ] **Step 1: 给 KefuServiceColumn 加卡片样式**

在 `src/components/kefu/KefuServiceColumn.vue` 文件末尾（`</script>` 之后）新增 `<style scoped>` 块：

```html
<style scoped>
.kefu-service-column { display: flex; flex-direction: column; gap: 12px; }
.svc-block {
  background: var(--el-bg-color, #fff); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 12px; padding: 14px 16px;
}
.svc-title {
  margin: 0 0 10px; font-size: 13px; font-weight: 600;
  color: var(--el-text-color-secondary, #909399);
}
</style>
```

- [ ] **Step 2: 运行现有组件测试确认不回归**

Run: `npx vitest run src/components/kefu/__tests__/KefuServiceColumn.test.ts`
Expected: PASS（样式改动不影响结构/交互断言）。

- [ ] **Step 3: Commit**

```bash
git add src/components/kefu/KefuServiceColumn.vue
git commit -m "style(kefu): 右侧自助区块卡片化（白底/描边/圆角/统一间距）"
```

---

### Task 5: 文档同步 + Playwright 实机验证（桌面 + 移动）

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（§9.6 整页外观 + 新截图）
- Modify/Add: `docs/user-guide/_images/kefu-message-bubbles.png`（替换为整页图）

- [ ] **Step 1: 确认服务在运行**

前端 `http://localhost:5173`、后端 `http://localhost:3002`、MCP `:3003`、**OpenCode `:4096`**（客服会话创建依赖它；若未运行：`opencode serve --port 4096 --hostname 127.0.0.1`）。前端改动由 Vite HMR 生效。

- [ ] **Step 2: Playwright 桌面验证（≥992px）**

打开 `http://localhost:5173/kefu/demo`：
1. 断言居中卡片外壳（`.kefu-card` 存在、有圆角/阴影）、白底顶栏（头像 + 客服名 + ● 在线）。
2. 发一句 → 访客气泡为**品牌蓝**（校验 `.kmb--user .kmb__bubble` 背景 ≈ `rgb(79,110,242)`）、右对齐；等客服回复 → 白卡气泡 + 头像 + Markdown。
3. composer：聚焦输入框 → 容器高亮描边；发送按钮为**纸飞机图标圆钮**。
4. 上传附件 → 待发 chip → 发送 → 访客气泡含 `📎` chip。
5. 右列 4 个自助区块为卡片样式。
6. 截图存 `.playwright-mcp/kefu-page-desktop.png`。

- [ ] **Step 3: Playwright 移动验证（<992px）**

`browser_resize` 到 430×900：断言 `.kefu-card` 全屏（无圆角/阴影）、右列隐藏、顶栏出现「🗂 自助服务」按钮 → 点击 → 抽屉弹出含 4 区块。截图存 `.playwright-mcp/kefu-page-mobile.png`。

- [ ] **Step 4: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` §9.6：把「消息气泡外观」小节扩写为整页外观（居中卡片、白底顶栏+在线态、品牌蓝访客气泡、纸飞机 composer、右列卡片、移动全屏+抽屉）；用桌面截图替换/新增 `_images/` 图片引用。

- [ ] **Step 5: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md docs/user-guide/_images
git commit -m "docs(kefu): 访客页整页视觉说明 + 桌面/移动截图"
```

---

## Self-Review

**Spec coverage:**
- 强调色作用域变量 → Task 3 Step 3（`.kefu-page` 根定义）+ Task 1（气泡消费）✓
- 居中悬浮卡片外壳 + 响应式全屏/抽屉 → Task 3（`.kefu-card` + `@media`）✓
- 白底克制顶栏 + 头像 + 在线态 → Task 3 Step 1/3 + 头像工具 Task 1 ✓
- 组合输入框（附件/自增高/纸飞机/聚焦描边）→ Task 2 ✓
- 访客气泡改品牌蓝 → Task 1 Step 5 ✓
- 头像工具复用 `avatar.ts` → Task 1 ✓
- 右列卡片化 → Task 4 ✓
- 加载/错误/空状态 → Task 3（skeleton/error/welcome）✓
- 零逻辑改动（send/upload/SSE/失败恢复/FAQ）→ 未触碰这些函数体，仅迁移 `onEnter`/`onFileChange` 到 composer + 加 `loading` 展示态 ✓
- 新增 `KefuComposer.test.ts` + `avatar.test.ts`；现有测试保持绿（Task 3 Step 4 补 stub）✓
- Playwright 桌面 + 移动验证 + 文档 → Task 5 ✓

**Placeholder scan:** 无 TBD/TODO；每个 code step 含完整代码；测试含具体断言。✓

**Type consistency:**
- `avatarInitial(name?: string): string` / `avatarColor(name?: string): string` 在 Task 1 定义，Task 3 顶栏 computed 同签名调用 ✓
- `KefuComposer` props `{ draft, pending, sending }` 与 emits `update:draft/pickFiles/removePending/send` 在 Task 2 定义，Task 3 以 `:draft/:pending/:sending` + `@update:draft/@pick-files/@remove-pending/@send` 一致绑定 ✓
- class 名 `.composer__send`/`.composer__chip`/`.kmb--user` 等在组件与测试间一致 ✓
- `--kefu-accent*` 变量名在 Task 3 定义、Task 1/2 消费一致 ✓
