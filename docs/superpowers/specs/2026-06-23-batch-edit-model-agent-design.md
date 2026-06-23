# 批任务：组头编辑 agent/模型（持久）— 设计文档

> 状态：设计已与需求方确认（组头编辑、持久；model + agent 都可改）。日期：2026-06-23

## 1. 目标

让用户在批任务**组头**直接修改该批次的 **agent 与模型**并持久化；改后「重试失败」「重新执行」以及 worker 的后续运行自然使用新值。解决「批次绑定的模型不可用（如 glm-5 卡住无响应）时只能删了重建」的痛点。

## 2. 背景与现状

- `ai_chat_batches` 已有 `agent`、`model` 列（创建时设定，事后不可改）。worker `_run_one` 经 `_fetch_batch_context(batch_id)` 读 `(prompt, agent, model)` 并传给 `send_message`，故**改了 batch.model/agent 即影响后续所有运行**（重试、重新执行、追加）。
- 现状无任何「编辑批次」端点；`BatchGroup` 组头只读显示 `agent · model`（留空显「默认」）。
- 已确认根因：某些模型（如 `bailian-coding-plan/glm-5`）prompt 发出后助手零产出、180s 无进度看门狗判失败；默认/`mimo/*` 正常。改模型可让卡住的批次切到可用模型重跑。

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 交互 | **组头编辑、持久**（写入 `ai_chat_batches`），改后重试/重执/worker 自动用新值 |
| 范围 | **model + agent 都可改** |
| 允许状态 | **任意状态**可改（在跑的子任务已发出 prompt，不受影响；pending/后续/重试用新值） |
| 空值 | 空串视为清除 → NULL = 用 OpenCode/全局默认 |
| 落地 | 单一内聚特性，一个 PR |

## 4. 后端

**文件**：`server/utils/batch_repo.py`、`server/routes/ai_chat_batches.py`；测试 `server/tests/test_batch_routes.py`。

- `batch_repo.update_batch_config(user_id, batch_id, *, agent, model) -> dict | None`：
  - `UPDATE ai_chat_batches SET agent=%s, model=%s WHERE id=%s AND user_id=%s`（`agent`/`model` 传 None 即写 NULL）；
  - `cur.rowcount == 0` → 返回 `None`（不存在/不属于本人）；
  - 否则 `commit`，返回 `get_batch_detail(user_id, batch_id)`。
- 路由 `PATCH /ai/chat/batches/<batch_id>`（`@login_required`）：
  - body `{agent?, model?}`；规范化：`agent = (body.get('agent') or '').strip() or None`，`model = (body.get('model') or '').strip() or None`；
  - `result = update_batch_config(uid, batch_id, agent=agent, model=model)`；`None → 404`；成功返回 detail（200）。
  - 不触发 worker（仅改配置；用户随后自行重试/重执）。

## 5. 前端

**文件**：`src/api/aiChatBatches.ts`、`src/stores/aiChatBatches.ts`、新增 `src/components/ai-chat/EditBatchConfigDialog.vue`、`src/components/ai-chat/BatchGroup.vue`。

- `updateBatchConfig(id, body: {agent: string|null, model: string|null})` API → `PATCH /ai/chat/batches/${id}`，返回 `AiChatBatchDetail`。
- store action `updateBatchConfig(id, body)`：调 API → 更新 `items` 对应行 → 若 `activeBatch.id===id` 则 `applyDetail`（与 retry/reexecute 刷新同套）。
- `EditBatchConfigDialog.vue`（props `{modelValue, batch}`，emit `update:modelValue`/`saved`）：
  - Agent `ElSelect`（`listAgents`，[...agents, ...subagents]）、模型 `ElSelect`（`listModels().models`，`filterable clearable`），均预填 `batch.agent`/`batch.model`、可清空；
  - 保存按钮 → `store.updateBatchConfig(batch.id, {agent: selectedAgent || null, model: selectedModel || null})` → 成功 `ElMessage.success` + emit `saved` + 关闭；失败 `ElMessage.error`。
- `BatchGroup.vue` 组头操作区加「编辑」图标（如 `Edit`/`Setting`，`@click.stop` 开对话框）；`EditBatchConfigDialog` 挂在组件内（`v-model` + `:batch="batch"`）；`@saved` 后刷新该组（若展开则 `store.selectBatch(batch.id)`，组头 `agent · model` 立即更新）。

## 6. 测试

- 后端（`test_batch_routes.py`）：
  - PATCH 改 `{agent:'plan', model:'mimo/mimo-v2.5'}` → 200，DB 中 batch.agent/model 更新，返回 detail 含新值；
  - PATCH 空值 `{agent:'', model:''}` → DB 置 NULL；
  - 他人/不存在 batch → 404。
- 前端：`vue-tsc` clean；`EditBatchConfigDialog` 预填当前值、保存触发 `updateBatchConfig`。
- 实测：把卡在 glm-5 的批次改成默认/mimo → 重试失败 → 子任务正常出内容并完成。

## 7. 风险与边界

- **在跑的子任务**：已 `send_prompt_async` 发出，改 batch.model 不影响在途；只影响之后认领的 pending（含重试/重执）。可接受、符合预期。
- **并发**：PATCH 单行 UPDATE，与 worker 读 `_fetch_batch_context`（每次运行读最新）不冲突；worker 总读改后的值。
- **模型可用性**：本特性只让用户换模型，不校验模型真可用（`listModels` 的 `connected` 仅表 provider 配置）；换到坏模型仍会卡——这是用户选择，UI 已能显示失败原因（上一个 PR）。
- **REST 语义**：`PATCH /batches/<id>` 仅认 `agent`/`model` 字段，忽略其它，避免越权改 total/status 等。
