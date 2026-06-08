# 项目级数据迁移（批量导入 / 批量清空）设计

- 日期：2026-06-08
- 状态：已评审待实现
- 关联：复用现有「菜单级导出」（`MenuExportPage.vue` / `server/routes/menu_export.py`）、单页导入与引用解析（`src/stores/pageConfig.ts`）、重新解析引用（commit `d51b850` / `38d3658`）

## 背景与目标

现有「数据导出」页（`/admin/menu-export`）已支持**按项目（菜单）维度**勾选多个数据页、用导出脚本生成 ZIP。但导入侧只有**单页** Excel/JSON 导入，且没有批量清空能力。

本设计在「数据工具」中补齐两项能力，并让三者统一在一个页面：

1. **按项目维度批量导入**多个数据页的数据（多个 Excel，每页一个）。
2. 导入时**自动分析引用顺序**，按引用关系（`reference` / `quoteSelect`）拓扑排序后导入。
3. **按项目维度批量清空**多个数据页的全部记录。

### 非目标（YAGNI）

- 不处理 `relation`（M:N 双向）字段的顺序分析——仅 `reference` + `quoteSelect` 单向引用。
- 不做"导出↔导入"的标准 ZIP 往返格式——导入数据来源是用户用现有单页导入模板准备的多个 Excel。
- 批量清空不删除页面/字段定义（`page_configs`）或菜单，只清数据。
- 不做按条件的部分删除——清空即整页（指定分支）全部记录。

## 关键决策（来自需求澄清）

| 决策点 | 选择 |
|--------|------|
| 导入数据来源/格式 | 多个 Excel 文件，每页一个，复用现有单页导入模板 |
| 文件→页面对应 | 先选项目 → 列出该项目下所有数据页 → 逐页挂 Excel（可留空跳过）|
| 引用顺序分析范围 | 仅 `reference` + `quoteSelect`（单向，值存 JSONB）|
| 循环/未匹配引用 | 尽力按序导入 + 收尾自动对所有页跑一次「重新解析引用」 |
| 批量删除范围 | 清空选定页在指定分支（默认 `main`）的全部记录 |
| 页面入口组织 | 统一「数据迁移」页 + 三 Tab（数据导出 / 批量导入 / 批量清空）|
| 导入执行位置 | 前端编排（复用前端已有解析逻辑），后端基本不改 |
| 删除执行位置 | 后端轻量事务接口（批量 DELETE）|

**为什么导入用前端编排**：引用解析（`resolveReferenceImportValues` / `resolveQuoteImportValues` / `reResolveReferences`）全部在前端 `pageConfig` store，且刚迭代过（延迟解析 + 重新解析）。后端统一方案需把这套逻辑用 Python 重写一份，双份维护、易不一致，收益不抵成本。

## 架构与页面组织

把现有 `MenuExportPage.vue` 改造为统一的「数据迁移」页。**保留路由 path `/admin/menu-export`**（避免破坏 `menus` 表里已挂的导航），仅把组件内容 Tab 化、`meta.title` 改为「数据迁移」。

| Tab | 内容 | 来源 |
|-----|------|------|
| 数据导出 | 现有菜单导出逻辑原样迁入 | 抽成 `ExportTab.vue`（搬运现有 `MenuExportPage` 代码）|
| 批量导入 | 选项目 → 逐页挂 Excel → 按引用顺序导入 | 新增 `ImportTab.vue` |
| 批量清空 | 选项目 → 勾选页 → 二次确认 → 清空 | 新增 `ClearTab.vue` |

### 模块拆分（单一职责、可独立测试）

1. **`src/utils/referenceTopoSort.ts`（纯函数）**
   - 输入：选中页的 `PageConfig[]`
   - 扫描每页 `reference`/`quoteSelect` 字段的 `targetCollection`，连边「被引用页 → 引用页」（目标先导）
   - Kahn 算法拓扑排序，成环时断边继续
   - 返回 `{ order: string[]; cycles: string[][] }`
   - 不依赖 Vue/网络，纯数据进出

2. **`src/composables/useBatchImport.ts`（编排）**
   - 接收「页面→文件」映射，调用 topoSort 定序，按序复用 `pageConfigStore` 的解析+写库逻辑，收尾跑 `reResolveReferences`，产出进度与结果汇总

3. **后端 `server/utils/menu_export.py` 新增 `batch_clear()`** + **`server/routes/menu_export.py` 新增 `POST /menuExport/batchClear`**

复用现状：导入侧完全复用前端已有 `resolve*ImportValues` / `reResolveReferences` / `parseImportFile`；删除侧后端加一个轻量批量删除接口。

## 批量导入：数据流与引用排序

### UI 流程（`ImportTab.vue`）

1. 顶部选项目（菜单树，复用 `getAvailableExportMenus`）+ 选目标分支（默认 `main`）。
2. 选定后列出该项目下所有数据页，每页一行：页名、collection、当前记录数、一个 Excel 上传槽（留空=跳过该页）。
3. 「开始导入」触发编排。

### 引用依赖图 + 定序（`referenceTopoSort.ts`）

- 节点 = 选中项目下数据页的 collection。
- 对每页扫 `reference`/`quoteSelect` 字段，取 `targetCollection`；若目标也在本项目页集合内，连边「目标 → 本页」。
- 指向**项目外 / 库内已有**集合的引用：不连边（运行时直接查库解析），不影响排序。
- Kahn 拓扑排序得 `order`；检测到环则断一条边继续，把环信息记入结果。
- **只对挂了文件的页执行导入**，但排序基于完整依赖关系，保证「被引用页若也在本批，则先导入」。

### 逐页导入执行（`useBatchImport.ts`，复用现有逻辑）

按 `order` 顺序，对每个有文件的页：

1. `parseImportFile(file, pageFields)` 解析 Excel；
2. 预盖保序 `_importId`（复用 `makeImportRowId`，保证自引用/同批顺序不断）；
3. `resolveRelation/Reference/Quote/CollectionSelectImportValues`（共享 `collectionCache`）——此时被引用页已落库，能解析到；
4. 生成自增序列值后批量创建（复用 `DynamicPage` 现有 `doImport` 的核心写库流程，抽到 composable 共用）。

> 实现说明：现有 `DynamicPage.doImport` 内含「解析→建序列→逐条创建」流程。将其核心抽取为 composable 可复用函数（如 `importRecordsForPage(pageId, records, onProgress)`），`DynamicPage` 与 `useBatchImport` 共用，避免逻辑分叉。

### 收尾

对本批所有导入过的页各跑一次 `pageConfigStore.reResolveReferences(pageId)`，补齐环/外部引用当场未匹配的值。

### 进度与结果

- 双层进度：当前页第 N 条 / 总第 M 页。
- 结束弹汇总表，每页：成功条数、失败条数、收尾重解析补齐条数、仍未匹配条数。
- 失败页不中断整批（沿用现有失败策略）。

### 边界

- 部分页留空 → 只导有文件的页。
- 被引用页未挂文件但库里已有数据 → 正常解析。
- 被引用页未挂文件且库里也没有 → 该引用先留原值，收尾重解析仍无则计入「未匹配」。

## 批量清空（`ClearTab.vue` + 后端接口）

### UI 流程

1. 选项目 + 选分支（默认 `main`）→ 列出该项目下所有数据页，含记录数。
2. 勾选要清空的页（可全选）→ 底部显示「将清空 N 个页、共 M 条记录」。
3. **二次确认**：弹窗要求手动输入确认词（固定词 `CLEAR`）才放行，防误删。

### 后端接口 `POST /menuExport/batchClear`

请求：

```json
{ "collections": ["orders", "products"], "branchId": "main" }
```

逻辑（**单事务**，返回每页删除条数）：

```sql
-- 清数据
DELETE FROM dynamic_data
WHERE collection = ANY(%s) AND branch_id = %s;

-- 清理悬挂的 M:N 关系（被清集合作为 source 或 target 都删）
DELETE FROM data_relations
WHERE branch_id = %s
  AND (collection = ANY(%s) OR related_collection = ANY(%s));
```

- `data_relations` 实际列：`collection / record_id / field_name / related_collection / related_id / branch_id`（已核实 `init_db.py:49`）。
- 失败回滚；写一条 `operation_log`（清空是重要操作，需可审计）。
- **权限**：`write_required`（guest 禁止）。批量清空较危险，**可按需收紧为 `admin`**；先按 `write_required` + 二次确认实现。
- 空 `collections` 返回 400。

## 错误处理

- 导入：单页解析失败 → 标记该页失败、继续其余页；单条写库失败 → 计入该页失败数不中断；拓扑成环 → 断边继续 + 结果提示环路径；收尾重解析失败 → 仅提示不影响已导入数据。
- 删除：事务失败整体回滚并返回明确错误；前端确认词不符则按钮禁用。

## 测试

- **`src/utils/__tests__/referenceTopoSort.test.ts`（重点，纯函数）**：线性链、分叉、菱形、自引用、跨页环、指向外部集合、空集合等用例。
- **`src/composables/__tests__/useBatchImport.test.ts`**：mock `pageConfigStore` 方法，验证「按拓扑序调用各页导入 + 收尾对每页调用 `reResolveReferences`」。
- **后端 `server/tests/test_routes_menu_export.py` 扩展**：`batchClear` 的 SQL 含 `collection ANY` + branch 过滤 + `data_relations` 清理、权限拦截（guest 403）、空 collections 报错、操作日志写入。

## 影响文件清单

新增：
- `src/views/admin/DataMigrationPage.vue`（容器，三 Tab）
- `src/views/admin/data-migration/ExportTab.vue`（迁入现有导出逻辑）
- `src/views/admin/data-migration/ImportTab.vue`
- `src/views/admin/data-migration/ClearTab.vue`
- `src/utils/referenceTopoSort.ts`
- `src/composables/useBatchImport.ts`
- 对应测试文件

修改：
- `src/router/index.ts`：`menu-export` 路由 component 指向 `DataMigrationPage`，`meta.title` 改「数据迁移」（path 不变）
- `src/api/menu.ts`：新增 `batchClear` 调用（按项目列页可复用现有预览/可用菜单接口）
- `src/views/dynamic/DynamicPage.vue`：抽取 `doImport` 核心为共用函数（或迁入 composable）
- `server/routes/menu_export.py`：新增 `POST /menuExport/batchClear`
- `server/utils/menu_export.py`：新增 `batch_clear()`
- `server/tests/test_routes_menu_export.py`：新增 batchClear 测试

> 旧文件 `MenuExportPage.vue` 在导出逻辑迁出后删除（或保留为薄壳）。
