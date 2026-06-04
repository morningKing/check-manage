# AI Chat 变更文件：Diff 视图 + 分组折叠

**Date:** 2026-06-04
**Status:** Approved (design)

## 问题背景

AI 助手会话的「变更文件」面板（`src/views/ai-chat/AiChatView.vue`）有两个体验问题：

1. **大文件预览卡顿**：点击「修改」文件的「预览」会 `fetch` 整个文件内容（`previewChange`，`AiChatView.vue:107`），再用 `MarkdownView`（md-editor）渲染**整份文件**。文件大时 md-editor 的渲染拖慢页面。
2. **删除文件挤满面板**：`changes` 是一个扁平列表（`AiChatView.vue:563-593`），新增/修改/删除按路径排序混在一起。删除文件多时会把面板撑满，且删除项没有可操作内容却仍占用整行。

后端 `server/utils/workspace_changes.py` 目前只通过 `git status --porcelain` 返回 `{path, status}`，**不含 diff 内容**。

## 目标

- 「修改」文件改用 **并排（side-by-side）diff 视图**，只传输/渲染变更的 hunk，消除卡顿。
- 「新增」大文件改用 **轻量代码查看器**（容量受限），不再走 md-editor 重渲染。
- 变更面板按状态 **分组 + 折叠**：新增 / 修改 / 删除三组，删除组默认折叠，仅列路径。

非目标：不改变 `git status` 扫描逻辑；不引入新的 diff 第三方库（用自写解析 + 纯 `<div>` 渲染）。

## 架构

### 1. 后端：新增 diff 端点

`server/utils/workspace_changes.py`：

- 重构现有的 git 仓库发现逻辑（`_find_git_repos`），新增 `resolve_repo_for_path(workspace_path, rel_path)`，把一个 workspace 相对路径映射回其所属 git 仓库 + 仓库内相对路径。复用现有的 nested-repo 处理思路。
- 新增 `file_diff(workspace_path, rel_path)`：
  - **modified**：`git -C <repo> diff -- <repo-rel-path>`，返回 unified diff 文本。
  - **added（untracked）**：读取文件内容，**容量上限**（如 2000 行 / 256 KB），超限设 `truncated=True`。
  - 输出统一为 `{ status, diff?, content?, truncated }`。

`server/routes/ai_chat.py`：新增路由

```
GET /ai/chat/sessions/:id/diff?path=<rel-path>
→ { status: 'modified'|'added'|'deleted', diff?: str, content?: str, truncated: bool }
```

- **路径安全**：`?path=` 必须经过与下载路由（`download_file`，`ai_chat.py:713`）相同的 path-traversal 校验，限定在 workspace 根目录内。越界返回 400/403。
- 大小上限在后端落实，保证单次响应不会因超大文件而过大。
- `deleted` 状态不提供 diff/内容（文件已不存在）。

### 2. 前端：并排 diff 渲染

**`src/components/ai-chat/FileDiffView.vue`**（新）：

- 工具函数 `parseUnifiedDiff(diff: string): DiffHunk[]`（放入 `src/utils/` 或组件内）：把 git unified 输出解析成 hunk，再把每个 hunk 对齐成 **左（旧）/ 右（新）** 行：
  - context 行：左右两侧相同。
  - `-` 行 vs `+` 行：按连续段逐行配对（第 1 个删除 ↔ 第 1 个新增），较短一侧补空白填充行。
  - 左侧删除行红色背景、右侧新增行绿色背景，各侧带行号。
- 渲染：纯 `<div>` 行 + monospace，**不经过 md-editor / 重型高亮**，大 diff 也快。

**新增文件查看器**：在同一抽屉里，新增文件用**容量受限的 `<pre>` 查看器**（带行号），按后端 `truncated` 截断；截断时显示「文件过大，下载查看完整内容」+ 下载按钮。可作为右列「全绿」呈现，或独立 `<pre>`，以实现简单为准。

**抽屉接入**（`AiChatView.vue` 的 `previewChange` / `ElDrawer`）：

- `modified` → `fetch` `/diff`，渲染 `FileDiffView`。
- `added` → `fetch` 截断内容，渲染轻量查看器。
- `deleted` → 不提供预览。

### 3. 变更文件面板：分组 + 折叠

把扁平 `changes` 列表按 `status` 分成 3 个**可折叠分组**，组标题带数量徽标：

| 分组 | 默认状态 | 内容 |
|------|----------|------|
| 新增 (n) | 展开 | 现有行 UI（预览 / 下载） |
| 修改 (n) | 展开 | 行 UI，「预览」打开 diff 视图 |
| 删除 (n) | **折叠** | 仅路径列表，无预览/下载（文件已删除） |

- 折叠状态用本地 `ref` 开关（轻量，不用 el-collapse）。
- 保留现有 🔄 重新扫描按钮。
- 空分组不渲染对应区块。

## 数据流

```
点击「修改」预览
  → GET /ai/chat/sessions/:id/diff?path=...
  → 后端 resolve_repo_for_path → git diff（仅 hunk）
  → 前端 parseUnifiedDiff → 对齐左右行
  → FileDiffView 渲染（纯 div，红/绿）
```

```
点击「新增」预览
  → GET /ai/chat/sessions/:id/diff?path=...
  → 后端读取文件（容量上限，可能 truncated）
  → 轻量 <pre> 查看器渲染
```

## 错误处理

- 路径越界 / 非 workspace 内 → 后端 400/403，前端 `ElMessage.error('预览失败')`。
- diff 命令失败 / 文件读取失败 → 返回错误，前端提示失败。
- `truncated=true` → 前端显示截断提示 + 下载入口。

## 测试

- **后端** `server/tests/test_workspace_changes.py`：
  - `resolve_repo_for_path` 把 workspace 相对路径正确映射到嵌套仓库。
  - `file_diff` modified → 返回包含 hunk 的 diff；added → 返回内容并在超限时 `truncated`。
  - 路径穿越（`../`、绝对路径）被拒绝。
- **前端**：
  - `parseUnifiedDiff` 单测：context / 纯新增 / 纯删除 / 增删不等长 hunk 的对齐结果。
  - `FileDiffView` 渲染单测：行数、红/绿类名、行号。
  - 面板分组：删除组默认折叠、空组不渲染、计数正确。

## 涉及文件

- `server/utils/workspace_changes.py`（重构 + `resolve_repo_for_path` + `file_diff`）
- `server/routes/ai_chat.py`（新路由 `/diff`）
- `server/tests/test_workspace_changes.py`
- `src/api/aiChat.ts`（新增 `getFileDiff` + 类型）
- `src/components/ai-chat/FileDiffView.vue`（新）
- `src/utils/`（`parseUnifiedDiff` 工具 + 单测）
- `src/views/ai-chat/AiChatView.vue`（分组折叠、抽屉接入 diff/查看器）
