# 数据导入：保序行 id（导入顺序与文件一致）

**Date:** 2026-06-05
**Status:** Approved (design)

## 问题背景

数据页「导入数据」后，列表里的顺序通常**与导入文件的行顺序不一致**。根因（已查证）：

1. 后端 `batch-create`（`server/routes/dynamic.py:1037-1045`）用 `execute_values` 批量 INSERT，**不写 `created_at`**，走列默认值 `now()`。
2. `now()` 返回事务时间 —— 同一批（一次多行 INSERT，≤500 行）里**所有行 `created_at` 完全相同**（已用临时表实测：5 行 → 1 个 distinct 时间戳）。
3. 列表查询 `ORDER BY created_at, id`（`dynamic.py:439`、`:446`）。`created_at` 全相等 → 实际按 **`id`** 排。
4. 行 `id` 在无显式 id 列时是随机生成的 `` `${collection}-${Math.random().toString(36).slice(2,10)}` ``（`src/views/dynamic/DynamicPage.vue:2526`）→ 批内顺序 ≈ 随机。

表现：≤500 行单批 → 显示顺序随机；>500 行 → 批间（每 500 一批、各自一次 HTTP 请求/事务）相对有序，批内随机。

## 目标 / 非目标

- **目标：** 导入后列表默认顺序与文件行顺序一致。纯前端实现，不改 schema、不改后端查询、不伪造时间戳。
- **非目标：** 不引入显式排序列；不改变 `ORDER BY created_at, id`；不处理用户后续手动改 `created_at` 等场景。

## 方案（保序行 id）

让同一批（相同 `created_at`）内的行 id **按文件位置字典序排列**：在 id 内嵌入一个零填充的全局序号。`ORDER BY created_at, id` 于是天然按文件顺序返回。

### ID 格式

```
${collection}-${paddedSeq}-${rand}
```

- `paddedSeq` = 该行在 `records` 数组（= 文件顺序）中的全局索引，零填充到 `width = String(records.length).length` 位。定宽保证字典序 == 数字序（避免 "10" < "9" 这类变长比较错误）。
- `rand` = 6 位 base36 随机后缀，保证跨多次导入的全局唯一（`(id, branch_id)` 是 `dynamic_data` 主键，不能撞）。批内 `paddedSeq` 已唯一，`rand` 仅作跨导入兜底；字典序比较先在 `paddedSeq` 段分胜负，故不影响排序。

### 正确性

- **≤500 行（单批，created_at 相同）**：按 `id` == 按 `paddedSeq` == 文件顺序。✓
- **>500 行（多批、多事务）**：批间 `created_at` 递增 → 批序保留；批内按 `paddedSeq` 保序。✓
- **跨多次导入**：各次导入事务的 `created_at` 不同，互不干扰；`rand` 保证 id 全局不撞（碰撞需 collection+seq+rand+branch 全同，约 1/36⁶，可忽略，且不弱于现有随机方案）。✓

## 改动点（最小化）

### 1. 新增纯函数 `src/utils/importId.ts`

```ts
makeImportRowId(collection: string, index: number, total: number): string
```

返回 `${collection}-${pad(index, width)}-${rand}`，`width = String(Math.max(total, 1)).length`。

### 2. `src/views/dynamic/DynamicPage.vue` 的 `doImport`

在 `await Promise.all([...resolve*ImportValues...])`（`:2473-2478`）**之前**，给每条 record 预盖 `_importId`：

```ts
records.forEach((r, i) => {
  if (!r._importId) r._importId = makeImportRowId(collection.value, i, records.length)
})
```

效果：
- **所有**行（含自引用中被引用的行）都拿到保序 id；
- `pageConfig` 的引用解析（`resolveCollectionSelectImportValues` 等）用的是 `record._importId`（已预盖，`pageConfig.ts:1360` 的 `if (!record._importId)` 随机生成分支不再触发）→ 自引用值与最终行 id 一致，**引用不破**；
- `DynamicPage.vue:2526` 的 `id: importId || <random>` 因 `_importId` 总有值而采用保序 id → **该行无需改动**（保留 `|| <random>` 作防御兜底）。

### 不改动

- `server/routes/dynamic.py`（`ORDER BY created_at, id` 不变，靠 id 保序即可）。
- `src/stores/pageConfig.ts`（已优先使用 `_importId`，预盖后其随机分支自然成为死代码，无需删除）。
- 后端 schema / `created_at` 默认值。

## 数据流（修复后）

```
解析文件 → records（文件顺序）
doImport:
  records.forEach 预盖 _importId = makeImportRowId(collection, i, total)   ← 新增
  Promise.all(resolve*ImportValues)  // 自引用解析用已预盖的 _importId
  batchData.map: id = _importId(保序)  // 行 id 即保序 id
  POST /<collection>/batch-create  // 同批 created_at 相同
列表 GET: ORDER BY created_at, id → 同批按保序 id == 文件顺序
```

## 错误处理 / 边界

- `total = 0`：`doImport` 在 `records.length === 0` 时已提前返回（`handleFileSelected` 中 `ElMessage.warning('文件中没有可导入的数据')`），不会进入。`width = String(Math.max(total,1)).length` 兜底避免 0 宽。
- 超大导入（> 10^width）：`width` 按本次 `records.length` 动态计算，单次导入内所有行同宽 → 始终正确。
- 已有 `_importId`（理论上 doImport 入口不会有）：`if (!r._importId)` 保护，不覆盖。

## 测试

- **`src/utils/__tests__/importId.test.ts`**（新）：
  - 前缀为 `${collection}-`；
  - 定宽零填充：`total=1000` → seq 段 4 位；`index=5` → `0005`；
  - 索引 `0..N-1` 生成的 id 数组，**`[...].sort()` 后顺序 == 原索引顺序**（核心保序断言，覆盖跨 10 的幂边界，如 total=12 时 9 与 10）；
  - 含随机后缀（同 index 两次调用 id 不同）。
- 现有 `src/utils/__tests__/fieldImport.test.ts` 与 `src/stores/__tests__/pageConfig.test.ts` 导入相关用例保持绿（确认预盖逻辑不破坏自引用解析）。

## 涉及文件

- 新增：`src/utils/importId.ts`、`src/utils/__tests__/importId.test.ts`
- 修改：`src/views/dynamic/DynamicPage.vue`（`doImport` 预盖 `_importId`，导入 `makeImportRowId`）
