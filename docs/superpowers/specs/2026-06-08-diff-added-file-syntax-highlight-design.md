# 变更文件预览：新增文件加代码高亮

**Date:** 2026-06-08
**Status:** Approved (design)

## 问题背景

AI 助手「变更文件」面板点击「预览」打开 diff 抽屉（`FileDiffView`）。**新增文件**的内容当前以纯 `<pre class="diff-added-viewer">` 渲染，**没有代码高亮**（`src/components/ai-chat/FileDiffView.vue:36`）。用户希望新增文件内容有语法高亮，像制品预览那样。

项目已有 `md-editor-v3`（`MarkdownView` 使用它，自带多语言代码高亮）；`ArtifactPreview` 已用"把代码包成 ```` ``` ```` 代码块交给 MarkdownView"的方式获得高亮。

## 目标 / 非目标

- **目标**：FileDiffView 的**新增文件**内容用 `MarkdownView`（md-editor）渲染，获得多语言语法高亮，与制品预览一致。
- **非目标**：不改 modified 的并排红/绿 diff 视图（本次只处理新增文件）；deleted/null 分支不变；不引入新依赖（复用现有 md-editor）。

## 方案

### 1. `FileDiffView.vue` — added 分支改用 MarkdownView

- 新增可选 prop `filename?: string`，用于从扩展名推断语言：
  `lang = (filename.split('.').pop() || '').toLowerCase()`（如 `ts/js/py/json/vue/go/java/...` 即 md-editor 的语言名；未知扩展 → 空语言 fence，纯文本展示）。
- 把内容包成代码块交给 MarkdownView：
  `addedMarkdown = fence + lang + '\n' + content + '\n' + fence`
- **安全围栏**：`fence` 取比 `content` 中最长连续反引号串还长、且 ≥3 的反引号串，避免内容自身含 ```` ``` ```` 时围栏被提前闭合（比 ArtifactPreview 固定三反引号更稳）。实现：
  ```ts
  function safeFence(text: string): string {
    let longest = 0, run = 0
    for (const ch of text) { if (ch === '`') { run++; longest = Math.max(longest, run) } else run = 0 }
    return '`'.repeat(Math.max(3, longest + 1))
  }
  ```
- 模板：added 分支由
  ```vue
  <pre class="diff-added-viewer"><span v-for="(ln, i) in addedLines" ...>...</span></pre>
  ```
  改为
  ```vue
  <MarkdownView :text="addedMarkdown" />
  ```
- 移除不再使用的 `addedLines` computed 与 `.diff-added-viewer` / `.diff-added-line` 样式。`.diff-truncated`（截断提示）保留在下方不变。
- 导入：`import MarkdownView from './MarkdownView.vue'`。

### 2. `AiChatView.vue` — 传 filename

diff 抽屉里的 `<FileDiffView ... />` 增加 `:filename="diffFile"`（`diffFile` 已是被预览文件的路径）。

### 数据流（修复后）

```
点击新增文件「预览」 → getFileDiff → {status:'added', content, truncated}
FileDiffView(filename=路径): lang=扩展名 → addedMarkdown=安全围栏+lang+content
→ MarkdownView(md-editor) 渲染带高亮的代码块
```

## 边界 / 错误处理

- `filename` 缺失或无扩展名 → `lang=''` → 无语言代码块（纯文本，仍正常显示，不报错）。
- content 含 ```` ``` ```` → 安全围栏保证整体作为一个代码块渲染，不被截断。
- 截断文件：MarkdownView 渲染截断后的内容，下方 `.diff-truncated` 提示 + 抽屉里已有「下载完整文件」入口（AiChatView 提供）。
- 大文件：content 已在后端按 `MAX_DIFF_LINES=2000` 截断，md-editor 渲染量有界。

## 测试（`FileDiffView.test.ts`）

全局 stub `MarkdownView`（md-editor 在 jsdom 下偏重，且只需验证传入文本）：
```ts
const stubs = { MarkdownView: { props: ['text'], template: '<div class="md-stub">{{ text }}</div>' } }
```
- **新增文件高亮**：`mount(FileDiffView, { props: { status:'added', content:'export const x = 1', truncated:false, filename:'a.ts' }, global:{ stubs } })` → `.md-stub` 文本包含 ` ```ts ` 与 `export const x = 1`；`.diff-row` 不存在。
- **安全围栏**：content 含一行 ```` ``` ```` → `.md-stub` 文本里外层围栏为 ```` ```` ````（≥4 个反引号）。
- **无扩展名**：`filename` 省略或无点 → fence 后语言为空（` ```\n ` 开头）。
- 保留并适配现有用例：modified（`.diff-row` 三行 + del/add 类）、deleted 占位、null 占位、truncated 提示（这些分支不渲染 MarkdownView，stub 不影响）。

## 涉及文件

- 修改：`src/components/ai-chat/FileDiffView.vue`、`src/views/ai-chat/AiChatView.vue`、`src/components/ai-chat/__tests__/FileDiffView.test.ts`
