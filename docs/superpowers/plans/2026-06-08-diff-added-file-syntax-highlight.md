# 新增文件预览代码高亮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「变更文件」diff 抽屉里**新增文件**的内容带语法高亮（用 MarkdownView/md-editor 渲染为代码块）。

**Architecture:** `FileDiffView` 的 added 分支由纯 `<pre>` 改为 `<MarkdownView>`，把内容包成"安全围栏 + 扩展名语言"的代码块；`AiChatView` 给它传 `filename`。纯前端，无新依赖。

**Tech Stack:** Vue 3 + TypeScript，Vitest，md-editor-v3（经 MarkdownView）。

---

## File Structure

- `src/components/ai-chat/FileDiffView.vue`（改）— added 分支改用 MarkdownView + `safeFence` + 扩展名取语言。
- `src/views/ai-chat/AiChatView.vue`（改）— diff 抽屉给 FileDiffView 传 `:filename`。
- `src/components/ai-chat/__tests__/FileDiffView.test.ts`（改）— mock MarkdownView，验证传入文本。

---

## Task 1: FileDiffView added 分支改用 MarkdownView

**Files:**
- Modify: `src/components/ai-chat/FileDiffView.vue`
- Modify: `src/views/ai-chat/AiChatView.vue`
- Test: `src/components/ai-chat/__tests__/FileDiffView.test.ts`

- [ ] **Step 1: Rewrite the test** — replace the ENTIRE `src/components/ai-chat/__tests__/FileDiffView.test.ts` with:

```ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// MarkdownView wraps md-editor (heavy in jsdom). Mock the module so it never
// loads md-editor; the stub just echoes the `text` prop it receives so we can
// assert exactly what FileDiffView hands it.
vi.mock('../MarkdownView.vue', () => ({
  default: { props: ['text'], template: '<div class="md-stub">{{ text }}</div>' },
}))

import FileDiffView from '../FileDiffView.vue'

const DIFF = `--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line1
-line2
+CHANGED
 line3
`

describe('FileDiffView', () => {
  it('renders side-by-side change rows for a modified diff', () => {
    const w = mount(FileDiffView, { props: { status: 'modified', diff: DIFF, truncated: false } })
    expect(w.findAll('.diff-row').length).toBe(3)
    expect(w.find('.diff-cell--del').text()).toContain('line2')
    expect(w.find('.diff-cell--add').text()).toContain('CHANGED')
  })

  it('renders added-file content as a code block with language from the extension', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'export const x = 1', truncated: false, filename: 'a.ts' },
    })
    const md = w.find('.md-stub').text()
    expect(md).toContain('```ts')
    expect(md).toContain('export const x = 1')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('uses a longer fence when the content contains backticks', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'a\n```\nb', truncated: false, filename: 'x.md' },
    })
    // inner ``` (3) forces the outer fence to 4 backticks
    expect(w.find('.md-stub').text()).toContain('````md')
  })

  it('emits an empty-language fence when filename has no extension', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'plain', truncated: false, filename: 'README' },
    })
    expect(w.find('.md-stub').text()).toMatch(/^```\nplain\n```$/)
  })

  it('shows a truncation note when truncated', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'x', truncated: true, filename: 'x.txt' },
    })
    expect(w.find('.diff-truncated').exists()).toBe(true)
  })

  it('shows a deleted placeholder for deleted status', () => {
    const w = mount(FileDiffView, { props: { status: 'deleted', truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('已删除')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('shows a generic placeholder for null status', () => {
    const w = mount(FileDiffView, { props: { status: null, truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('没有差异')
  })
})
```

(Note: the old `.diff-added-viewer` / `.diff-added-line` tests are intentionally dropped — that DOM no longer exists once the added branch renders via MarkdownView.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/components/ai-chat/__tests__/FileDiffView.test.ts`
Expected: FAIL — the added-file tests look for `.md-stub` (FileDiffView still renders `.diff-added-viewer`, no MarkdownView).

- [ ] **Step 3: Rewrite `src/components/ai-chat/FileDiffView.vue`** to:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { parseUnifiedDiff } from '@/utils/unifiedDiff'
import MarkdownView from './MarkdownView.vue'

const props = defineProps<{
  status: 'added' | 'modified' | 'deleted' | null
  diff?: string
  content?: string
  truncated?: boolean
  filename?: string
}>()

const hunks = computed(() => (props.status === 'modified' ? parseUnifiedDiff(props.diff || '') : []))

// A fence longer than the longest backtick run in the content, so content that
// itself contains ``` can't close the block early.
function safeFence(text: string): string {
  let longest = 0
  let run = 0
  for (const ch of text) {
    if (ch === '`') { run++; if (run > longest) longest = run }
    else run = 0
  }
  return '`'.repeat(Math.max(3, longest + 1))
}

// Language for highlighting = the file extension (from the basename, so a dotted
// directory + extension-less file doesn't misfire). Empty when there's no ext.
const addedMarkdown = computed(() => {
  if (props.status !== 'added') return ''
  const content = props.content || ''
  const base = (props.filename || '').split(/[\\/]/).pop() || ''
  const lang = base.includes('.') ? (base.split('.').pop() || '').toLowerCase() : ''
  const fence = safeFence(content)
  return `${fence}${lang}\n${content}\n${fence}`
})
</script>

<template>
  <div class="file-diff">
    <!-- modified: side-by-side hunks -->
    <template v-if="status === 'modified'">
      <div v-if="!hunks.length" class="diff-empty">无文本差异</div>
      <div v-for="(h, hi) in hunks" :key="hi" class="diff-hunk">
        <div class="diff-hunk__header">{{ h.header }}</div>
        <div v-for="(row, ri) in h.rows" :key="ri" class="diff-row" :class="`diff-row--${row.type}`">
          <span class="diff-no">{{ row.leftNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--del': row.type === 'del' || row.type === 'change' }">{{ row.left ?? '' }}</span>
          <span class="diff-no">{{ row.rightNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--add': row.type === 'add' || row.type === 'change' }">{{ row.right ?? '' }}</span>
        </div>
      </div>
    </template>

    <!-- added: render content as a highlighted code block -->
    <template v-else-if="status === 'added'">
      <MarkdownView :text="addedMarkdown" />
    </template>

    <div v-else-if="status === 'deleted'" class="diff-empty">该文件已删除，无可预览内容</div>
    <div v-else class="diff-empty">该文件当前没有差异（可能已被还原或移除）</div>

    <div v-if="truncated" class="diff-truncated">内容过大，已截断；请下载查看完整内容。</div>
  </div>
</template>

<style scoped lang="scss">
.file-diff { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }
.diff-hunk { margin-bottom: 14px; border: 1px solid var(--el-border-color-light); border-radius: 6px; overflow: hidden; }
.diff-hunk__header { padding: 4px 10px; background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.diff-row { display: grid; grid-template-columns: 44px 1fr 44px 1fr; align-items: stretch; }
.diff-no { color: var(--el-text-color-secondary); text-align: right; padding: 0 6px; user-select: none; background: var(--el-fill-color-lighter); }
.diff-cell { padding: 0 8px; white-space: pre-wrap; word-break: break-word; }
.diff-cell--del { background: var(--el-color-danger-light-9); }
.diff-cell--add { background: var(--el-color-success-light-9); }
.diff-empty { color: var(--el-text-color-secondary); padding: 8px 0; }
.diff-truncated { margin-top: 10px; padding: 6px 10px; font-size: 12px; color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); border-radius: 6px; }
</style>
```

(Changes vs current: `+filename` prop, `+MarkdownView` import, `safeFence` + `addedMarkdown` replace `addedLines`, added template branch uses `<MarkdownView>`, and the `.diff-added-viewer`/`.diff-added-line` styles are removed.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/ai-chat/__tests__/FileDiffView.test.ts`
Expected: PASS (7).

- [ ] **Step 5: Pass `filename` from the diff drawer in `AiChatView.vue`**

In `src/views/ai-chat/AiChatView.vue`, find the diff-drawer `<FileDiffView ... />` (it has `v-else-if="diffData"` and binds `:status/:diff/:content/:truncated` from `diffData`). Add a `:filename` binding:
```vue
        <FileDiffView
          v-else-if="diffData"
          :status="diffData.status"
          :diff="diffData.diff"
          :content="diffData.content"
          :truncated="diffData.truncated"
          :filename="diffFile"
        />
```
(`diffFile` is the existing ref holding the previewed file's path — same one used in `:title` and the 下载 link.)

- [ ] **Step 6: Type-check + run the ai-chat suite**

Run: `npx vue-tsc --noEmit 2>&1 | grep -iE "FileDiffView|AiChatView" | head`
Expected: no NEW errors referencing `filename`, `addedMarkdown`, `safeFence`, or the changed lines.

Run: `npx vitest run src/components/ai-chat`
Expected: PASS (FileDiffView + the other ai-chat component suites stay green).

- [ ] **Step 7: Commit**

```bash
git add src/components/ai-chat/FileDiffView.vue src/views/ai-chat/AiChatView.vue src/components/ai-chat/__tests__/FileDiffView.test.ts
git commit -m "feat(ai-chat): syntax-highlight added-file diff preview via MarkdownView"
```

---

## Self-Review 结果

- **Spec 覆盖：** added 分支用 MarkdownView（Step 3 模板）；`filename` prop + 扩展名取语言（Step 3 `addedMarkdown`，basename 处理）；安全围栏（`safeFence`）；移除 `addedLines`/`.diff-added-viewer`（Step 3）；AiChatView 传 `filename`（Step 5）；测试 stub/mock MarkdownView + 高亮/围栏/无扩展名/截断/modified/deleted/null（Step 1）。modified 并排 diff 与 deleted/null 不变。全覆盖。
- **Placeholder 扫描：** 无 TBD/TODO；每步含完整代码与确切命令。
- **类型/命名一致：** `filename?: string`、`addedMarkdown`、`safeFence` 在组件与测试断言一致；`:filename="diffFile"` 与组件 prop 名一致；mock 路径 `../MarkdownView.vue` 与组件导入路径一致。
