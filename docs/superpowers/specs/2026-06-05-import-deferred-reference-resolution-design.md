# 数据导入：引用字段延迟解析 + 手动重新解析

**Date:** 2026-06-05
**Status:** Approved (design)

## 问题背景

数据页导入时，`quoteSelect`（引用选择）和 `reference`（1:N 引用）字段的列填的是**目标记录的主键值/显示值**。导入解析器（`src/stores/pageConfig.ts` 的 `resolveQuoteImportValues`、`resolveReferenceImportValues`）把这些值**按目标集合的当前数据**解析成内部 id。因此存在硬性顺序依赖：

- 先导入被引用页、再导入引用页 → 解析成功。
- 先导入引用页（此时目标页还没数据）→ 解析失败：
  - **quoteSelect**：未匹配值被**丢弃**（`resolveQuoteImportValues` 只 push 解析成功的）→ 引用直接丢失。
  - **reference**：保留原始主键/显示值，但显示按内部 id 查父记录 → 查不到 → 显示空/失效。

用户希望：**即使被引用页后导入，引用也能生效**。

## 目标 / 非目标

- **目标：** 引用页可在被引用页之前导入；导入完被引用页后，通过手动「重新解析引用」把未解析的引用补全为有效 id。覆盖 **quoteSelect + reference**。
- **非目标：** 不覆盖 `relation`（M:N，存 `data_relations`，改写复杂、风险高）；不改后端、不改 schema；不做自动回溯（仅手动按钮触发）。

## 核心洞察

"重新解析"本质就是**把导入解析逻辑重新跑在已存储的数据上**。`resolveQuoteImportValues` / `resolveReferenceImportValues` 的解析顺序本就是：已是内部 id → 保留；否则按主键值/显示值查 → id。只要导入时**不丢弃**未匹配值（以原始值持久化），之后重跑同一解析器即可补全。

## 方案

### 1. 导入：不再丢弃未解析的 quoteSelect 值

`resolveQuoteImportValues`（`pageConfig.ts:1283` 附近）当前：值匹配不上则不 push（丢弃）。改为：**匹配不上时保留原始主键/显示值**（仍用 `seen` 去重）。这样未解析引用以原始值持久化、可恢复。

```ts
for (const v of vals) {
  if (idSet.has(v)) {                       // 已是内部 id
    if (!seen.has(v)) { seen.add(v); resolved.push(v) }
  } else {
    const ids = pkToId.get(v) || displayToIds.get(v)   // 解析
    if (ids) {
      for (const id of ids) if (!seen.has(id)) { seen.add(id); resolved.push(id) }
    } else {
      // 改动：保留原始值（之前是丢弃），供之后重新解析
      if (!seen.has(v)) { seen.add(v); resolved.push(v) }
    }
  }
}
```

`reference` 解析器（`resolveReferenceImportValues`）已保留原始值（仅在解析成功时覆盖），**导入侧无需改动**。

### 2. 新 store action：`reResolveReferences(pageId)`

```
reResolveReferences(pageId): Promise<{ resolved: number; stillPending: number }>
```

- 拉取该页全部记录（`get('/<collection>', { all: true })` 或现有"加载全部"路径）。
- 快照每条记录的 quoteSelect/reference 字段值。
- 对记录数组跑 `resolveQuoteImportValues(pageId, records)` 与 `resolveReferenceImportValues(pageId, records)`（针对当前目标数据）。
- 对比快照，找出 quote/reference 字段**发生变化**的记录。
- 把变化记录通过 `batch-create`（`/<collection>/batch-create`，`continueOnError:true`）upsert 回写：`{ id, data: <完整字段>, relations: {} }`。`relations` 传空不会删除已有关联（`batch_create` 对 relations 是 `ON CONFLICT DO NOTHING` 插入，不删除），且 quoteSelect/reference 都在 JSONB `data` 内。
- 统计：`resolved` = 本次新解析出 id 的字段值数；`stillPending` = 仍为原始值（目标仍缺）的字段值数。

### 3. UI：数据页「重新解析引用」

- 在 `DynamicPage.vue` 的「更多」下拉里加一项「重新解析引用」，**仅当该页含 quoteSelect 或 reference 字段时显示**。
- 点击 → 调 `reResolveReferences(pageId)` → 重新加载表格 → toast：`已解析 N 条引用，仍有 M 条未匹配`（M=0 时只报成功）。

### 数据流

```
导入引用页（被引用页尚无数据）→ quoteSelect/reference 存"原始主键/显示值"（不丢弃）
…稍后导入被引用页…
点「重新解析引用」→ 重跑解析器 → 原始值换成内部 id → 引用生效（显示/导出/图谱恢复正常）
```

## 错误处理 / 边界

- **降级可恢复**：未解析期间 quoteSelect/reference 显示原始值（而非消失/空）；重解析后恢复。严格优于现状（quoteSelect 原本直接丢失）。
- 目标集合加载失败：解析器现有 `try/catch` 跳过逻辑不变；该字段保持原始值。
- 重复点击「重新解析」：幂等——已是 id 的值被 `idSet` 命中保留，不受影响。
- 仍未匹配（目标确实没有该记录）：保持原始值，计入 `stillPending`，下次再点可继续补。
- 无变化时：不发起任何 upsert，toast 提示"无需更新"。

## 测试

- **`resolveQuoteImportValues` 目标缺失保留原始值**：目标集合无匹配记录 → 断言导入值数组保留原始主键/显示值（不为空）。
- **`resolveQuoteImportValues` 目标存在仍解析为 id**：回归，行为不变。
- **`reResolveReferences`**：
  - 记录的 quoteSelect 存了原始主键值、目标现已存在 → 断言被解析为内部 id，且回写（mock `get` 返回记录、`post` 捕获 batch-create payload），返回 `resolved` 计数正确。
  - reference 字段同理。
  - 目标仍缺失 → 保持原始值、`stillPending` 计数正确、不回写该记录。

## 涉及文件

- 修改：`src/stores/pageConfig.ts`（`resolveQuoteImportValues` 保留原始值；新增 `reResolveReferences`）
- 修改：`src/views/dynamic/DynamicPage.vue`（「更多」下拉新增「重新解析引用」+ 处理函数 + 条件显示）
- 修改：`src/stores/__tests__/pageConfig.test.ts`（新增用例）
