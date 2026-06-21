# AI 会话治理与审计 — 设计文档

> 状态：设计已与需求方确认（删除策略 + 审计机制两项关键决策已定），待评审。
> 日期：2026-06-21

## 1. 目标

把 AI 助手会话管理从"个人各自为政、可物理删除"变成**受治理的系统能力**：个人只能**关闭（close）**会话、不能物理删除；会话与消息**永久保留**；会话生命周期操作**可审计、可溯源**。

## 2. 背景与现状（问题）

- **个人能物理删除自己的会话**：`DELETE /ai/chat/sessions/:id`（`delete_session`，`@write_required`）会删 DB 行 + OpenCode session + 工作区。一删即无法审计、无法溯源、无法重启。
- 会话 `status` 仅有 `active / completed / error / failed / pending / running`，**没有"软关闭可重开"的状态**；个人会话列表 `WHERE status='active'` 只显示 active。
- 会话操作**完全不写 `operation_logs`**，目前没有任何审计/溯源。
- 消息删除 `delete_message_onwards`（Edit/Retry）会物理删消息——本次**不纳入**（属会话内编辑，按确认的"会话生命周期审计"范围）。

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 物理删除策略 | **完全禁止**：个人和 admin 都不能物理删会话/消息；个人只能 close（软关闭可重开）；永久保留供审计/溯源；admin 最多"归档" |
| 审计机制/粒度 | **复用现有 `operation_logs`**，记录会话**生命周期**操作（create/close/reopen/archive）；消息历史已在 `ai_chat_messages` 永久保留 |

## 4. 会话状态机

```
            个人 close                 admin archive
  active  ───────────►  closed  ───────────────────►  archived
    ▲                     │                              │
    └──── 个人 reopen ─────┘            (个人不可 reopen) ─┘
```

- `active`：进行中（创建时默认）。
- `closed`：个人软关闭，**可被本人 reopen**。reopen 时若 OpenCode session 已失效，由 **M3 韧性恢复**自动重建上下文。
- `archived`：admin 归档（隐藏但永久保留；个人不可 reopen）。
- **物理删除：移除**（无任何端点物理删交互会话/消息）。
- batch / scan 等非交互会话的 `pending/running/completed/failed` 不变。

## 5. 后端

**文件**：`server/routes/ai_chat.py`、`server/utils/operation_log.py`（复用）、`server/utils/permissions.py`（新增权限）、`server/init_db.py`（status 注释/默认无需改，'closed'/'archived' 是值不是列）。

- **废弃个人物理删除**：移除 `DELETE /sessions/:id` 的物理删行为，改为 **`POST /ai/chat/sessions/:id/close`**：
  - `UPDATE ai_chat_sessions SET status='closed' WHERE id=%s AND user_id=%s`（仅本人，`status` 当前为 `active`）；
  - `stop_listener(sid)`；**不**删 DB 行 / 消息 / 工作区；OpenCode session 保留或惰性失效。
- **新 `POST /ai/chat/sessions/:id/reopen`**：本人把自己的 `closed` 会话改回 `active`（`archived` 不可 reopen，返回 403）。
- **list 改造**（`GET /sessions`）：个人列表 `WHERE user_id=%s AND status IN ('active','closed') ...`（含 batch 子会话原条件），返回每条的 `status`，前端据此显示"重开"。
- **admin 端点**（`@require_permission('admin.ai_chat_admin')`）：
  - `GET /ai/chat/admin/sessions`：列所有用户会话（含 closed/archived，可按 user/status 过滤）；
  - `POST /ai/chat/sessions/:id/archive`：admin 归档任意会话（`status='archived'`）。
- **审计接入**：在 create / close / reopen / archive 各调
  `log_operation(action, 'ai_chat_session', sid, title, description)`
  （`operation_log.py` 已自动填 operator_id/name/role、created_at）。action 用 `create_session` / `close_session` / `reopen_session` / `archive_session`。
- 兼容：旧前端若仍调 `DELETE /sessions/:id`，让该路由返回 405/410 或内部转为 close（实现时定，倾向直接移除路由并由前端改用 close）。

## 6. 前端

**文件**：`src/api/aiChat.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`。

- 会话列表项的"删除"动作 → 改为 **"关闭"**（调 `close`）；`closed` 会话灰显并显示 **"重开"** 按钮（调 `reopen`）。
- 移除前端的物理删除调用。
- admin 会话治理 + 审计：复用现有 `operation_logs` 审计页（按 `target_type='ai_chat_session'` 过滤即可看会话操作的操作人/时间/动作）；admin 会话列表 + 归档按钮作为同期或紧后续。

## 7. 权限

- 个人：`close` / `reopen` **仅自己的**会话（`user_id` 校验）。
- admin：`GET /admin/sessions` + `archive`，新增 RBAC 能力 **`admin.ai_chat_admin`**（加入 `PERMISSION_CATALOG`；admin 内置超级用户默认有）。
- 审计查询：走现有 operation_logs（其读取已有权限控制）。

## 8. 测试

- **后端**：
  - `close` → `status='closed'` 且写一条 `operation_logs(action='close_session', target_type='ai_chat_session')`；不删 DB 行/消息。
  - `reopen` → `active`；`archived` 会话 reopen → 403。
  - 个人 `close`/`reopen` 他人会话 → 404（`user_id` 不匹配）。
  - 物理 `DELETE /sessions/:id` 路由已移除（404/405）。
  - admin `archive` + 无权限用户调 admin 端点 → 403。
  - list 返回 `active`+`closed`（不再隐藏 closed）。
- **前端**：`vue-tsc` clean；列表渲染 close/reopen 按钮（按 status）。
- **文档**：用户指南「AI 助手」补充会话关闭/重开说明；CLAUDE.md AI 段记录治理 + 审计。

## 9. 风险与注意

- **OpenCode session 与 close**：close 不删 OpenCode session；reopen 时若该 session 已被 OpenCode 回收，由 M3 `_recover_session_and_resend` 自动重建——已在 M3 实现并验证，复用即可。
- **既有 active 会话**：上线后存量 `active` 会话不受影响；无需数据迁移（'closed'/'archived' 是新取值，不加列）。
- **审计完整性边界**：本期审计覆盖会话**生命周期**（create/close/reopen/archive）。消息发送/编辑（Edit/Retry）不纳入审计、消息可被 Edit/Retry 改动——如需消息级溯源，是后续增强（独立决策）。
