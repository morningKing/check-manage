# 对话框内联渲染图片/SVG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消息气泡里的图片类 `file` part（含 SVG）内联渲染为缩略图（可点开原图、失败回退 chip），而不是只显示文件名 chip。

**Architecture:** 纯前端。新增纯函数 `isImageFile`（按扩展名判断）+ 新组件 `ChatFile.vue`（图片 → `<img>`，否则/失败 → chip），AiChatView 用 `ChatFile` 替换现有 `file` part 的内联 chip。后端不动（下载接口已能服务 workspace 内任意文件；SVG 经 `<img>` 加载脚本被禁用，无 XSS）。

**Tech Stack:** Vue 3 `<script setup>` + TS + Element Plus + Vitest/@vue/test-utils。

参考 spec：`docs/superpowers/specs/2026-05-29-ai-chat-inline-image-svg-design.md`

**约定**：前端单测 `npx vitest run <file>`；构建 `npm run build`；全量 `npm test`。注意 `tsconfig` 的 `lib` 是 ES2020 —— 不要在新代码里用 `Array.prototype.at()` 等 ES2022 API（`npm run build` 会 type-check 测试文件）。

---

## File Structure

- `src/utils/artifacts.ts` — 新增并导出纯函数 `isImageFile(name)`（与既有 `extLang`/`isRenderableLang` 等文件类型辅助同处）。
- `src/components/ai-chat/ChatFile.vue` — 新组件，渲染单个 `file` part（图片内联 / 否则 chip / 失败回退）。
- `src/views/ai-chat/AiChatView.vue` — import `ChatFile`，把 `p.type === 'file'` 的内联 chip 换成 `<ChatFile>`。
- 测试：`src/utils/__tests__/artifacts.test.ts`（追加 `isImageFile`）、`src/components/ai-chat/__tests__/ChatFile.test.ts`（新）。

---

### Task 1: `isImageFile` 工具

**Files:**
- Modify: `src/utils/artifacts.ts`
- Test: `src/utils/__tests__/artifacts.test.ts`

- [ ] **Step 1: 写失败测试**

In `src/utils/__tests__/artifacts.test.ts`: add `isImageFile` to the existing `import { ... } from '@/utils/artifacts'` statement (or add a new import line if the existing import is awkward to extend), then append this block at the end of the file:

```ts
describe('isImageFile', () => {
  it('detects image extensions case-insensitively', () => {
    expect(isImageFile('a.svg')).toBe(true)
    expect(isImageFile('a.PNG')).toBe(true)
    expect(isImageFile('photo.jpeg')).toBe(true)
    expect(isImageFile('x.webp')).toBe(true)
    expect(isImageFile('y.gif')).toBe(true)
  })
  it('returns false for non-images and names without an extension', () => {
    expect(isImageFile('a.txt')).toBe(false)
    expect(isImageFile('script.py')).toBe(false)
    expect(isImageFile('noext')).toBe(false)
    expect(isImageFile('svg')).toBe(false)
  })
})
```

If `describe`/`it`/`expect` are not already imported in the file, add `import { describe, it, expect } from 'vitest'` at the top (check first — the file already uses them, so they're almost certainly imported).

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/utils/__tests__/artifacts.test.ts`
Expected: FAIL (`isImageFile` is not exported / not a function).

- [ ] **Step 3: 实现 `isImageFile`**

In `src/utils/artifacts.ts`, add (e.g. near the other file-type helpers like `isRenderableLang`):

```ts
const IMAGE_EXTS = new Set(['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'])

/** True if a filename looks like an inline-renderable image (by extension). */
export function isImageFile(name: string): boolean {
  const i = name.lastIndexOf('.')
  if (i < 0) return false
  return IMAGE_EXTS.has(name.slice(i + 1).trim().toLowerCase())
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/utils/__tests__/artifacts.test.ts`
Expected: PASS (all existing + the 2 new `isImageFile` tests).

- [ ] **Step 5: Commit**

```bash
git add src/utils/artifacts.ts src/utils/__tests__/artifacts.test.ts
git commit -m "feat(ai-chat): isImageFile helper for inline image detection"
```

---

### Task 2: `ChatFile.vue` 组件 + AiChatView 接线

**Files:**
- Create: `src/components/ai-chat/ChatFile.vue`
- Create: `src/components/ai-chat/__tests__/ChatFile.test.ts`
- Modify: `src/views/ai-chat/AiChatView.vue`

- [ ] **Step 1: 写失败测试**

Create `src/components/ai-chat/__tests__/ChatFile.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatFile from '@/components/ai-chat/ChatFile.vue'

const stubs = { ElIcon: { template: '<i><slot/></i>' }, Document: true }

describe('ChatFile', () => {
  it('renders an <img> for an image file (clickable to open full)', () => {
    const w = mount(ChatFile, {
      props: { name: 'diagram.svg', src: '/dl?path=diagram.svg' },
      global: { stubs },
    })
    const img = w.find('img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('/dl?path=diagram.svg')
    expect(w.find('a').attributes('href')).toBe('/dl?path=diagram.svg')
    expect(w.text()).toContain('diagram.svg')
  })

  it('renders a chip for a non-image file', () => {
    const w = mount(ChatFile, {
      props: { name: 'report.pdf', src: '/dl?path=report.pdf' },
      global: { stubs },
    })
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('.file-chip').exists()).toBe(true)
    expect(w.text()).toContain('report.pdf')
  })

  it('falls back to a chip when the image fails to load', async () => {
    const w = mount(ChatFile, {
      props: { name: 'broken.png', src: '/dl?path=broken.png' },
      global: { stubs },
    })
    expect(w.find('img').exists()).toBe(true)
    await w.find('img').trigger('error')
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('.file-chip').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/ChatFile.test.ts`
Expected: FAIL (cannot resolve `ChatFile.vue`).

- [ ] **Step 3: 实现组件**

Create `src/components/ai-chat/ChatFile.vue`:

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { isImageFile } from '@/utils/artifacts'

const props = defineProps<{ name: string; src: string }>()
const failed = ref(false)
const showImage = computed(() => isImageFile(props.name) && !failed.value)
</script>

<template>
  <a
    v-if="showImage"
    class="chat-file__img" :href="src" target="_blank" rel="noopener"
  >
    <img :src="src" :alt="name" @error="failed = true" />
    <span class="chat-file__caption">{{ name }}</span>
  </a>
  <div v-else class="file-chip">
    <ElIcon><Document /></ElIcon><span>{{ name }}</span>
  </div>
</template>

<style scoped lang="scss">
.chat-file__img {
  display: inline-block;
  max-width: 100%;
  margin: 2px 0 6px;
  text-decoration: none;
}
.chat-file__img img {
  display: block;
  max-width: 100%;
  max-height: 360px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}
.chat-file__caption {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  margin: 2px 4px 6px 0;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 13px;
}
</style>
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/ai-chat/__tests__/ChatFile.test.ts`
Expected: PASS (3 passed).

- [ ] **Step 5: 接线 AiChatView — import**

In `src/views/ai-chat/AiChatView.vue` `<script setup>`, add next to the other ai-chat component imports (e.g. after `import McpServicesBlock ...`):

```ts
import ChatFile from '@/components/ai-chat/ChatFile.vue'
```

- [ ] **Step 6: 接线 — 替换 file part 渲染**

In `src/views/ai-chat/AiChatView.vue`, replace this block (currently around lines 256-258):

```html
<div v-if="p.type === 'file'" class="file-chip">
  <ElIcon><Document /></ElIcon><span>{{ p.name }}</span>
</div>
```

with (keep it as the first `v-if` of the part-type chain):

```html
<ChatFile v-if="p.type === 'file'" :name="p.name" :src="fileUrl(p.path)" />
```

Note: `fileUrl` already exists in this file (`const fileUrl = (path) => downloadFileUrl(activeId.value || '', path)`). Leave the `.file-chip` style in `AiChatView.vue` as-is (it's also used by `.attach-chip` for pending attachments).

- [ ] **Step 7: 构建 (type-check)**

Run: `npm run build`
Expected: vue-tsc passes, vite build succeeds. If a pre-existing unrelated error surfaces, report it but do not fix unrelated code.

- [ ] **Step 8: 前端全量测试无回归**

Run: `npm test`
Expected: all previously-passing tests still pass, plus the new `isImageFile` (2) and `ChatFile` (3) tests.

- [ ] **Step 9: Commit**

```bash
git add src/components/ai-chat/ChatFile.vue \
        src/components/ai-chat/__tests__/ChatFile.test.ts \
        src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): inline-render image/SVG file parts in chat"
```

---

## 真机验证（全部任务完成后）

1. 重新构建前端 / 起 proxy。
2. 在 AI 助手让 agent 产出（或上传）一个 SVG 和一个 PNG。
3. 期望：消息气泡里直接显示图片（不再是文件名 chip），点击在新标签打开原图，下方有文件名。
4. 边界：非图片文件（如 .pdf/.csv）仍是 chip；路径失效的图片回退为 chip。

## Self-Review 备注

- **Spec 覆盖**：`isImageFile`(T1)、`ChatFile.vue` 图片/chip/失败回退(T2 步骤3)、AiChatView 接线(T2 步骤5-6)、安全（`<img>` 加载 SVG）—— 覆盖 spec 全部条目；测试覆盖 spec 测试清单（isImageFile 各扩展名 + 组件三态）。
- **类型一致**：`ChatFile` props `{ name: string; src: string }` 与 AiChatView 传参 `:name="p.name" :src="fileUrl(p.path)"` 一致；`isImageFile(name: string): boolean` 在组件与测试中签名一致。
- **YAGNI**：不动输入框待发送附件 chip、不动后端、不注入 SVG 标记。
