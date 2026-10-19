# 导入历史（持久化导入结果 + 跨会话重试/导出）— Design Spec

## Background

数据页"导入数据"的进度/结果目前只在一个 `el-dialog` 里呈现：解析进度、
按文件分组的成功/失败统计、"导出失败清单"/"重试失败记录"入口。这些数据
只存在于 `useMultiFileImport` composable 的内存状态里——弹窗一旦关闭（用户
点确定，或刷新页面/切换页面），这些操作入口就再也找不到了，即使数据本身
在关闭弹窗那一刻并未被清空（只是没有 UI 能重新打开它）。刷新页面/关掉浏览
器标签页后，数据彻底丢失。

用户要求：导入结果需要跨会话持久（后端记录），失败记录支持直接重试（不是
只能导出查看）。

## Decisions (confirmed with user)

1. **持久化范围**：跨会话持久化，后端新增数据表，而不是仅在当前会话内加一
   个"重新打开弹窗"的入口。
2. **重试能力**：历史记录里的失败记录支持直接"重试"（复用现有
   `retryImportFailures` 的整套语义），需要把当时的完整请求体
   `payload: {id, data, relations}` 存进数据库（参照 `webhook_logs` 已有的
   JSONB `request_payload` 存储惯例）。
3. **会话内重试 ↔ 历史记录：实时同步**。用户在还开着的弹窗里点"重试失败
   记录"，历史记录里对应的失败行也要同步移除、计数同步更新；反过来历史页
   的重试也是同样效果。两边任何时候看到的数字必须一致。

## Scope

只覆盖数据页"导入数据"这一个入口（`DynamicPage.vue` 的多文件顺序导入流程，
`src/composables/useMultiFileImport.ts` + `src/utils/importPageRecords.ts`）。
**不**涉及：
- `src/composables/useBatchImport.ts`（跨页批量导入，`ImportTab.vue` 用，另一
  个独立功能入口）
- Open API 批量创建（`server/routes/open_api.py`）
- ETL 导入（已经有自己独立的异步执行 + 日志体系，见 `etl_logs`）

`importPageRecords()`（两个功能共用的核心上传函数）本身**不改动任何逻辑**，
只在其返回类型上新增一个可选字段（见下）——这个改动对 `useBatchImport.ts`
完全透明、无影响。

**刻意不改动** `src/composables/useMultiFileImport.ts`（已上线并经过完整审查
的 composable）。本次所有新逻辑都放在 `DynamicPage.vue`（它本来就能直接读
`multiImport.state`，不需要经过 composable 的 deps 接口）和新的后端/前端
文件里。

## Architecture

### 数据库

`server/init_db.py` 新增两张表（迁移块追加在文件末尾同类迁移代码之后，参照
`webhook_logs` 的迁移写法：`information_schema` 判断表不存在才建）：

```sql
CREATE TABLE IF NOT EXISTS import_runs (
    id              VARCHAR(100) PRIMARY KEY,
    page_id         VARCHAR(100) NOT NULL,
    collection      VARCHAR(200) NOT NULL,
    branch_id       VARCHAR(100) NOT NULL DEFAULT 'main',
    file_name       VARCHAR(500) NOT NULL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    created_count   INTEGER NOT NULL DEFAULT 0,
    updated_count   INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'success',  -- 'success' | 'partial'
    operator        VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_runs_page_created ON import_runs(page_id, created_at DESC);

CREATE TABLE IF NOT EXISTS import_run_failures (
    id              VARCHAR(100) PRIMARY KEY,
    run_id          VARCHAR(100) NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    record_id       VARCHAR(200) NOT NULL,
    original_record JSONB NOT NULL,
    payload         JSONB NOT NULL,
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_run_failures_run_id ON import_run_failures(run_id);
```

`status` 只有两个值：`failed_count === 0 ? 'success' : 'partial'`——和前端
`FileImportEntry.status`（`success`/`partial`）的判定逻辑保持同一口径。
导入历史只记录"真正执行了上传"的文件（解析失败/空文件/被取消跳过的文件不
会调用上传，自然不会有 run 记录，这是既有行为，不需要特殊处理）。

`record_id` = `payload.id`（导入记录的确定性/随机 id，见
`makeStableImportId`/`makeImportRowId`），用作重试后定位"这条失败记录解决
了"的锚点。

### 后端：`server/routes/import_runs.py`（新文件，蓝图 `import_runs_bp`）

参照 `server/routes/webhooks.py` 的 `/webhook/logs`、`/webhook/logs/<id>`
写法（分页、`try/except` 包一层返回 500、`row_to_dict` 风格），权限校验用
`require_page_action(collection, action)`（跟 `dynamic.py` 自己的路由一致，
而不是 webhook 那种全局 `admin.*` 权限——导入历史是"谁能导入这个数据页，
谁就能看这个数据页的导入历史"）。

```python
POST /importRuns
  权限: require_page_action(collection, 'create')
  Body: {
    "pageId": str, "collection": str, "branchId": str, "fileName": str,
    "successCount": int, "createdCount": int, "updatedCount": int, "failedCount": int,
    "failures": [
      {"recordId": str, "originalRecord": {...}, "payload": {...}, "reason": str}, ...
    ]
  }
  行为: status = 'success' if failedCount == 0 else 'partial'；
        单个事务内 INSERT import_runs 一行 + failures（如果有）批量 INSERT
        （execute_values，参照 dynamic.py batch_create_items 的批量写法）。
  返回: {"id": "<runId>"}, 201

GET /importRuns?pageId=<pageId>&collection=<collection>&limit=&offset=
  权限: require_page_action(collection, 'read')
  行为: WHERE page_id = %s ORDER BY created_at DESC LIMIT/OFFSET 分页
        （limit 默认 20，上限 100，参照 webhook list_logs 的写法）。
        不返回 failures 明细（列表页只要汇总数）。
  返回: {"runs": [{id, fileName, branchId, successCount, createdCount,
                   updatedCount, failedCount, status, operator, createdAt}, ...],
         "total": N}

GET /importRuns/<id>
  权限: 先按 id 查出该行的 collection，再 require_page_action(collection, 'read')
  行为: 查 run 本身 + 该 run 的全部 import_run_failures
  返回: {run: {...同上}, failures: [{recordId, originalRecord, payload, reason, createdAt}, ...]}
  404: run 不存在时返回 {"error": "..."}, 404

POST /importRuns/<id>/retry-result
  权限: 先查出该行的 collection，再 require_page_action(collection, 'create')
  Body: {
    "resolvedRecordIds": [str, ...],
    "successDelta": int, "createdDelta": int, "updatedDelta": int
  }
  行为（单事务）:
    DELETE FROM import_run_failures WHERE run_id = %s AND record_id = ANY(%s)
    UPDATE import_runs SET
      success_count = success_count + %(successDelta)s,
      created_count = created_count + %(createdDelta)s,
      updated_count = updated_count + %(updatedDelta)s,
      failed_count = failed_count - %(resolvedCount)s,
      status = CASE WHEN failed_count - %(resolvedCount)s <= 0 THEN 'success' ELSE 'partial' END
    WHERE id = %s
    （resolvedCount = len(resolvedRecordIds)；resolvedRecordIds 为空数组时
    直接返回当前行状态，不做无意义的 UPDATE/DELETE）
  返回: 更新后的 run 汇总 {successCount, createdCount, updatedCount, failedCount, status}
```

`app.py` 里 `import_runs_bp` 注册在其它非 `dynamic_bp` 蓝图之间（`dynamic_bp`
必须最后注册，这是现有规则）；`RESERVED`（`server/routes/dynamic.py`）新增
`'importRuns'`（跟路由前缀 `/importRuns` 一致——`RESERVED` 里像
`etlTasks`/`apiKeys`/`triggerRules` 这类驼峰式路由用的就是驼峰字符串本身,
不是 kebab-case），防止用户把数据页 collection 取名叫 `importRuns` 和这个
新路由前缀冲突。

### 前端

**`src/api/importRuns.ts`**（新文件，参照 `src/api/etl.ts` 的风格）：

```typescript
import { get, post } from '@/utils/request'

export interface ImportRunSummary {
  id: string
  fileName: string
  branchId: string
  successCount: number
  createdCount: number
  updatedCount: number
  failedCount: number
  status: 'success' | 'partial'
  operator: string
  createdAt: string
}

export interface ImportRunFailureRow {
  recordId: string
  originalRecord: Record<string, any>
  payload: { id: string; data: any; relations: Record<string, string[]> }
  reason: string
  createdAt: string
}

export function createImportRun(body: {
  pageId: string; collection: string; branchId: string; fileName: string
  successCount: number; createdCount: number; updatedCount: number; failedCount: number
  failures: Array<{ recordId: string; originalRecord: any; payload: any; reason: string }>
}): Promise<{ id: string }> {
  return post('/importRuns', body)
}

export function listImportRuns(
  pageId: string, collection: string, limit = 20, offset = 0
): Promise<{ runs: ImportRunSummary[]; total: number }> {
  return get('/importRuns', { pageId, collection, limit, offset })
}

export function getImportRunDetail(
  id: string
): Promise<{ run: ImportRunSummary; failures: ImportRunFailureRow[] }> {
  return get(`/importRuns/${id}`)
}

export function syncImportRunRetryResult(
  id: string,
  body: { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number }
): Promise<{ successCount: number; createdCount: number; updatedCount: number; failedCount: number; status: string }> {
  return post(`/importRuns/${id}/retry-result`, body)
}
```

**`src/utils/importPageRecords.ts`**：`ImportPageResult` 新增一个可选字段
（纯类型改动，函数本身逻辑不变）：

```typescript
export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
  failures: ImportFailure[]
  runId?: string | null   // 本次上传对应的导入历史记录 id；由调用方（DynamicPage.vue）
                           // 在 importPageRecords() 返回后另行 POST /importRuns 得到，
                           // importPageRecords() 自己永远不会设置这个字段
}
```

新增一个纯函数（同文件，供 `DynamicPage.vue` 和后面的导入历史抽屉共用，
避免"计算这次重试解决了哪些失败记录"的 diff 逻辑写两遍）：

```typescript
/**
 * 计算一次重试前后的差集：哪些失败记录这次解决了（不在新的 failures 里
 * 但在旧的里），以及本次重试贡献的成功/新增/更新增量——供调用方同步到
 * /importRuns/<id>/retry-result。
 */
export function diffRetryResult(
  before: ImportFailure[],
  retryResult: ImportPageResult
): { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number } {
  const stillFailingIds = new Set(retryResult.failures.map((f) => f.payload.id))
  const resolvedRecordIds = before
    .map((f) => f.payload.id)
    .filter((id) => !stillFailingIds.has(id))
  return {
    resolvedRecordIds,
    successDelta: retryResult.success,
    createdDelta: retryResult.created,
    updatedDelta: retryResult.updated,
  }
}
```

**`src/views/dynamic/DynamicPage.vue`**：

- `uploadImportedRecords`（已存在，多文件导入功能引入）在
  `importPageRecords(...)` 返回后，取当前文件名（
  `multiImport.state.fileResults[multiImport.state.currentFileIndex]?.file.name`
  ——此时组合式函数的循环已经把 `currentFileIndex` 指向这个文件，读取安全），
  best-effort（`try/catch`，失败只 `console.warn`，不影响导入本身）调用
  `createImportRun(...)`，把返回的 `runId` 合并进最终返回给 composable 的
  `ImportPageResult`。
- `handleRetryFileFailures`（已存在）在 `multiImport.retryFileFailures(index)`
  完成后，如果 `entry.result?.runId` 存在，用重试前的 `failures`
  （调用前先取一份快照）+ 重试后的 `entry.result` 调用
  `diffRetryResult` + `syncImportRunRetryResult`，best-effort（失败只
  `console.warn`，不影响页面表现——本地状态已经是对的，只是历史记录暂时没
  同步上，用户下次打开历史页会看到略微滞后的数字，不是数据错误）。
- "更多"下拉菜单新增一项"导入历史"，点击打开新的
  `ImportHistoryDrawer.vue`（`v-model` 控制可见性，传入 `pageId`/
  `collection`）。

**`src/components/dynamic-import/ImportHistoryDrawer.vue`**（新文件）：

- `el-drawer`，打开时调用 `listImportRuns` 拉取分页列表，按时间倒序展示
  （文件名、时间、操作人、状态 tag、成功/失败数）。
- 点击一行 → 调用 `getImportRunDetail` 展开该行的失败明细（原始数据/失败
  原因），失败数 > 0 时展示"导出失败清单"（直接复用现成的
  `exportImportFailures`，把 `ImportRunFailureRow[]` 映射成
  `ImportFailure[]` 形状后调用）/"重试失败记录"按钮。
- "重试失败记录"：把当前展开的 `failures`（`ImportRunFailureRow[]`）映射成
  `ImportFailure[]`，调用现成的 `retryImportFailures(post, collection,
  failures, onProgress)`（不改动这个函数），拿到结果后：
  1. 本地更新这一行展开的失败列表（去掉已解决的）和顶层汇总数字；
  2. 调用 `diffRetryResult` + `syncImportRunRetryResult` 把结果落回后端
     （这次不是 best-effort——这就是历史页操作的主路径，失败要提示用户，
     参照现有错误提示的 `ElMessage.error` 惯例）。
- 分页（`el-pagination`，跟列表 API 的 `limit`/`offset` 对应）。

## Data Flow

```
（导入侧，写入）
DynamicPage 顺序处理某个文件 → importPageRecords() 返回 ImportPageResult
  → uploadImportedRecords 取当前文件名，POST /importRuns（best-effort）
    → 拿到 runId，合并进返回值 → entry.result.runId 可用

（会话内重试，双向同步的一侧）
弹窗点"重试失败记录" → multiImport.retryFileFailures(index)（现有逻辑不变）
  → handleRetryFileFailures 用重试前后差异 diffRetryResult
    → POST /importRuns/<runId>/retry-result（best-effort）

（历史页，读 + 重试，双向同步的另一侧）
"更多"→"导入历史" → ImportHistoryDrawer 打开
  → GET /importRuns 列表 → 点开一行 → GET /importRuns/<id> 详情
  → "重试失败记录" → 复用 retryImportFailures()
    → diffRetryResult → POST /importRuns/<id>/retry-result（阻塞、失败要提示）
    → 本地也更新展开的失败列表/汇总数字
```

## Error Handling

- 历史记录的**写入**（`POST /importRuns`）和**会话内重试的同步**
  （`POST /importRuns/<id>/retry-result` 从弹窗触发的那次）都是
  best-effort：失败不影响导入/重试本身的用户体验，只在控制台警告。这是
  一个"锦上添花"的审计功能，不应该因为网络抖动让用户重试失败。
- 历史页里的操作（列表加载、详情加载、重试同步）走正常的错误提示路径
  （`ElMessage.error`），因为这是用户主动发起、明确在等待结果的操作。
- `POST /importRuns/<id>/retry-result` 的 `resolvedRecordIds` 为空数组时
  （这次重试一个都没成功）后端直接返回当前状态，不做空的
  UPDATE/DELETE。

## Testing

- **后端（pytest，新文件 `server/tests/test_routes_import_runs.py`，参照
  `test_routes_webhook.py`/其他 routes 测试的真实-DB 或 mocked-cursor 惯例，
  跟随该测试目录已有的主流模式）**：
  - `POST /importRuns` 正确写入 run + failures，返回 id；`failedCount=0`
    时 `status='success'`，否则 `'partial'`。
  - `GET /importRuns` 按 `page_id` 过滤、按 `created_at DESC` 排序、分页
    参数生效。
  - `GET /importRuns/<id>` 返回完整 failures；不存在的 id 返回 404。
  - `POST /importRuns/<id>/retry-result`：`resolvedRecordIds` 对应的
    failures 行被删除，run 的四个计数按 delta 更新，`status` 按新
    `failed_count` 重新计算；空 `resolvedRecordIds` 不报错、原样返回。
  - 权限：非该数据页 `create`/`read` 权限的角色调用被拒绝（403）。
- **前端（Vitest）**：
  - `diffRetryResult`（纯函数，`src/utils/__tests__/importPageRecords.test.ts`
    追加）：混合"部分解决/全部解决/一个都没解决"的场景，断言
    `resolvedRecordIds`/三个 delta 计算正确。
  - `ImportHistoryDrawer.vue`：mock `src/api/importRuns.ts`，测试列表渲染、
    展开详情、重试成功后本地列表更新、导出按钮触发
    `exportImportFailures`（mock 掉，只断言调用参数）。
- **Playwright（CLAUDE.md 强制项）**：真实导入一个含失败记录的文件 → 关闭
  弹窗 → 打开"导入历史" → 确认能看到刚才的记录和失败数 → 展开详情、
  "重试失败记录"、确认成功后该行失败数下降 → 刷新整个页面 → 重新打开
  "导入历史" → 确认数据还在（跨会话持久的核心验证点）→ 再验证一次
  "会话内弹窗重试 → 历史页数字同步更新"（在同一次导入的弹窗还开着时先在
  弹窗里重试一部分，再打开历史页确认数字已经是重试后的）。
