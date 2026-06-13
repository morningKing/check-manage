# 数据页交互重设计 设计文档

> 日期：2026-06-13
> 分支：`feat/neutral-slate-ui-redesign`
> 关联文件：`src/views/dynamic/DynamicPage.vue`（主）、`src/components/dynamic-data/DataTable.vue`（行内操作）、`src/components/dynamic-data/ViewSelector.vue`（视图选择，复用）

## 背景与目标

数据页（`DynamicPage.vue`）是平台唯一的业务数据页面，承载表格/Excel/看板/日历/甘特五种视图与全部数据操作。经前一轮「单行功能区 + 标签栏瘦身」后，竖向占用已优化，但**功能入口的数量与语义组织仍然杂乱**：

- 功能行仍有 ~9 个并列控件，高频（搜索/新增）与低频（视图选择）平铺，权重不分。
- 「更多」下拉是一个扁平杂物抽屉，导出/导入/版本/依赖/批量删除 9 个互不相关项堆在一起。
- 检索一件事却有三个入口（关键字框 + AI 圆钮 + 高级查询圆钮）。
- 每行表格重复一长串操作（查看/编辑/删除/复制/图谱/导出脚本）。

**目标**：在**保留全部现有功能**的前提下重组交互，使功能入口语义清晰、视觉克制、按使用频率分层。**不删除、不新增任何业务功能**（YAGNI）。

**主场景判定**：均衡，无明显侧重 → 按通用信息架构最佳实践分层。

## 设计总览

功能区按语义重组为「检索区（左）｜操作区（右）」两个分区，配合**选中行时浮现的批量操作条**与**行内主操作外露 + ⋯ 溢出菜单**。

```
标题行：  巡检用例  ·副标题·  [主分支] 切换▾

检索区                                    操作区
[🔍 搜索…        关键字▾] [视图▾]   [⊞表格 ◧Excel …(segmented)] [＋新增] [操作▾]

—— 选中行时，表格上方浮现 ——
[☑ 已选 3 项]   [批量删除]   [取消选择]
```

## 组件级设计

### ① 标题行（保持现状）

不改动。`pageConfig.name` + `description` + 分支标签 + 「切换▾」下拉（含管理员「管理版本…」）。分支切换属于"页面身份"上下文，留在标题行。

### ② 检索区（左）— 三合一统一检索

将现有三个检索入口（`searchKeyword` 输入框、AI 圆钮 `toggleAiSearch`、高级查询圆钮 `toggleQueryMode`）合并为**单一搜索框 + 框内模式选择器**。

**结构**：
```
[🔍 <input>                       <模式▾>]   [视图▾]
```

**模式选择器**（`searchMode: 'keyword' | 'ai' | 'mongo'`，新增 ref，替代 `aiSearchMode`/`queryMode` 两个布尔的"对外入口"，但底层执行逻辑复用现有函数）：

| 模式 | 输入框行为 | 执行 | 结果呈现 |
|------|-----------|------|---------|
| 关键字（默认） | 普通输入，绑定 `searchKeyword`，实时过滤（沿用现有计算属性） | 即时 | 直接过滤 |
| AI 智能 | 占位变「用自然语言描述查询条件…」，绑定 `aiSearchText` | 回车 → `executeAiQuery()` | 生成 `aiGeneratedFilter` 后，框后显示可关闭 chip `✦ AI 筛选 ✕`（点 ✕ = `clearAiQuery()`，hover 显示 filter JSON，复用现有 tooltip） |
| 高级查询 | 占位变 `{"field": {"$regex": "value"}}`，等宽字体，绑定 `mongoQueryText`；框内尾部一个「?」图标触发**语法帮助 popover**（移用现有 `query-help` 内容） | Ctrl+Enter → `executeMongoQuery()` | 生效后框后显示可关闭 chip `⟨⟩ 高级 ✕`（点 ✕ = `clearMongoQuery()`） |

**关键变化**：切换 AI/高级模式时，**同一个搜索框原地变身**，不再单独下沉出 `.advanced-search-bar` 一整行。删除独立的展开行；其执行按钮（AI查询/查询）由"回车/Ctrl+Enter + 模式内联执行"替代，"清除"由 chip 的 ✕ 替代。

**`视图▾`**：保持 `ViewSelector` 组件不变（保存的列视图选择 + 管理视图），紧邻检索框右侧。

**Excel 视图下**：检索区整体隐藏（沿用现有 `viewMode !== 'excel'` 守卫）。

### ③ 操作区（右）— 视图形态 + 新增 + 单一聚合菜单

**视图形态切换**（保留一键 segmented，**不改下拉**）：
沿用现有 `el-radio-group` segmented 控件，按页配置动态显示可用形态：
```
[⊞表格] [◧Excel] [▦看板?] [📅日历?] [📊甘特?]
```
（`table`/`excel` 常驻，`kanban`/`calendar`/`gantt` 按 `hasKanbanConfig`/`hasCalendarConfig`/`hasGanttConfig` 显示。）

**`＋新增`**：保持独立主按钮（`type="primary"`，高频写入），`v-if="!isGuest && canCreate"` 守卫不变。

**`操作▾`**：将现有「更多」扁平菜单重组为**带分组标题的子菜单结构**。批量删除移出（见 ④）。

```
操作▾
├ 刷新                                    handleRefresh
├──────────────────────────────────
│  导入 / 导出 ▸
│    ├ 导出 Excel                         handleMoreCommand('export')
│    ├ <绑定的导出脚本…(动态列表)>          handleMoreCommand('script:' + id)
│    ├ 导入数据            (!isGuest)      handleMoreCommand('import')
│    └ 下载导入模板        (!isGuest)      handleMoreCommand('template')
├──────────────────────────────────
│  引用 / 关系 ▸           (hasReferenceFields)
│    └ 重新解析引用        (!isGuest)      handleMoreCommand('reResolveRefs')
├──────────────────────────────────
│  数据治理（管理员）▸      (isAdmin)
│    ├ 版本管理                            handleMoreCommand('version')
│    ├ 依赖管理                            handleMoreCommand('dependency')
│    └ 复制 collection 名                  handleMoreCommand('copyCollection')
```

实现用 Element Plus 嵌套 `el-sub-menu` 风格（`el-dropdown-menu` 内嵌 `el-dropdown` 或分组标题 + 缩进项）。所有 `command` 值与 `handleMoreCommand` 分支**保持不变**——仅菜单 DOM 结构与分组呈现变化。分组若整组无可见项（如非管理员的"数据治理"组），整组连同分组标题一并隐藏。

### ④ 批量操作浮现条（选中行时）

`批量删除`（现位于「更多」，`isAdmin`）从聚合菜单移出，上下文化。

- 当 `selectedRows.length > 0` 时，在功能区与表格之间浮现一条：
  ```
  [☑ 已选 N 项]        [批量删除]        [取消选择]
  ```
- `批量删除` → `handleBatchDeleteConfirm()`（现有，`isAdmin` 守卫保留；非管理员不显示该按钮，但浮现条仍显示"已选 N 项 + 取消选择"）。
- `取消选择` → 清空选择（调用 `DataTable` 的清空选择方法 / 重置 `selectedRows`）。
- `selectedRows.length === 0` 时此条不渲染，零占用。
- **不新增"批量导出"等不存在的功能**。

### ⑤ 行内操作 — 主操作外露 + ⋯ 溢出菜单

`DataTable.vue` 操作列由"一长串平铺按钮"改为"主操作 + ⋯ 溢出"。

- **主操作（外露）**：`canUpdate` 时显示 `✎ 编辑`（`handleEdit`）；只读角色（无 `canUpdate`）退化为 `查看`（`handleView`）。
- **⋯ 溢出菜单**（`el-dropdown`，trigger=click）：
  ```
  ⋯
  ├ 查看                    handleView
  ├ 复制         (canCreate) handleCopy
  ├ 关系图谱                 handleShowRelationGraph
  ├ 导出脚本 ▸  (有绑定行级脚本) handleRowExport(id, row)（多个时子菜单，单个时直接项）
  └ 删除         (canDelete) handleDeleteConfirm（危险色）
  ```
- 操作列在**行 hover 时高亮**（非 hover 时弱化为次要色，沿用主题 `--el-text-color-secondary`）。
- 所有 emit 事件名（`@view`/`@edit`/`@delete`/`@copy`/`@reference-click` 等）与现有 `DataTable` 对外契约保持一致；仅操作列模板内部重组。

## 响应式与降级

- 检索区/操作区用 `flex-wrap`（功能区现已 `flex-wrap: wrap; gap`）。窄屏时操作区换行至检索区下方，分区语义不破坏。
- 批量浮现条在窄屏独占一行，按钮右对齐。

## 不在本次范围

- 不改后端、不改 `handleMoreCommand`/`handleRowExport` 等业务逻辑函数体。
- 不改五种视图（表格/Excel/看板/日历/甘特）各自的渲染。
- 不新增任何业务能力（批量导出、批量编辑等均不臆造）。
- 分支切换菜单与「操作▾→数据治理→版本管理」可能存在两个入口指向同一版本管理对话框——保留现状双入口，不在本次合并。

## 测试策略

现有前端测试 `src/views/dynamic/__tests__/DynamicPage.test.ts`（32 项）必须保持通过。新增/调整断言覆盖：

1. **统一检索模式切换**：`searchMode` 在 keyword/ai/mongo 间切换时，绑定的 model（`searchKeyword`/`aiSearchText`/`mongoQueryText`）与占位、执行入口正确对应；切换不再渲染独立 `.advanced-search-bar`。
2. **聚合菜单 command 完整性**：`操作▾` 展开后，所有原 `handleMoreCommand` 的 command（export/script:*/import/template/reResolveRefs/version/dependency/copyCollection）按角色守卫正确出现，且分组标题在整组隐藏时不残留。
3. **批量浮现条**：`selectedRows` 非空时浮现条出现且含"批量删除"（管理员）/"取消选择"；为空时不渲染。
4. **行内操作**：`DataTable` 操作列在 `canUpdate` 下外露"编辑"、`⋯` 菜单含查看/复制/关系图谱/导出脚本/删除；只读角色外露"查看"。

视觉验证：Playwright 截图比对功能区单行内 5 个语义入口、`操作▾` 分组子菜单、选中行浮现条、行内 hover 高亮。

## 净效果

- 功能行并列控件：~9 → **5**（检索框 / 视图 / 形态 segmented / 新增 / 操作）。
- 「更多」扁平 9 项 → `操作▾` **3 个分组子菜单**。
- 检索三入口 → **1 个框 + 模式选择器**。
- 行内一长串 → **编辑 + ⋯**。
- 批量删除 → 选中行上下文浮现。
- **零功能删除**。
