# 定时 AI 数据流水线（Scheduled AI Row-Processor）设计

- 日期：2026-06-03
- 状态：已评审，待实现
- 作者：jayKim + Claude

## 1. 背景与目标

这是一个**通用范式**，不是单一的「审核」功能：

> 按调度周期**扫描某个数据页**，把每条待处理记录连同其**字段数据 + 附件文档**作为上下文交给一个 AI 会话，AI 按**提示词（引用某个 skill）**工作，产出**结构化 JSON**，流水线解析后**回写到该记录的指定列**并流转状态。

「方案审核」只是其中一种配置；同一个引擎也能做：需求扫描分析、自动分类/打标、信息抽取补全、合规检查等。差异**全部在配置里**——目标数据页、状态字段与取值、提示词与 skill、上下文字段、字段映射、调度周期。

**核心复用**：AI 会话执行**完全复用现有 AI 批任务引擎与「批任务」面板**（`utils/batch_engine.py` 的 `BatchWorker`：3 并发 / 超时 / 重试 / 每会话独立工作区 / 可点进去看完整对话）。每次扫描 = 一个批任务，每条记录 = 一个子会话。

**关键决策（来自评审）**

| 决策点 | 结论 |
|--------|------|
| 回写方式 | 流水线解析 AI 末尾的结构化 JSON，按「字段映射」写到记录的指定列 |
| 记录选取与防重 | **状态字段三态流转**：待处理 → 处理中 → 已处理/处理失败（原子认领） |
| 上下文组成 | 记录字段（`record.md`）+ 附件文档（file/image 字段 → `data_files` 拷入工作区）|
| 提示词/skill | 用户写操作提示词（引用 skill）；**系统自动追加「输出 JSON 契约」**（由字段映射生成）|
| 配置入口 | 新建专门的「AI 定时任务」管理页 + 新配置表 |
| 执行与可观测 | 复用 AI 批任务引擎 + 面板；执行集成方式采用 **Approach A：完成回调钩子** |

**非目标（V1，YAGNI）**：跨集合回写 / 在其他表建记录（可由 skill 经 MCP 自行完成）、cron 表达式（仅按分钟间隔）、多分支扫描、行级重试策略。

## 2. 数据模型

### 2.1 新表 `ai_scan_tasks`（一行 = 一个定时任务）

```sql
ai_scan_tasks(
  id              text PRIMARY KEY,
  name            text NOT NULL,
  enabled         bool NOT NULL DEFAULT true,
  owner_user_id   text NOT NULL REFERENCES users(id),   -- 批任务/工作区/MCP token/RBAC 的身份
  collection      text NOT NULL,                        -- 目标数据页（扫 dynamic_data）
  branch_id       text NOT NULL DEFAULT 'main',
  status_field    text NOT NULL,                        -- 记录 JSONB 里用于进度/防重的字段名（任意字段）
  pending_value   text NOT NULL DEFAULT '',             -- 待处理值（空 = 匹配 null/空）
  running_value   text NOT NULL DEFAULT '处理中',
  done_value      text NOT NULL DEFAULT '已处理',
  failed_value    text NOT NULL DEFAULT '处理失败',
  extra_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 可选 MongoDB 风格过滤，叠加在待处理状态之上
  context_fields  JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {includeAllFields:bool, fields:[...], fileFields:'auto'|[...]}
  prompt_template text NOT NULL,                        -- 操作指令（引用 skill）
  field_mapping   JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{jsonKey, column, required}] AI JSON → 记录列
  schedule_interval_minutes int NOT NULL DEFAULT 15,
  max_records_per_scan      int NOT NULL DEFAULT 20,
  last_run_at     timestamptz,
  last_scan_count int DEFAULT 0,
  last_error      text,
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
)
```

### 2.2 扩展 `ai_chat_sessions`（沿用批任务列的幂等 ALTER 方式）

```sql
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS scan_task_id     text NULL REFERENCES ai_scan_tasks(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_record_id text NULL;
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_scan
  ON ai_chat_sessions(scan_task_id, source_record_id);
```

这两列让完成钩子能定位「这条 AI 会话对应哪条记录、属于哪个任务」从而回写。

### 2.3 复用关系

每次扫描创建 1 行 `ai_chat_batches`（名如 `AI定时·<任务名>·<时间>`）+ N 个子 `ai_chat_sessions`，每个子会话带 `scan_task_id` + `source_record_id`，`batch_input_file` 指向该记录暂存的**上下文目录**。

### 2.4 RBAC

- `PERMISSION_CATALOG` 新增能力 key `admin.ai_scan`（分组「数据工具」）。
- 新菜单「AI 定时任务」(`/admin/ai-scan-tasks`)，roles=['admin']，在 `init_db`/`seed_data` 种子，并加入前端 `ADMIN_PATH_PERMISSION` 映射。

## 3. 调度与认领

### 3.1 调度器 `utils/ai_scan_scheduler.py`

APScheduler `BackgroundScheduler`，在 `app.py` 以 `WERKZEUG_RUN_MAIN` 守卫启动（与 backup/dependency 调度器并列）。**每分钟 tick**：加载启用任务，对 `last_run_at + interval_minutes` 到期者调用 `run_task(task)`。进程内**按任务加锁**，跳过上一次还在跑的任务。启动时先做一次**孤儿清扫**（见 3.3）。

### 3.2 原子三态认领（`utils/ai_scan_engine.py → run_task`）

候选匹配 = `collection` + `branch_id` + 待处理匹配 + 可选 `extra_filter`（用现有 `utils/mongo_query.translate` 转成 JSONB WHERE）。待处理匹配：`pending_value` 为空 → `(data->>status_field IS NULL OR data->>status_field='')`；否则 `data->>status_field = pending_value`。

一条语句完成认领（翻为 `running_value`），跨 tick / 跨进程都不会重复挑选：

```sql
WITH picked AS (
  SELECT id FROM dynamic_data
   WHERE collection=%s AND branch_id=%s AND <pending> AND <filter>
   ORDER BY created_at
   FOR UPDATE SKIP LOCKED LIMIT %s            -- max_records_per_scan
)
UPDATE dynamic_data d
   SET data = jsonb_set(d.data, ARRAY[%s], to_jsonb(%s::text)),   -- ARRAY[status_field]=running_value（参数化，防注入）
       updated_at = now(), version = d.version + 1
  FROM picked WHERE d.id = picked.id AND d.branch_id=%s
RETURNING d.id, d.data;
```

- 认领到 0 条 → 仅更新 `last_run_at`/`last_scan_count=0`，不建批任务。
- 认领到 N 条 → 构建上下文目录（§4）+ `create_batch`（§5），再记 `last_run_at`/`last_scan_count`。

### 3.3 一致性与孤儿

- 认领先独立提交（保证 `SKIP LOCKED` 跨进程生效）；若随后 `create_batch` 抛错，则把已认领记录**尽力回滚为 `pending_value`** 并写 `last_error`。
- 进程在「认领后、建批任务前」崩溃 → **启动孤儿清扫**：对每个任务，把处于 `running_value` 且没有存活（pending/running）子会话的记录重置为 `pending_value`（镜像批任务 worker 现有的 `_restart_audit`）。

## 4. 单条记录的上下文构建

对每条认领记录，在现有 batch-staging 区暂存一个**上下文目录**，把它的路径作为该子会话的 `batch_input_file`。对批任务 worker 的 `_prepare_workspace` 做**向后兼容的小扩展**：**当暂存路径是目录时，把目录内容拷进会话工作区的 `uploads/`**（现状是拷单个文件——存量批任务不受影响）。

上下文目录内容：
1. **`record.md`** —— 记录字段以「标签—值」可读形式渲染。字段标签复用 `get_field_label_map`。纳入哪些字段遵循 `context_fields`（默认全部 / 可指定子集）。
2. **`attachments/`** —— 记录里 file/image 字段的真实文档：解析字段值 → `data_files.storage_path` → 按原始文件名拷入。拉取哪些文件字段遵循 `context_fields.fileFields`（`'auto'` = 页面配置里所有 file/image 字段）。*（file 字段值的具体结构在实现计划阶段对接 `data_files` 时确认。）*

**每个子会话收到的提示词三段拼装：**
```
[系统前言]  本任务数据见 uploads/record.md，附件见 uploads/attachments/。
[用户]      <prompt_template>          ← 你的操作指令，引用 skill
[系统契约]  完成后请在回复末尾输出一个 JSON 代码块，且仅含字段：{ <field_mapping 的 jsonKey> }
```

整段提示词是**按任务**的（一次扫描内所有子会话相同），所以存在 `ai_chat_batches.prompt` 上即可；每条记录的差异只在上下文目录。

## 5. 执行与回写（复用批任务引擎）

### 5.1 派发

`run_task` 调用现有 `create_batch(owner_user_id, name="AI定时·<任务名>·<时间>", prompt=<拼装提示词>, files=[...])`，每项 `path` 为该记录的上下文目录。`create_batch` 做小幅向后兼容扩展：新增可选 `scan_task_id` 参数 + 每个 file 项可带 `recordId`，据此给子会话写入 `scan_task_id` + `source_record_id`。之后由**现有 `BatchWorker`** 执行（3 并发 / 超时 / 重试 / 面板），**零新增并发代码**。

### 5.2 完成钩子（对 `batch_engine` 唯一改动，单行隔离）

在 `_mark_done` / `_mark_failed` 末尾：
```python
if session_row.get('scan_task_id'):
    from utils.ai_scan_engine import on_child_finished
    on_child_finished(session_row, final_msg, ok=<bool>)
```

`on_child_finished(session, final_msg, ok)` 逻辑全部在 `ai_scan_engine`：
- **子会话失败/超时** → 记录 `status_field = failed_value`；完整对话仍在批任务面板可查；任务 `last_error` 记一笔。
- **子会话成功** → 从回复中提取**最后一个 fenced `json` 代码块**（兜底：最后一个配平的 `{…}`）。
  - 解析成功且 `required` 键齐全 → 一条 `UPDATE dynamic_data`：按 `field_mapping` 逐个 `jsonb_set(jsonKey→column)`，置 `status_field = done_value`，`version+1`/`updated_at=now()`，限定 `id=source_record_id` + collection + branch。
  - 解析失败 / 缺必填键 → `status_field = failed_value`（原始输出在面板可查）。
- 记录已被删除/合并 → `UPDATE` 命中 0 行 → 记日志、空操作。

`jsonb_set` 的字段路径全部参数化（`ARRAY[%s]`），防注入。

### 5.3 重处理语义

扫描只挑 `pending_value`，故 `failed_value` 的记录**不会被自动重试**——它显式停在「处理失败」等人工排查；重试 = 把其状态重置为待处理，或在批任务面板点「重试失败」重跑该子会话（回写幂等，重跑覆盖）。

## 6. 配置 API、RBAC 与管理页

### 6.1 后端 `server/routes/ai_scan_tasks.py`（`ai_scan_tasks_bp`）

在 `dynamic_bp` 之前注册；`ai-scan-tasks` 加入 `RESERVED`。全部 `@require_permission('admin.ai_scan')`：
- `GET /ai-scan-tasks`、`GET /ai-scan-tasks/:id`
- `POST /ai-scan-tasks`、`PUT /ai-scan-tasks/:id`、`DELETE /ai-scan-tasks/:id`
- `POST /ai-scan-tasks/:id/run-now` —— 立即跑一次（测试用）

`DELETE` 先把该任务在途的 `running_value` 记录重置为 `pending_value`，再删除任务（子会话历史经 `ON DELETE SET NULL` 保留）。

### 6.2 前端

新增 `src/views/admin/AiScanTaskManager.vue` + `stores/aiScanTask.ts` + `api/aiScanTask.ts` + `types/aiScanTask.ts` + 路由。列表 + 编辑器：
- **基本**：名称、启用、调度间隔(分钟)、每次最多 N 条、负责人(默认当前管理员)。
- **目标数据**：数据页(collection)选择器 + 分支(默认 main)，复用 pageConfig store。
- **状态流转**：`status_field`（从该页字段选）+ 待处理/处理中/已处理/处理失败 四值（带默认）。
- **候选过滤(可选)**：`extra_filter` MongoDB 风格 JSON 文本框 + 校验。
- **上下文**：默认「全部字段 + 自动附件」，可选字段多选 / 指定文件字段。
- **提示词**：`prompt_template` 文本框，下方**实时预览**系统将追加的「输出 JSON 契约」。
- **字段映射**：行编辑器 `{jsonKey, 目标列(从该页字段选), 必填}`，驱动上面的契约预览。
- **运行信息(只读)**：上次运行时间 / 本次条数 / 最近错误；以及「立即运行」按钮。

## 7. 错误处理与边界

- `run_task` 整体 try/except：任一任务报错只写 `last_error`，tick 继续跑其他任务，调度器不崩。
- OpenCode/MCP 不可用或超时 → 子会话失败 → 记录置 `failed_value`，面板可查。
- JSON 解析失败 / 缺必填键 → `failed_value`，原始输出面板可查。
- 记录中途被删/合并 → 回写命中 0 行 → 空操作。
- **防重处理**：任务级进程内锁 + 原子 `SKIP LOCKED` 认领，双保险。
- **崩溃一致性**：`create_batch` 失败回滚认领；进程崩溃由启动孤儿清扫修复。
- **背压**：一次最多认领 `max_records_per_scan`，worker 3 并发逐步消化；下个 tick 不会重复认领（已是 `running_value`）。
- **删除任务**：先重置在途记录为待处理再删。
- **锁定/非 main 分支**：V1 仅扫 main。
- 调度器 `WERKZEUG_RUN_MAIN` 守卫防重复启动。
- **配置校验**：保存时校验 `status_field`、`field_mapping.column` 是否在页面字段中（仅告警不阻断，字段可能动态）；`extra_filter` JSON 合法性。

## 8. 测试

- **后端 pytest**：原子三态认领（只挑 pending+filter、翻 running、limit）；`extra_filter` 经 `mongo_query`；上下文构建（`record.md` 渲染、file 字段→`data_files` 拷贝，mock fs）；提示词拼装（前言+模板+契约）；`on_child_finished`（JSON 提取 fenced/balanced/none、成功回写映射列+done、缺必填→failed、子失败→failed、记录已删→空操作）；`create_batch` 扩展（写入 scan_task_id/source_record_id）；`_prepare_workspace` 目录拷贝扩展（存量单文件路径仍工作）；调度 due 逻辑、`run_task` try/except 写 last_error；路由 CRUD + run-now + RBAC 403 + 删除重置在途；`test_route_permission_keys` 覆盖 `admin.ai_scan`。沿用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。
- **前端 vitest**：task store/api；字段映射编辑器 + 契约预览；路由 `admin.ai_scan` 门禁；按 CLAUDE.md stub Element Plus。
- **E2E（Playwright）**：在某测试数据页建任务（含状态字段）→ 插入一条待处理记录 → 立即运行 → 「批任务」面板出现批任务 → 完成后记录状态翻 `done` 且映射列被写入。需 OpenCode+MCP 运行时。

## 9. 关键文件清单

**后端（新增）**：`server/routes/ai_scan_tasks.py`、`server/utils/ai_scan_engine.py`（认领/上下文/提示词/回写）、`server/utils/ai_scan_scheduler.py`、`server/utils/ai_scan_repo.py`（CRUD）。
**后端（修改）**：`server/utils/batch_engine.py`（`_prepare_workspace` 目录拷贝 + 完成钩子）、`server/utils/batch_repo.py`（`create_batch` 加 `scan_task_id`/`recordId`）、`server/utils/permissions.py`（`admin.ai_scan`）、`server/routes/dynamic.py`（`RESERVED` 加 `ai-scan-tasks`）、`server/app.py`（注册蓝图 + 启动调度器）、`server/init_db.py` + `server/seed_data.py`（建表/扩列/种菜单）。
**前端（新增）**：`src/views/admin/AiScanTaskManager.vue`、`src/stores/aiScanTask.ts`、`src/api/aiScanTask.ts`、`src/types/aiScanTask.ts`。
**前端（修改）**：`src/router/index.ts`（路由）、`src/stores/auth.ts`（`ADMIN_PATH_PERMISSION` 加 `/admin/ai-scan-tasks`）。
