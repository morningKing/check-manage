# 修复：引用/关联字段导出为内部 id

**Date:** 2026-06-05
**Status:** Approved (design)

## 问题背景

数据页导出 Excel 时，`quoteSelect`（引用选择）和 `relation`（关联）字段在某些页面导出的是**内部记录 id**（如 `products-0001-abc`），而不是用户在界面看到的"真实值"（如「产品名称」）。

根因（已查证 + 用真实配置确认）：

- 导出时 `valueToLabel`（`src/utils/excel.ts:57-85`）对 `relation`/`quoteSelect` 字段用 `relationDisplayMap?.[fieldName]` 这个 id→显示值的 Map 做替换；**Map 缺失时回退到 `value.join('、')`，即原始内部 id**。
- 这个 Map 由 `fetchRelationDisplayMaps`（`src/stores/pageConfig.ts:1054`）和 `fetchQuoteDisplayMaps`（`:1203`）构建。两者都有 `const pkField = getTargetPrimaryKeyField(...); if (!pkField) continue` —— **当目标集合没有主键字段时直接跳过，不生成该字段的 Map** → 导出回退到 id。
- 实测：`page-orders` 的 `quoteSelect` 字段 → 目标 `products`，而 `products` **无主键字段**（`targetPK=[]`），正中此路径。

（`reference` 1:N 字段走 `_ref_*_display`（`parent[displayField] || parent.id`），displayField 已设置时本就正常，不在本次范围。）

## 目标 / 非目标

- **目标：** 当目标集合无主键时，`quoteSelect`/`relation` 字段导出该字段配置的 `displayField`（界面显示的"真实值"），不再导出内部 id。
- **非目标：** 不改有主键页面的导出行为（仍导出主键值，保证可重新导入）；不改 `reference` 字段；不动后端 / schema。

## 方案

在 `fetchRelationDisplayMaps` 与 `fetchQuoteDisplayMaps` 中，用 `labelField = pkField || config.displayField` 取代"无主键就跳过"：

```ts
// 之前
const pkField = getTargetPrimaryKeyField(config.targetCollection)
if (!pkField) continue
...
const pkVal = r[pkField]
if (pkVal) idToPk.set(r.id, String(pkVal))

// 之后
const pkField = getTargetPrimaryKeyField(config.targetCollection)
const labelField = pkField || config.displayField   // 无主键时回退 displayField
if (!labelField) continue                            // 两者都没有，保持回退 id
...
const val = r[labelField]
if (val) idToLabel.set(r.id, String(val))
```

- `fetchRelationDisplayMaps`：`config = field.relationConfig`，用 `config.displayField`。
- `fetchQuoteDisplayMaps`：`config = field.quoteConfig`，用 `config.displayField`。

### 正确性 / 取舍

- **有主键页面**：`labelField = pkField`，行为完全不变（导出主键值，可重新导入）。✓
- **无主键但有 displayField**：导出 displayField 值（用户期望的真实值）。导入侧 `resolveQuoteImportValues`/`resolveRelationImportValues` 本就同时按主键值与 displayField 匹配（`pageConfig.ts` 的 `pkToId` + `displayToId`），所以导出的 displayField **仍能被重新导入**。✓
- **既无主键又无 displayField**：`continue`，`valueToLabel` 回退到 id（无可用真实值，唯一可行）。

## 错误处理 / 边界

- 目标集合加载失败：保持原有 `try/catch` 跳过逻辑不变。
- `displayField` 在某记录上为空：该记录 `if (val)` 不入 Map，`valueToLabel` 对该 id 回退到 id（与现状一致，单条缺失不影响其它）。
- 多个目标记录有相同 displayField：Map 以 id 为 key，互不覆盖，导出各自的显示值；不影响正确性。

## 测试

`src/stores/__tests__/pageConfig.test.ts` 新增（沿用该文件既有的 `get` mock 方式）：

- **`fetchQuoteDisplayMaps` 无主键回退 displayField**：mock 目标集合返回 `[{id:'p1', 产品名称:'苹果'}, {id:'p2', 产品名称:'香蕉'}]`、无主键字段、quoteConfig.displayField='产品名称' → 断言返回 `{ <field>: Map(p1→'苹果', p2→'香蕉') }`（不是空、不是 id）。
- **`fetchQuoteDisplayMaps` 有主键仍用主键值**：目标含主键字段 → 断言 Map 用主键值（行为不变）。
- **`fetchRelationDisplayMaps` 无主键回退 displayField**：同上，针对 relationConfig。

## 涉及文件

- 修改：`src/stores/pageConfig.ts`（`fetchRelationDisplayMaps`、`fetchQuoteDisplayMaps` 各一处）
- 修改：`src/stores/__tests__/pageConfig.test.ts`（新增 3 个用例）
