# 数据页自定义行级操作按钮（绑定 Webhook / AI 扫描任务）设计

日期：2026-08-09
状态：已确认，待编写实施计划

## 1. 背景与目标

数据页目前的行操作是**写死**的：`DataTable.vue` 的「⋯」溢出菜单里固定渲染 查看 / 编辑 / 删除，`DynamicPage.vue` 通过 `extra-actions` 插槽额外挂了 复制 / 关系图谱 / 「导出：<脚本名>」。其中「导出：xxx」是唯一一个**配置驱动**的行按钮（由 `PageConfig.rowExportScripts` 指定），但它只能做导出。

用户需要的是：**在数据页上按需配置行级功能按钮，点击后对该行触发一个 Webhook，或触发一个 AI 任务**。

现状盘点（四块拼图都已存在，缺的是把它们串起来的绑定层）：

| 能力 | 现状 |
|---|---|
| 行级按钮挂载点 | 已有。`src/components/common/DataTable.vue:130` 的 `el-dropdown` + `extra-actions` 插槽（slot prop `{ row }`） |
| 配置驱动行按钮的先例 | 已有。`PageConfig.rowExportScripts: string[]` → `DynamicPage.vue:270` 渲染「导出：xxx」 |
| Webhook 执行器 | 已有。`server/utils/webhook_engine.py`：HMAC-SHA256 签名、超时、重试、`webhook_logs`。但只有**事件驱动**（create/update/delete/merge），无手动触发 |
| AI 单记录处理管线 | 已有。`server/utils/ai_scan_engine.py`：claim → 状态机（待处理/处理中/已处理/处理失败）→ 构建上下文目录 → 批任务引擎 → `on_child_finished` 结构化回写。但入口只有**定时扫描整页** |

### 目标

1. 页面配置里可定义任意数量的行级按钮，出现在行「⋯」菜单中。
2. 每个按钮绑定一条 Webhook 规则或一个 AI 扫描任务。
3. 异步执行，执行状态与结果**回写到该行的字段上**。
4. 按钮支持：角色可见性、按行条件显示、二次确认、点击时弹参数表单。

### 非目标（本期明确不做）

- **批量执行**（勾选多行一起触发）。批量场景已由定时扫描任务覆盖，YAGNI。
- 行内平铺按钮。一律进「⋯」溢出菜单，避免撑宽操作列。
- 跨页面复用的全局动作库（见 §2 已否决方案 C）。
- 组合条件（and/or）。单条件已确认够用。

## 2. 方案选型

**采纳方案 A：页面配置内嵌 `rowActions`，统一动作模型。**

配置存在 `page_configs` 的 JSONB 里（与 `deleteBinding` / `viewConfig` 同级），只描述「哪个按钮、绑哪个执行器、怎么显示、怎么回写」这层**薄绑定**；重的执行器（Webhook 规则、AI 扫描任务）仍是全局资源，各有自己的管理页。

已否决：

- **方案 B（复用 `trigger_rules` 表）**：`trigger_rules` 是事件驱动语义，塞入 UI 按钮定义（文案、角色可见性、参数表单）会让该表承担两种不同职责，且按钮与页面的归属关系变成隐式。
- **方案 C（独立 `row_actions` 表 + 全局动作库）**：多一张表、多一个管理页、多一层 id 解引用；而 `visibleWhen` / `paramFields` / `statusField` 本质强依赖具体页面的字段名，跨页面复用的实际价值很低——换个页面基本要重配。

## 3. 配置模型

### 3.1 存储

`page_configs` 表新增一列 `row_actions JSONB DEFAULT '[]'::jsonb`，与现有的 `view_config` / `delete_binding` 同构（它们都是独立 JSONB 列，而非塞在 `fields` 里）。

迁移照抄 `init_db.py` 里 `delete_binding` 的幂等写法（查 `information_schema.columns`，缺列才 `ALTER TABLE ADD COLUMN`）。

`webhook_rules.trigger_event` 是 `VARCHAR(50)` **无 CHECK 约束**，故增加 `'manual'` 取值不需要任何 DDL 变更。

### 3.2 类型定义

新文件 `src/types/rowAction.ts`，从 `src/types/index.ts` 再导出。

```ts
export type RowActionType = 'webhook' | 'aiTask'

export interface RowActionCondition {
  field: string
  operator: 'eq' | 'ne' | 'in' | 'notIn' | 'empty' | 'notEmpty'
  value?: string | string[]   // eq/ne 用 string；in/notIn 用 string[]；empty/notEmpty 不用
}

export interface RowActionConfig {
  id: string                          // nanoid，页面内唯一
  label: string                       // 菜单文案，必填
  actionType: RowActionType
  enabled: boolean

  // 二选一，由 actionType 决定
  webhookRuleId?: string              // actionType='webhook'
  scanTaskId?: string                 // actionType='aiTask'

  roles?: string[]                    // 空/缺省 = 不限角色
  visibleWhen?: RowActionCondition    // 缺省 = 总是显示
  confirmText?: string                // 缺省 = 不二次确认
  paramFields?: FieldConfig[]         // 缺省 = 不弹参数表单

  statusField?: string                // 缺省 = 不写状态
  runningValue?: string
  doneValue?: string
  failedValue?: string
  responseMapping?: FieldMappingRow[] // 仅 webhook 生效
}
```

`PageConfig` / `PageFormData` 各新增 `rowActions?: RowActionConfig[]`。

### 3.3 刻意的取舍

- **`visibleWhen` 用 `{field, operator, value}` 而非 MongoDB 查询**。该条件必须在前后端**各求值一次**（前端决定显不显示，后端决定准不准执行），把 `utils/mongo_query.py` 那套搬到 JS 不值得。现有 `webhook_engine._check_condition` 本就只支持 `{field, value}` 等值判断，这里只是扩成一个小算子集。
- **`paramFields` 复用 `FieldConfig`**，因此参数表单直接用现成的 `DynamicForm` 渲染，无需新控件。但 `relation` / `reference` / `quoteSelect` / `autoSequence` 在参数表单里没有语义（它们要落库才成立），保存时校验拒绝。
- **`responseMapping` 复用 `FieldMappingRow`**（`{jsonKey, column, required}`，同 `ai_scan_tasks.fieldMapping`），回写代码两边共用一份。
- **`statusField` 及三个状态值可选**。不配 = 纯触发。AI 动作若不配，回落到所绑 `ai_scan_tasks` 自己的 `statusField` / `runningValue` / `doneValue` / `failedValue`（那才是它的主场），行动作只在需要覆盖时才填。

### 3.4 保存时校验（`server/routes/page_configs.py` create/update）

- `label` 非空；`id` 页面内唯一。
- `actionType` 与 `webhookRuleId` / `scanTaskId` 必须匹配，且指向存在的记录。
- `statusField`、`responseMapping[].column`、`visibleWhen.field` 必须是本页面已定义的字段名。
- `paramFields[].controlType` 不得为 `relation` / `reference` / `quoteSelect` / `autoSequence`。

## 4. 执行链路

### 4.1 入口

```
POST /<collection>/<recordId>/row-actions/<actionId>/run
Body: { params?: { ...paramFields 的值 } }
→ 200 { ok: true, status: 'running' | 'submitted' }
```

`status` 取值：配了 `statusField` 时返回 `'running'`（行上已写入 `runningValue`，前端可开始轮询）；未配时返回 `'submitted'`（行上无状态可观测，前端只弹 toast、不轮询）。

注册在 `dynamic_bp` 上。Werkzeug 按静态段优先级路由，不会被 `/<collection>` catch-all 抢走。**立即返回**，实际执行在后台。

### 4.2 新模块 `server/utils/row_action_engine.py`

`run_action(collection, record_id, action, user, params)`：

1. 读取该行 `data`。
2. 后端复核 `roles` 白名单 + `visibleWhen`（前端隐藏不算数）。
3. 若配了 `statusField` 且当前值已是 `runningValue` → `409 该行有正在执行的动作`（幂等闸门）。不配 `statusField` 则不设闸。
4. 写 `runningValue`。
5. 按 `actionType` 分派（见 4.3 / 4.4）。
6. 写 `operation_logs`（`target_type='row_action'`，记 actionId / label / recordId / 操作人）。

### 4.3 Webhook 分支

新增 `webhook_engine.fire_webhook_rule(rule_id, collection, record_id, row_data, operator, params, branch_id)`：按 id 取单条规则，直接调现成的 `_fire_single_webhook` —— **HMAC 签名、超时、重试、`webhook_logs` 全部白拿**。

`_build_payload` 扩一个 `event='manual'` 分支，payload 带 `actionId` / `actionLabel` / `params`。

执行放在后台线程，`finally` 保证落一个终态：

- 成功且配了 `responseMapping` → 解析响应 JSON 按映射写回 + `doneValue`
- 成功但无 `responseMapping` → 只写 `doneValue`
- 失败 → 写 `failedValue`

`webhook_rules.triggerEvent` 枚举增加 `'manual'`。这类规则**永远不会**被 create/update/delete 自动触发（`fire_webhooks` 按 event 精确匹配），只能被按钮按 id 点名调用。

`manual` 规则自身的 `triggerCondition` **被忽略** —— 触发条件由按钮的 `visibleWhen` 单点负责，避免两处条件语义打架。规则管理页在 `triggerEvent='manual'` 时隐藏条件配置项。同理，`triggerTiming` / `rollbackOnFailure` 对 `manual` 无意义（没有可回滚的事务），一并隐藏并按 `after` / `false` 处理。

### 4.4 AI 分支

新增 `ai_scan_engine.claim_one(task, record_id)`：`claim_records` 的单行版，同样 `FOR UPDATE SKIP LOCKED` + 原子翻状态，但**不带 `pendingValue` 谓词** —— 手动重跑必须能对「已处理」的行再来一次，这是它与定时扫描的唯一语义差别。

随后原样调用 `build_context_dir(task, record)` → `create_batch(owner, name=..., prompt=assemble_prompt(task), files=[单条], scan_task_id=task.id, agent=task.agent)`。批任务引擎接手，`on_child_finished` **一行不改**完成结构化回写。

`params` 以 `## 本次执行参数` 段落追加进 `record.md`（而非拼进 prompt）—— prompt 是 batch 级共享的，params 是单行的；写进上下文文件语义更正确，且 `assemble_prompt` 不用动。

> 实现注意：`ai_scan_engine` 内存在两种 task dict 形状 —— `_load_task()` 返回 snake_case 键（`status_field`），而 `build_context_dir` / `claim_records` 使用 camelCase 键（`statusField`，来自 `ai_scan_repo`）。`claim_one` 必须与 `build_context_dir` 保持同一形状。

### 4.5 执行痕迹：不新建表

Webhook 的审计是 `webhook_logs`（已含错误信息、响应体、重试次数）；AI 的审计是子会话的完整对话（批任务引擎已持久化含工具调用的全量消息）。行上的状态字段回答「现在怎么样」，这两处回答「当时发生了什么」，职责不重叠。

## 5. 错误处理

| 情况 | 结果 |
|---|---|
| 动作不存在 / `enabled=false` | 404 / 400 |
| 绑定的 webhook 规则或扫描任务已被删除 | 400「动作绑定的执行器已不存在」（保存时校验过，但执行器可能后删） |
| 角色不在白名单 | 403 |
| `visibleWhen` 不满足 | 409「当前记录不满足该动作的执行条件」 |
| 行已处于 `runningValue` | 409 |
| `claim_one` 命中 0 行（行已删 / 被锁） | 409，并回滚已写的 `runningValue` |
| 后台执行抛异常 | `finally` 写 `failedValue`；webhook 细节进 `webhook_logs` |
| 进程执行中崩溃 | AI 侧由现有 `sweep_orphans` 兜底 |

### 已知限制（已与用户确认接受）

Webhook 侧若 Flask 进程在后台线程执行中崩溃，会留下卡在「执行中」的行。兜底方式：**幂等闸门对陈旧的 `runningValue` 放行** —— 若该行 `updated_at` 距今超过阈值（取所绑规则的 `timeout × (retries + 1)`，并设下限 5 分钟），视为陈旧，允许重新触发。不为此建执行记录表。

## 6. 前端

### 6.1 配置 UI

`src/views/admin/PageConfigManager.vue` 的 `el-tabs` 新增 **「行操作」** 标签页（与 基本信息 / 视图配置 / 字段配置 / 删除绑定 / 关系图谱 并列）。

内容抽成独立组件 `src/components/admin/RowActionsEditor.vue` —— `PageConfigManager.vue` 已 1500+ 行，不再往里塞。

编辑器结构：左侧动作列表（可拖拽排序，顺序即菜单顺序），右侧编辑面板分四组：

1. **基本** —— 名称 / 类型 / 启用
2. **绑定** —— 按类型下拉选 Webhook 规则或 AI 扫描任务，带跳转到对应管理页的链接
3. **显示条件** —— 角色多选 + 单条件构造器 + 确认文案
4. **执行结果** —— 状态字段 + 三个状态值 + 响应字段映射（后者仅 webhook 显示）

`paramFields` 复用已有的字段配置编辑器组件（`deleteBinding.fields` 已是这么做的）。

### 6.2 运行时 UI

- `src/views/dynamic/DynamicPage.vue:270` 的 `extra-actions` 插槽内追加一组 `el-dropdown-item`，与「导出：xxx」并列。
- 可见性 = `enabled` && 角色匹配（`authStore.userRole`，`isSuperuser` 直通）&& `visibleWhen` 对该行求值通过。
- 新组件 `src/components/dynamic/RowActionRunner.vue`：承载二次确认框 + 参数表单对话框（内嵌 `DynamicForm`）+ 提交 + 轮询。
- **轮询**：提交后 toast「已提交」并立即刷新一次列表（看到「执行中」），随后若该行仍处于 `runningValue` 则每 5s 轮询一次，**上限 5 分钟**（AI 动作常跑几十秒到数分钟），行离开 `runningValue` 即停；组件卸载 / 页面切走时清理定时器。接口返回 `status='submitted'`（该动作未配 `statusField`）时不启动轮询，只 toast。超过 5 分钟仍未终态则停止轮询并提示「执行时间较长，请稍后手动刷新查看」，不报错。
- API 层 `src/api/rowAction.ts`。
- 条件求值器 `src/utils/rowActionCondition.ts`，与后端 `server/utils/row_action_condition.py` 逻辑对齐。

## 7. 权限

- **执行**：`write_required` + `require_page_action(collection, 'update')`（行动作会改这一行的数据，等同更新）+ 动作自身的 `roles` 白名单。
- **配置**：走页面配置本身的权限（`admin.page_configs`），**不新增权限位**。
- 前端的隐藏/置灰仅是 UX，后端为准。

## 8. 测试

### 后端

- 新增 `server/tests/test_row_action_engine.py`：条件求值全算子；角色闸门；幂等闸门（含陈旧 `runningValue` 放行）；`claim_one` 对「已处理」行能重跑；执行器已删除的报错；`responseMapping` 写回；失败落 `failedValue`。
- `server/tests/test_webhook_engine.py` 补：`manual` 事件不被 create/update 触发；`fire_webhook_rule` 按 id 精确命中。
- `server/tests/test_page_configs.py`（或对应文件）补 `rowActions` 保存校验。

### 前端

- `src/utils/__tests__/rowActionCondition.test.ts`：**与后端共用同一份表驱动用例**（同一组 JSON fixture，两边各自读取），保证两侧求值不漂移。
- `src/components/dynamic/__tests__/RowActionRunner.test.ts`：确认框、参数表单、轮询启停与卸载清理。

## 9. 验证（CLAUDE.md 强制要求）

- **Playwright 实跑**：配置一个 webhook 行动作 + 一个 AI 行动作，在数据页点开「⋯」→ 二次确认 → 填参数 → 提交，断言行状态变「执行中」、轮询后变终态；交叉核对 `webhook_logs` 行 / AI 子会话对话。截图存 `.playwright-mcp/`。
- **`proxy.py` 验证**：本期**不新增 SSE**（轮询走普通 JSON 请求），因此 `_is_sse()` 无需改动；但新增了路由形状 `/<collection>/<id>/row-actions/...`，仍需过一遍 `:8080` 确认转发正常、`Authorization` 头未被丢弃。

## 10. 文档

- 新建 `docs/user-guide/data/row-actions.md`（含真实页面截图，存 `docs/user-guide/_images/`），从 `docs/user-guide/README.md` 索引链接。
- `docs/user-guide/integration/webhooks.md` 补 `manual` 事件说明。
- `docs/user-guide/ai/scan-tasks.md` 补「手动对单行触发」一节。
- `CLAUDE.md` 增补一小节描述该机制。

## 11. 涉及文件清单

**新增**

- `src/types/rowAction.ts`
- `src/utils/rowActionCondition.ts`
- `src/api/rowAction.ts`
- `src/components/admin/RowActionsEditor.vue`
- `src/components/dynamic/RowActionRunner.vue`
- `server/utils/row_action_engine.py`
- `server/utils/row_action_condition.py`
- `server/tests/test_row_action_engine.py`
- `src/utils/__tests__/rowActionCondition.test.ts`
- `src/components/dynamic/__tests__/RowActionRunner.test.ts`
- `docs/user-guide/data/row-actions.md`

**修改**

- `src/types/page.ts`（`PageConfig` / `PageFormData` 加 `rowActions`）、`src/types/index.ts`、`src/types/webhook.ts`（`triggerEvent` 加 `'manual'`）
- `src/views/admin/PageConfigManager.vue`（新标签页）
- `src/views/dynamic/DynamicPage.vue`（`extra-actions` 插槽 + 挂 `RowActionRunner`）
- `server/init_db.py`（`page_configs.row_actions` 列迁移）
- `server/routes/dynamic.py`（新路由）
- `server/routes/page_configs.py`（`row_to_dict` + 3 处 SELECT 列表 + INSERT + UPDATE 的 SET 分支 + 保存校验）
- `server/utils/webhook_engine.py`（`fire_webhook_rule` + `_build_payload` 的 `manual` 分支 + `_check_condition` 兼容）
- `server/utils/ai_scan_engine.py`（`claim_one`）
- `docs/user-guide/README.md`、`docs/user-guide/integration/webhooks.md`、`docs/user-guide/ai/scan-tasks.md`、`CLAUDE.md`
