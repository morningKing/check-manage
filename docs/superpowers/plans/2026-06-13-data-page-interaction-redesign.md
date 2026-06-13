# 数据页交互重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留全部现有功能的前提下，把数据页功能区重组为「检索区｜操作区」分区 + 三合一统一检索 + 单一操作聚合菜单 + 选中行浮现批量条 + 行内「编辑 + ⋯」。

**Architecture:** 纯前端模板与交互重组，不改后端、不改业务处理函数体。唯一抽出的可单测逻辑是「检索模式切换时该清除哪个查询」，落为纯函数 `searchModeTransition`。其余为声明式模板改造，靠 `vue-tsc` 类型检查 + 既有 642 项测试保持绿 + Playwright 截图验证。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + Element Plus + Vitest + Playwright MCP。

关联文件：
- `src/views/dynamic/DynamicPage.vue` — 功能区、检索、聚合菜单、批量条、行内 slot（主）
- `src/components/common/DataTable.vue` — 操作列「编辑 + ⋯」重构
- `src/views/dynamic/searchMode.ts` — 新建，纯函数
- `src/views/dynamic/__tests__/searchMode.test.ts` — 新建，单测

设计依据：`docs/superpowers/specs/2026-06-13-data-page-interaction-redesign-design.md`

---

## 既有代码锚点（实现前必读）

`DynamicPage.vue` 现状关键位置（行号会随编辑漂移，按代码片段定位）：

- 检索状态 ref：
  ```ts
  const searchKeyword = ref('')          // ~922
  const queryMode = ref(false)           // ~937
  const mongoQueryText = ref('')         // ~938
  const activeMongoQuery = ref<Record<string, any> | null>(null)  // ~939
  const aiSearchMode = ref(false)        // ~944
  const aiSearchText = ref('')           // ~945
  const aiGeneratedFilter = ref<Record<string, any> | null>(null) // ~947
  const selectedRows = ref<DynamicRecord[]>([])  // ~1039
  ```
- 切换函数：`toggleQueryMode()` (~1698)、`toggleAiSearch()` (~1759)。执行/清除：`executeMongoQuery` (~1714)、`clearMongoQuery` (~1745)、`executeAiQuery` (~1774)、`clearAiQuery` (~1811)。**这些执行/清除函数体保持不变。**
- 模板：功能区 `<div class="page-actions">` (~68)；独立展开行 `<div class="advanced-search-bar" ...>` (~171)；更多菜单 (~113)；DataTable `#extra-actions` slot (~250)。
- 重置逻辑块（~2983）：
  ```ts
  queryMode.value = false
  activeMongoQuery.value = null
  mongoQueryText.value = ''
  aiSearchMode.value = false
  aiSearchText.value = ''
  // (aiSearchLoading.value = false 这一行保留)
  aiGeneratedFilter.value = null
  searchKeyword.value = ''
  ```
- `DataTable` 已 `defineExpose({ tableRef, clearSelection, ... })`（~635），父组件可直接 `dataTableRef.value?.clearSelection()`。
- `DataTable` 操作列 (~103-123)：`查看`(始终) + `#extra-actions` slot + `编辑`(canUpdate) + `删除`(canDelete)，列宽 250。

---

## Task 1: 抽出检索模式切换纯函数（TDD）

**Files:**
- Create: `src/views/dynamic/searchMode.ts`
- Test: `src/views/dynamic/__tests__/searchMode.test.ts`

把「从某检索模式切到另一模式时，应清除 AI 筛选还是 Mongo 查询」的判定逻辑抽成纯函数，便于单测，并供 Task 2 的 `setSearchMode` 调用。

逻辑等价于现有 `toggleAiSearch`/`toggleQueryMode` 的副作用：
- 切到 `keyword`：若来源是 ai 且有 AI 筛选 → 清 AI；若来源是 mongo 且有 Mongo 查询 → 清 Mongo。
- 切到 `ai`：若存在 Mongo 查询且无 AI 筛选 → 清 Mongo（清掉游离的高级查询）。
- 切到 `mongo`：若存在 AI 筛选 → 清 AI。

- [ ] **Step 1: 写失败测试**

`src/views/dynamic/__tests__/searchMode.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { searchModeTransition, type SearchMode } from '../searchMode'

describe('searchModeTransition', () => {
  const base = { hasAiFilter: false, hasMongoQuery: false }

  it('keyword <- ai: 有 AI 筛选时清 AI', () => {
    expect(searchModeTransition('ai', 'keyword', { ...base, hasAiFilter: true }))
      .toStrictEqual({ clearAi: true, clearMongo: false })
  })

  it('keyword <- mongo: 有 Mongo 查询时清 Mongo', () => {
    expect(searchModeTransition('mongo', 'keyword', { ...base, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: true })
  })

  it('-> ai: 有游离 Mongo 查询且无 AI 筛选时清 Mongo', () => {
    expect(searchModeTransition('keyword', 'ai', { ...base, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: true })
  })

  it('-> ai: 已有 AI 筛选则不清 Mongo（AI 复用 activeMongoQuery）', () => {
    expect(searchModeTransition('keyword', 'ai', { hasAiFilter: true, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: false })
  })

  it('-> mongo: 有 AI 筛选时清 AI', () => {
    expect(searchModeTransition('ai', 'mongo', { hasAiFilter: true, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: true, clearMongo: false })
  })

  it('同模式切换：无副作用', () => {
    expect(searchModeTransition('keyword', 'keyword', base))
      .toStrictEqual({ clearAi: false, clearMongo: false })
  })

  it('类型导出可用', () => {
    const m: SearchMode = 'ai'
    expect(['keyword', 'ai', 'mongo']).toContain(m)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/views/dynamic/__tests__/searchMode.test.ts`
Expected: FAIL，报 `Cannot find module '../searchMode'`。

- [ ] **Step 3: 写实现**

`src/views/dynamic/searchMode.ts`:
```ts
/**
 * 数据页统一检索的三种模式。
 * 关键字：实时过滤；ai：自然语言生成筛选；mongo：高级 JSON 查询。
 */
export type SearchMode = 'keyword' | 'ai' | 'mongo'

export interface SearchQueryState {
  /** 当前是否存在 AI 生成的筛选条件 */
  hasAiFilter: boolean
  /** 当前是否存在已生效的 Mongo 查询 */
  hasMongoQuery: boolean
}

export interface SearchModeTransitionResult {
  clearAi: boolean
  clearMongo: boolean
}

/**
 * 判定从 from 模式切到 to 模式时应清除哪些已生效的查询。
 * 等价于旧 toggleAiSearch / toggleQueryMode 的互斥副作用。
 */
export function searchModeTransition(
  from: SearchMode,
  to: SearchMode,
  state: SearchQueryState
): SearchModeTransitionResult {
  if (from === to) return { clearAi: false, clearMongo: false }

  if (to === 'keyword') {
    return {
      clearAi: from === 'ai' && state.hasAiFilter,
      clearMongo: from === 'mongo' && state.hasMongoQuery,
    }
  }
  if (to === 'ai') {
    // 进入 AI：清掉游离的高级查询（但 AI 自身复用 activeMongoQuery，故有 AI 筛选时不清）
    return { clearAi: false, clearMongo: state.hasMongoQuery && !state.hasAiFilter }
  }
  // to === 'mongo'：进入高级查询，清掉 AI 筛选
  return { clearAi: state.hasAiFilter, clearMongo: false }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/views/dynamic/__tests__/searchMode.test.ts`
Expected: PASS，7 项全过。

- [ ] **Step 5: 提交**

```bash
git add src/views/dynamic/searchMode.ts src/views/dynamic/__tests__/searchMode.test.ts
git commit -m "feat(data-page): 抽出检索模式切换纯函数 searchModeTransition"
```

---

## Task 2: 统一检索区 + 接入 searchMode

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（script：state + setSearchMode；template：检索区；删除 advanced-search-bar）

把 `aiSearchMode`/`queryMode` 两个布尔改为单一 `searchMode`，两个圆钮 + 独立展开行合并为「单一搜索框 + 框内模式选择器 + 框后 chip」。

- [ ] **Step 1: 替换检索状态 ref，引入 searchMode + computed 兼容**

将（~937、~944）：
```ts
const queryMode = ref(false)
```
和
```ts
const aiSearchMode = ref(false)
```
分别**删除**，并在 `searchKeyword` ref 之后新增：
```ts
import { searchModeTransition, type SearchMode } from './searchMode'
// ...（与其它 import 合并；script 顶部）

const searchMode = ref<SearchMode>('keyword')
// 兼容既有模板/逻辑中对两布尔的只读引用
const aiSearchMode = computed(() => searchMode.value === 'ai')
const queryMode = computed(() => searchMode.value === 'mongo')
```
保留 `mongoQueryText` / `activeMongoQuery` / `aiSearchText` / `aiGeneratedFilter` / `aiSearchLoading` 原样。

- [ ] **Step 2: 用 setSearchMode 取代两个 toggle 函数**

删除 `toggleQueryMode()`（~1698-1709）与 `toggleAiSearch()`（~1759-1769）整段，替换为：
```ts
/**
 * 切换统一检索模式（关键字 / AI / 高级）。
 * 互斥副作用复用纯函数 searchModeTransition。
 */
function setSearchMode(mode: SearchMode): void {
  const from = searchMode.value
  if (from === mode) return
  const { clearAi, clearMongo } = searchModeTransition(from, mode, {
    hasAiFilter: !!aiGeneratedFilter.value,
    hasMongoQuery: !!activeMongoQuery.value,
  })
  searchMode.value = mode
  if (clearAi) clearAiQuery()
  if (clearMongo) clearMongoQuery()
}
```

- [ ] **Step 3: 修正重置块中的布尔赋值**

在重置块（~2983）里，把：
```ts
queryMode.value = false
```
和
```ts
aiSearchMode.value = false
```
两行**删除**，改为在该块顶部加一行：
```ts
searchMode.value = 'keyword'
```
（其余 `activeMongoQuery.value = null` / `mongoQueryText.value = ''` / `aiSearchText.value = ''` / `aiGeneratedFilter.value = null` / `searchKeyword.value = ''` 保留不动。）

- [ ] **Step 4: 重写检索区模板**

把功能区里现有的「关键字 el-input + AI 圆钮 el-tooltip + 高级查询圆钮 el-tooltip + actions-divider」整段（~70-94）替换为：
```html
<!-- 检索区：三合一统一检索 -->
<div v-if="viewMode !== 'excel'" class="search-zone">
  <el-input
    v-if="searchMode === 'keyword'"
    v-model="searchKeyword"
    placeholder="搜索..."
    clearable
    :prefix-icon="Search"
    class="header-search"
  />
  <el-input
    v-else-if="searchMode === 'ai'"
    v-model="aiSearchText"
    placeholder="用自然语言描述查询条件，回车执行"
    clearable
    class="header-search header-search--wide"
    @keydown.enter="executeAiQuery"
  >
    <template #prefix><el-icon><MagicStick /></el-icon></template>
  </el-input>
  <el-input
    v-else
    v-model="mongoQueryText"
    placeholder='{"field": {"$regex": "value"}}'
    class="header-search header-search--wide header-search--mono"
    @keydown.ctrl.enter="executeMongoQuery"
  >
    <template #prefix><el-icon><DCaret /></el-icon></template>
    <template #suffix>
      <el-popover placement="bottom-end" :width="420" trigger="click">
        <template #reference>
          <el-icon class="query-help-icon"><QuestionFilled /></el-icon>
        </template>
        <div class="query-help">
          <p><b>MongoDB 查询语法</b>（支持中文字段名）</p>
          <table>
            <tr><td><code>{"用例ID": "IC-001"}</code></td><td>精确匹配</td></tr>
            <tr><td><code>{"名称": {"$regex": "test"}}</code></td><td>正则匹配（不区分大小写）</td></tr>
            <tr><td><code>{"名称": {"$like": "test"}}</code></td><td>模糊匹配（%test%）</td></tr>
            <tr><td><code>{"age": {"$gt": 18}}</code></td><td>大于（$gte, $lt, $lte 类似）</td></tr>
            <tr><td><code>{"状态": {"$in": ["a","b"]}}</code></td><td>在列表中</td></tr>
            <tr><td><code>{"状态": {"$nin": ["a"]}}</code></td><td>不在列表中</td></tr>
            <tr><td><code>{"field": {"$ne": "val"}}</code></td><td>不等于</td></tr>
            <tr><td><code>{"field": {"$exists": true}}</code></td><td>字段存在/不存在</td></tr>
            <tr><td><code>{"$or": [{...}, {...}]}</code></td><td>逻辑或</td></tr>
            <tr><td><code>{"$and": [{...}, {...}]}</code></td><td>逻辑与</td></tr>
          </table>
          <p style="margin-top:8px;color:#909399;font-size:12px">Ctrl+Enter 快捷执行 · 字段名可用中文标签或英文字段名</p>
        </div>
      </el-popover>
    </template>
  </el-input>

  <!-- 模式选择器 -->
  <el-select
    v-if="viewMode !== 'excel'"
    :model-value="searchMode"
    class="search-mode-select"
    @update:model-value="setSearchMode"
  >
    <el-option label="关键字" value="keyword" />
    <el-option label="AI 智能" value="ai" />
    <el-option label="高级查询" value="mongo" />
  </el-select>

  <!-- 生效中的查询 chip -->
  <el-tooltip v-if="aiGeneratedFilter" placement="bottom">
    <template #content>
      <pre style="margin:0;max-width:400px;white-space:pre-wrap">{{ JSON.stringify(aiGeneratedFilter, null, 2) }}</pre>
    </template>
    <el-tag type="warning" closable class="search-chip" @close="clearAiQuery">
      <el-icon style="vertical-align: -2px"><MagicStick /></el-icon> AI 筛选
    </el-tag>
  </el-tooltip>
  <el-tag
    v-else-if="activeMongoQuery"
    type="primary"
    closable
    class="search-chip"
    @close="clearMongoQuery"
  >
    ⟨⟩ 高级
  </el-tag>
  <span class="actions-divider"></span>
</div>
```

- [ ] **Step 5: 删除独立展开行 + 旧的 header AI chip**

删除整段独立展开行 `<div class="advanced-search-bar" v-if="...">...</div>`（~171-225）。
同时删除功能区里原先放在「更多」之后的 AI 筛选 tooltip 块（~145-153，`<el-tooltip v-if="aiGeneratedFilter" ...>`），因为 chip 已移入检索区（避免重复）。

- [ ] **Step 6: 补图标 import**

确认 `<script setup>` 顶部图标 import 含 `Search, MagicStick, DCaret`（已用），新增 `QuestionFilled`。在 `@element-plus/icons-vue` import 列表追加 `QuestionFilled`。

- [ ] **Step 7: 调整检索区样式**

在 `<style scoped>` 内（原 `.advanced-search-bar` 规则删除），把 `.header-search { width: 200px; }` 调整并新增：
```scss
.search-zone {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header-search { width: 200px; }
.header-search--wide { width: 320px; }
.header-search--mono :deep(.el-input__inner) { font-family: monospace; }
.search-mode-select { width: 104px; }
.search-chip { flex-shrink: 0; }
.query-help-icon { cursor: pointer; color: var(--el-text-color-placeholder); }
.query-help-icon:hover { color: var(--el-color-primary); }
```
（`.query-help` 表格样式若原先在 `advanced-search-bar` 作用域内，保留该 `.query-help { ... }` 规则块，仅删除 `.advanced-search-bar` 容器规则。）

- [ ] **Step 8: 类型检查 + 既有测试**

Run: `npx vue-tsc --noEmit`
Expected: 无报错（注意 `aiSearchMode`/`queryMode` 现为 computed，模板中只读引用合法；确认无处再对其赋值）。

Run: `npx vitest run src/`
Expected: 全绿（642 + 新增 7）。

- [ ] **Step 9: 提交**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(data-page): 三合一统一检索（框内模式选择器 + chip），移除独立查询行"
```

---

## Task 3: 操作区聚合菜单（分组）+ 移出批量删除

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（template：更多菜单 → 操作菜单）

把「更多」扁平菜单改为带分组标题的「操作▾」，并把 `batchDelete` 项移除（Task 4 的批量条承接）。视图形态 segmented、新增、刷新均保持现状不动。

> 实现选择：用「`divided` 分隔 + `disabled` 分组标题项」实现分组，而非 EP 嵌套飞出子菜单——更健壮、零 hover 抖动，净效果同分组。所有 `command` 值与 `handleMoreCommand` 分支保持不变。

- [ ] **Step 1: 替换更多菜单为分组操作菜单**

把功能区「更多」整段 `<el-dropdown @command="handleMoreCommand" ...>...</el-dropdown>`（~113-143）替换为：
```html
<el-dropdown @command="handleMoreCommand" trigger="click">
  <el-button>
    操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
  </el-button>
  <template #dropdown>
    <el-dropdown-menu>
      <el-dropdown-item command="refresh" :icon="Refresh">刷新</el-dropdown-item>

      <el-dropdown-item divided disabled class="dropdown-group-label">导入 / 导出</el-dropdown-item>
      <el-dropdown-item command="export" :icon="Download">导出 Excel</el-dropdown-item>
      <el-dropdown-item
        v-for="s in boundExportScripts"
        :key="s.id"
        :command="'script:' + s.id"
        :icon="Download"
      >
        {{ s.name }} ({{ s.outputFormat }})
      </el-dropdown-item>
      <el-dropdown-item v-if="!isGuest" command="import" :icon="Upload">导入数据</el-dropdown-item>
      <el-dropdown-item v-if="!isGuest" command="template" :icon="Download">下载导入模板</el-dropdown-item>

      <template v-if="!isGuest && hasReferenceFields">
        <el-dropdown-item divided disabled class="dropdown-group-label">引用 / 关系</el-dropdown-item>
        <el-dropdown-item command="reResolveRefs" :icon="RefreshRight">重新解析引用</el-dropdown-item>
      </template>

      <template v-if="isAdmin">
        <el-dropdown-item divided disabled class="dropdown-group-label">数据治理</el-dropdown-item>
        <el-dropdown-item command="version" :icon="Tickets">版本管理</el-dropdown-item>
        <el-dropdown-item command="dependency" :icon="Operation">依赖管理</el-dropdown-item>
        <el-dropdown-item command="copyCollection" :icon="CopyDocument">复制 collection 名</el-dropdown-item>
      </template>
    </el-dropdown-menu>
  </template>
</el-dropdown>
```
注意：原独立的「刷新」按钮 `<el-tooltip content="刷新"><el-button :icon="Refresh" @click="handleRefresh" /></el-tooltip>`（~110-112）**删除**，刷新并入操作菜单（command `refresh`）。

- [ ] **Step 2: handleMoreCommand 增加 refresh 分支**

在 `handleMoreCommand` 的 switch/if 链中新增一条（保持其它分支不变）：
```ts
case 'refresh':
  handleRefresh()
  break
```
（若 `handleMoreCommand` 用 if-else 结构，则等价新增 `if (command === 'refresh') { handleRefresh(); return }`。）

- [ ] **Step 3: 分组标题样式**

`<style scoped>` 新增：
```scss
:deep(.dropdown-group-label.is-disabled) {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  cursor: default;
  opacity: 1;
  padding-top: 4px;
  padding-bottom: 2px;
  font-weight: 600;
}
```

- [ ] **Step 4: 类型检查 + 测试**

Run: `npx vue-tsc --noEmit`
Expected: 无报错（`batchDelete` 已从模板移除，但 `handleMoreCommand` 仍保留其分支也无妨；`selectedRows` 仍被批量删除逻辑引用）。

Run: `npx vitest run src/`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(data-page): 更多菜单重组为分组操作菜单，刷新并入、批量删除移出"
```

---

## Task 4: 选中行浮现批量操作条

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（template：新增批量条；script：clearTableSelection）

- [ ] **Step 1: 在功能区与表格之间插入批量条**

在 `</div>`（page-header 闭合）之后、jump-source-bar 之前（或紧邻表格 `el-card` 之前皆可，置于 jump-source-bar 之后更佳），新增：
```html
<!-- 批量操作浮现条（仅表格视图、有选中时） -->
<div v-if="viewMode === 'table' && selectedRows.length > 0" class="batch-action-bar">
  <span class="batch-count">
    <el-icon><Select /></el-icon>
    已选 {{ selectedRows.length }} 项
  </span>
  <div class="batch-buttons">
    <el-button
      v-if="isAdmin"
      type="danger"
      :icon="Delete"
      @click="handleBatchDeleteConfirm"
    >
      批量删除
    </el-button>
    <el-button text @click="clearTableSelection">取消选择</el-button>
  </div>
</div>
```

- [ ] **Step 2: 新增 clearTableSelection 函数**

在 script 中（`handleSelectionChange` 附近）新增：
```ts
/**
 * 清空表格选择（批量条「取消选择」）。
 * DataTable 已 defineExpose clearSelection，清空会触发 selection-change 回写 selectedRows。
 */
function clearTableSelection(): void {
  dataTableRef.value?.clearSelection()
  selectedRows.value = []
}
```
（确认 `dataTableRef` 已存在：模板 `<DataTable ref="dataTableRef" ...>`，且 script 内已有 `const dataTableRef = ref()`。若缺失则补 `const dataTableRef = ref<InstanceType<typeof DataTable> | null>(null)`。）

- [ ] **Step 3: 补图标 import**

`@element-plus/icons-vue` import 追加 `Select`（`Delete` 已用）。

- [ ] **Step 4: 批量条样式**

`<style scoped>` 新增：
```scss
.batch-action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 14px;
  margin-bottom: 10px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}
.batch-count {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.batch-buttons { display: flex; gap: 8px; }
```

- [ ] **Step 5: 类型检查 + 测试**

Run: `npx vue-tsc --noEmit`
Expected: 无报错。

Run: `npx vitest run src/`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(data-page): 选中行浮现批量操作条，承接批量删除"
```

---

## Task 5: 行内操作「编辑 + ⋯ 溢出」

**Files:**
- Modify: `src/components/common/DataTable.vue`（操作列重构）
- Modify: `src/views/dynamic/DynamicPage.vue`（`#extra-actions` slot 改为 dropdown-item）

- [ ] **Step 1: 重构 DataTable 操作列**

把 `DataTable.vue` 操作列（~103-123）整段替换为：
```html
<!-- 操作列 -->
<el-table-column
  label="操作"
  :width="showActions ? 120 : 80"
  align="center"
  fixed="right"
>
  <template #default="{ row }">
    <template v-if="showActions">
      <el-button v-if="canUpdate" type="primary" link @click="handleEdit(row)">
        编辑
      </el-button>
      <el-button v-else type="primary" link @click="handleView(row)">
        查看
      </el-button>
      <el-dropdown trigger="click" class="row-actions-more">
        <el-button link class="row-actions-trigger">
          <el-icon><MoreFilled /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="canUpdate" @click="handleView(row)">查看</el-dropdown-item>
            <slot name="extra-actions" :row="row" />
            <el-dropdown-item
              v-if="canDelete"
              divided
              class="row-actions-danger"
              @click="handleDelete(row)"
            >删除</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </template>
    <el-button v-else type="primary" link @click="handleView(row)">
      查看
    </el-button>
  </template>
</el-table-column>
```

- [ ] **Step 2: DataTable 补 MoreFilled import + 样式**

`DataTable.vue` `<script setup>` 的 `@element-plus/icons-vue` import 追加 `MoreFilled`（若该文件尚未从该包 import，则新增一行 `import { MoreFilled } from '@element-plus/icons-vue'`）。

`<style scoped>` 新增：
```scss
.row-actions-more { margin-left: 4px; vertical-align: middle; }
.row-actions-trigger { color: var(--el-text-color-secondary); }
.row-actions-trigger:hover { color: var(--el-text-color-primary); }
:deep(.row-actions-danger) { color: var(--el-color-danger); }
```

- [ ] **Step 3: 改写 DynamicPage 的 #extra-actions slot**

把 `DynamicPage.vue` 的 `<template #extra-actions="{ row }">...</template>`（~250-290）整段替换为（link 按钮 → dropdown-item，导出脚本拍平为逐项）：
```html
<template #extra-actions="{ row }">
  <el-dropdown-item v-if="!isGuest && canCreate" @click="handleCopy(row)">复制</el-dropdown-item>
  <el-dropdown-item @click="handleShowRelationGraph(row)">关系图谱</el-dropdown-item>
  <el-dropdown-item
    v-for="s in boundRowExportScripts"
    :key="s.id"
    @click="handleRowExport(s.id, row)"
  >
    导出：{{ s.name }}
  </el-dropdown-item>
</template>
```
（功能等价：原「图谱」→「关系图谱」；原导出 dropdown/单按钮 → 每个绑定脚本一个 `导出：脚本名` 项，全部可达。）

- [ ] **Step 4: 类型检查 + 测试**

Run: `npx vue-tsc --noEmit`
Expected: 无报错。

Run: `npx vitest run src/`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/components/common/DataTable.vue src/views/dynamic/DynamicPage.vue
git commit -m "feat(data-page): 行内操作改为编辑外露 + ⋯ 溢出菜单"
```

---

## Task 6: 视觉验证 + 收尾

**Files:** 无代码改动（除非验证发现问题）

- [ ] **Step 1: 确保 dev server 在跑**

worktree 的 `npm run dev` 服务 `http://localhost:5173`（前序会话已起；若未起则 `npm run dev`）。

- [ ] **Step 2: Playwright 截图验证四处**

用 Playwright MCP 导航 `http://localhost:5173/inspection/case`，逐一截图核对：
1. 功能区单行内为 5 个语义入口：检索框+模式选择器、视图▾、形态 segmented、新增、操作▾。
2. 模式选择器切到「AI 智能」「高级查询」时，同一框原地变身，无独立展开行；高级模式框内有「?」语法帮助。
3. 勾选 1-2 行 → 表格上方浮现批量条（已选 N 项 / 批量删除 / 取消选择）；取消选择后批量条消失。
4. 行内只见「编辑」+「⋯」；点 ⋯ 展开含 查看/复制/关系图谱/导出：*/删除。
5. 操作▾ 展开为分组（刷新 / 导入导出 / 引用关系 / 数据治理）。

- [ ] **Step 3: 全量前后端无回归**

Run: `npx vitest run src/`
Expected: 全绿（642 + 7）。

- [ ] **Step 4: 提交（若验证产生微调）**

```bash
git add -A
git commit -m "fix(data-page): 交互重设计视觉验证微调"
```
（无微调则跳过。）

---

## Self-Review（作者自检）

**Spec 覆盖：**
- ① 标题行不变 → 计划不触碰，✓
- ② 三合一统一检索（框内模式选择器 + 原地变身 + chip + ?语法帮助）→ Task 2，✓
- ③ 操作区：形态 segmented 保留 + 新增独立 + 单一操作分组菜单 → Task 3（形态/新增明确不动），✓
- ④ 批量浮现条（已选N/批量删除/取消选择，零占用，不臆造批量导出）→ Task 4，✓
- ⑤ 行内 编辑 + ⋯（查看/复制/关系图谱/导出脚本/删除，hover 高亮，只读退化查看）→ Task 5，✓
- 测试策略：检索模式逻辑单测 → Task 1；其余靠既有测试 + Playwright → Task 6，✓
- 命令值不变（handleMoreCommand/handleRowExport）→ Task 3/5 明确保留，✓

**实现取舍（偏离 spec 视觉但功能等价，已在任务内注明）：**
- 操作菜单分组用「divided + disabled 标题」而非飞出子菜单（健壮性）。
- 行内导出脚本由「▸子菜单」拍平为「导出：脚本名」逐项（全部可达）。
两者均零功能删除，符合 spec「保留全部功能」总纲。

**类型一致性：** `searchMode: SearchMode`、`searchModeTransition(from,to,state)`、`setSearchMode(mode)`、`clearTableSelection()` 在各 Task 间命名一致；`aiSearchMode`/`queryMode` 由 ref 改 computed 后全程只读引用。

**占位符扫描：** 无 TBD/TODO；每个改动步骤含完整代码。
