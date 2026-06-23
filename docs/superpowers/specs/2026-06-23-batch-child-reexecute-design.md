# 批任务：单个子任务重新执行（清空上下文）— 设计文档

> 状态：设计已与需求方确认（仅终态可重执；清上下文=删旧消息+新建 OpenCode 会话）。日期：2026-06-23

## 1. 目标

支持对批任务中的**单个子任务**「重新执行」，并**清空其上下文**重跑：删掉该子任务的旧对话、用全新的 OpenCode 会话从头跑一遍。区别于已有的 `retry-failed`（批量重置所有失败子任务）——这是**单条、且对 completed 也可用**。

## 2. 背景与现状

- 现有 `batch_repo.reset_failed_to_pending(user_id, batch_id)`：把该 batch 所有 `failed` 子会话重置为 `pending`、清 `failed` 计数、复算状态。无单条、无清上下文。
- 批 worker `_run_one`：认领 `pending` 子会话 → `create_session`（新 OpenCode 会话）→ `_set_opencode_id` → 跑 prompt（用 `batch_input_file` 的输入文件）→ `_persist_conversation`（落库完整对话）→ `_mark_done`。**因此只要把子会话置回 `pending` 且清空 `opencode_session_id`，worker 重跑时就会自然新建会话**。
- 子会话是 `ai_chat_sessions` 行（`batch_id`/`batch_seq`/`batch_input_file`/`opencode_session_id`/`status`/`last_message_preview`/`error_message`）；对话在 `ai_chat_messages`。
- 输入文件 `batch_input_file` 在 batch-staging 下，重跑期间仍存在（仅 batch 删除时清理），可被 `_prepare_workspace` 再次拷入工作区。
- 前端 `BatchGroup.vue` 展开体逐行渲染 `store.activeSessions` 的子会话；store 有 `retryFailed`/`removeBatch`/`appendToBatch` 等。

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 可重执状态 | 仅终态 `completed` / `failed`（`pending`/`running` 不允许） |
| 清空上下文 | 删该子任务的旧 `ai_chat_messages` + 清 `opencode_session_id`/`last_message_preview`/`error_message`；worker 重跑建**全新** OpenCode 会话；旧 OpenCode 会话任其回收（不显式删） |
| 计数 | 旧 `completed`→`done-1`；旧 `failed`→`failed-1`；子会话回 `pending` → 复算 batch 状态（→ running） |
| 范围 | 单一内聚特性，一个 PR |

## 4. 后端

**文件**：`server/utils/batch_repo.py`、`server/routes/ai_chat_batches.py`；测试 `server/tests/test_batch_routes.py`。

- `batch_repo.reexecute_child(user_id, batch_id, session_id) -> dict | None`：
  - 单事务内：`SELECT s.status FROM ai_chat_sessions s JOIN ai_chat_batches b ON s.batch_id=b.id WHERE s.id=%s AND s.batch_id=%s AND b.user_id=%s`（归属校验）。无行 → 返回 `None`（→404）。
  - 若 `status NOT IN ('completed','failed')` → 抛 `ValueError('only completed/failed children can be re-executed')`（→409）。
  - `DELETE FROM ai_chat_messages WHERE session_id=%s`（清上下文）。
  - `UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, last_message_preview=NULL, error_message=NULL WHERE id=%s`。
  - 计数回退：`UPDATE ai_chat_batches SET done = done - 1 WHERE id=%s AND <old=completed>` 或 `failed = failed - 1 WHERE <old=failed>`（按旧状态二选一）。
  - `commit`。
  - 复算 batch 状态：复用 B 期加的 `_recompute_batch_status_for(batch_id)`（→ running）。
  - 返回 `get_batch_detail(user_id, batch_id)`。
- 路由 `POST /ai/chat/batches/<batch_id>/sessions/<session_id>/reexecute`（`@login_required`）：
  - `try: result = reexecute_child(uid, batch_id, session_id)`；`ValueError → 409`（状态冲突：非终态）；`result is None → 404`（不存在/不属于本人）；成功 `get_worker().notify()` + 返回 detail（200）。

## 5. 前端

**文件**：`src/api/aiChatBatches.ts`、`src/stores/aiChatBatches.ts`、`src/components/ai-chat/BatchGroup.vue`。

- `reexecuteChild(batchId, sessionId)` API → `POST /ai/chat/batches/${batchId}/sessions/${sessionId}/reexecute`，返回 `AiChatBatchDetail`。
- store action `reexecuteChild(batchId, sessionId)`：调 API → `await fetchList()`；若 `activeBatch.id===batchId` 则 `applyDetail` + 非终态时 `startDetailPolling`（与 `retryFailed(id)` 同套刷新）。
- `BatchGroup.vue` 子任务行：对 `s.status === 'completed' || s.status === 'failed'` 渲染「重新执行」图标（`@click.stop`，避免触发 `selectChild` 打开线程）；点后调 `store.reexecuteChild(batch.id, s.id)`，失败 `ElMessage.error`。用 `RefreshRight` 或 `RefreshLeft` 图标。

## 6. 测试

- 后端（`test_batch_routes.py`）：
  - completed 子任务 reexecute → 该子会话 `status='pending'`、`opencode_session_id IS NULL`、其 `ai_chat_messages` 被删空、batch `done` 减 1、batch `status='running'`；
  - failed 子任务 reexecute → batch `failed` 减 1；
  - `pending`/`running` 子任务 reexecute → 409；
  - 他人 batch / 不存在的 session → 404。
- 前端：`vue-tsc` clean；`BatchGroup` 对终态子项渲染「重新执行」、非终态不渲染。
- 实测：completed 子任务点「重新执行」→ 子任务回到运行→重新完成；点开线程只剩新一轮对话（旧的已清）。

## 7. 风险与边界

- **与 worker 的并发**：仅对终态子任务开放，且置 `pending` 用普通 UPDATE；worker 以 `FOR UPDATE SKIP LOCKED` 认领 `pending`，不会与本操作（改终态行）冲突。
- **输入文件存在性**：重跑依赖 `batch_input_file` 仍在 batch-staging；正常情况下在（仅 batch 删除时清）。若文件缺失，worker 的 `_prepare_workspace` 走既有失败路径 → 该子任务 `failed`（可接受、可再重执）。
- **计数一致性**：与 `_mark_done`/`_mark_failed` 同模式（计数增减 + 复算分两步），既有 worker 调用会自愈。
- **旧 OpenCode 会话**：不显式删，任其被 OpenCode 回收；与「close 不删会话」一致的取舍。
- **降级**：删消息/改状态在一个事务；复算在第二连接（与 append 一致）。
