# AI Chat P0 稳定性与可恢复性 Spec

**版本**：v1.0  
**状态**：待评审  
**日期**：2026-09-16  
**优先级**：P0  
**范围**：AI Chat 普通会话、批任务子会话和会话生命周期操作  
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 背景

当前 AI Chat 已具备流式对话、SSE 重连、批任务、消息持久化、会话关闭/重开/清空/删除等能力，但高频异常场景下存在以下问题：

- 发送失败后前端可能长期保持“正在回复”。
- SSE 断线期间错过 `session.idle` 后，前端可能无法恢复到完成状态。
- 重连成功后没有可靠的服务端状态同步和消息补偿。
- 关闭会话不一定会中止正在运行的 OpenCode 回合。
- 运行中的批任务可以直接删除，存在 Worker、数据库和工作区清理竞态。
- `close`、`stop`、`cancel`、`delete` 的用户语义不够清晰。
- 加载错误可能被显示成空数据。
- 操作失败后缺少明确的重试和恢复入口。

相关现状文件：

- `src/stores/aiChat.ts`
- `src/api/aiChat.ts`
- `src/views/ai-chat/AiChatView.vue`
- `src/components/ai-chat/BatchGroup.vue`
- `server/routes/ai_chat.py`
- `server/utils/chat_persist.py`
- `server/utils/opencode_client.py`
- `server/routes/ai_chat_batches.py`
- `server/utils/batch_repo.py`
- `server/utils/batch_engine.py`
- `server/utils/workspace.py`

## 2. 目标

本期目标是让用户在发送失败、网络断开、切换会话、停止任务和删除任务后，始终能够：

1. 知道系统当前处于什么状态。
2. 判断上一操作是否已经生效。
3. 通过明确按钮恢复或重试。
4. 不因重复点击造成重复执行。
5. 不因页面断线导致消息、任务状态或工作区状态不一致。

## 3. 非目标

本期不包含：

- 多 OpenCode 实例和滚动发布。
- 完整的任务调度中心。
- 消息全文搜索。
- 工具调用 UI 的全面重设计。
- Provider、模型和 MCP 能力重构。
- 命令执行接口的异步化改造；但必须纳入统一错误提示和超时提示。

## 4. 术语

| 术语 | 定义 |
|---|---|
| 会话 | `ai_chat_sessions` 中的一条交互或批任务子会话 |
| 回合 | 用户发送一次 Prompt 到 OpenCode 完成、失败或中止的完整过程 |
| 普通会话 | `batch_id IS NULL` 的交互式会话 |
| 批任务子会话 | `batch_id IS NOT NULL` 的文件级执行会话 |
| 运行时状态 | OpenCode 当前是否仍在执行某个回合 |
| 终态 | `completed`、`failed`、`stopped`、`closed` 或 `deleted` |
| drain | 停止接收新工作，等待当前工作完成或取消 |

## 5. 总体设计

采用“服务端状态为准、前端状态可恢复”的方案：

```text
发送请求
  → 服务端创建回合状态
  → OpenCode 执行
  → 持久化事件和消息
  → 前端接收 SSE
  → SSE 中断时查询服务端状态并补拉消息
  → 终态明确收敛
```

关键原则：

- 前端 `streaming` 不是事实来源。
- SSE 只负责实时通知，不能承担唯一状态存储职责。
- 断线恢复必须依赖数据库和 OpenCode 状态查询。
- 删除运行中资源必须先停止或取消，再执行清理。
- 所有异步操作都必须有幂等键或状态条件。

## 6. 状态模型

### 6.1 前端回合状态

前端每个会话维护以下状态：

| 状态 | 说明 |
|---|---|
| `idle` | 没有正在执行的回合 |
| `sending` | 正在提交 Prompt |
| `streaming` | 正在接收 OpenCode 输出 |
| `syncing` | 断线后正在同步服务端状态 |
| `failed` | 本回合失败，可重试 |
| `stopped` | 本回合被用户停止 |
| `completed` | 本回合已完成 |
| `recovery_required` | 自动恢复失败，需要用户手动同步 |

前端状态转换：

```text
idle → sending → streaming → completed
idle → sending → failed
streaming → syncing → streaming
streaming → syncing → completed
streaming → syncing → failed
streaming → stopped
syncing → recovery_required
```

### 6.2 服务端回合状态

服务端状态必须持久化，不能只放在 Flask 进程内存中：

| 状态 | 说明 |
|---|---|
| `accepted` | 已接受请求，尚未确认 OpenCode 开始执行 |
| `running` | OpenCode 正在执行 |
| `completed` | OpenCode 正常完成 |
| `failed` | 执行失败 |
| `stopped` | 用户主动中止 |
| `orphaned` | 进程异常退出后未正常收敛 |
| `recovering` | 服务端正在尝试恢复 |

### 6.3 会话生命周期状态

继续使用现有会话生命周期，但增加状态约束：

| 会话状态 | 允许操作 |
|---|---|
| `active` | 发送、停止、关闭、清空、删除 |
| `closed` | 查看、重开、清空、删除 |
| `deleted` | 仅允许审计查询，不允许发送和文件操作 |
| `pending/running` 批子会话 | 查看、停止、取消，不允许普通发送 |
| `completed/failed` 批子会话 | 查看、重新执行、重试或删除 |

## 7. 回合持久化设计

### 7.1 建议增加 `ai_chat_turns`

当前 `ai_chat_sessions` 主要描述会话，不足以可靠描述多个 Prompt 回合的生命周期。建议增加 `ai_chat_turns` 表。

主要字段：

- `id`
- `session_id`
- `user_id`
- `client_request_id`
- `status`
- `retry_of`
- `error_code`
- `error_message`
- `started_at`
- `last_event_at`
- `finished_at`
- `created_at`

约束：

- `(session_id, client_request_id)` 唯一。
- `session_id` 必须归属于 `user_id`。
- 同一个普通会话最多存在一个 `running` 回合。
- `retry_of` 只允许指向同一会话中的历史回合。

建议索引：

- `(session_id, created_at DESC)`。
- `(session_id, status)`。
- `(user_id, created_at DESC)`。

### 7.2 发送请求幂等

前端每次发送生成 `clientRequestId`。

服务端处理规则：

- 相同 `sessionId + clientRequestId` 已完成：返回已有回合结果。
- 相同请求正在执行：返回已有 `turnId`，不重复调用 OpenCode。
- 相同请求失败：允许用户明确点击重试后生成新的 `clientRequestId`。

## 8. 发送失败处理

### 8.1 服务端

发送接口必须按以下顺序处理：

1. 校验用户、会话状态和权限。
2. 创建或获取幂等回合。
3. 将回合标记为 `accepted`。
4. 启动持久化监听器。
5. 调用 OpenCode。
6. 调用成功后标记 `running`。
7. 调用失败后标记 `failed`。
8. 返回结构化错误和 `turnId`。

任何异常都必须完成回合状态收敛，不能只记录日志后返回普通 500。

### 8.2 前端

发送失败后必须：

- 清除 `streaming`。
- 清除 `thinking`。
- 停止当前回合计时器。
- 保留用户原始输入，允许一键重试。
- 保留已收到的部分输出，并标记为“未完成输出”。
- 不自动重复提交。

错误卡片显示：

```text
本轮执行失败
错误原因：网络连接中断
[重试本轮] [复制错误详情]
```

## 9. SSE 断线和恢复

### 9.1 断线判定

以下情况都进入 `syncing`：

- EventSource `error`。
- SSE 连接主动关闭。
- 长时间未收到事件超过心跳阈值。
- 页面从后台恢复后连接已失效。

### 9.2 重连流程

1. 关闭旧 EventSource。
2. 按现有退避策略重连。
3. 重连成功后先请求服务端运行时状态。
4. 根据服务端返回的 `turnStatus` 决定是否继续等待。
5. 请求消息增量或完整补偿。
6. 刷新文件、变更文件和 Token 状态。
7. 服务端为终态时清除前端 `streaming`。
8. 服务端仍为 `running` 时恢复实时监听。
9. 无法判断状态时显示 `recovery_required`。

### 9.3 新增状态接口

建议增加：

```text
GET /ai/chat/sessions/<session_id>/runtime-state
```

返回：

```json
{
  "sessionId": "session-001",
  "sessionStatus": "active",
  "turnId": "turn-001",
  "turnStatus": "running",
  "lastEventAt": "2026-09-16T10:20:00Z",
  "lastMessageSeq": 42,
  "opencodeSessionId": "oc-session-001",
  "error": null
}
```

未运行回合时：

```json
{
  "sessionStatus": "active",
  "turnId": null,
  "turnStatus": "idle",
  "error": null
}
```

### 9.4 消息同步

现有消息接口增加可选参数：

```text
GET /ai/chat/sessions/<session_id>/messages?afterSeq=<seq>
```

规则：

- `afterSeq` 为空时返回现有完整消息结果。
- `afterSeq` 存在时只返回之后新增的消息。
- 返回消息必须按 `seq ASC` 排序。
- 前端按消息 ID 或序号幂等合并，不能重复渲染。

如果当前消息接口无法安全支持增量读取，则 P0 先使用完整回载，后续再增加增量参数。

## 10. 停止、关闭和删除语义

### 10.1 停止生成

适用于当前回合：

1. 前端调用 abort 接口。
2. 服务端标记回合为 `stopped`。
3. 调用 OpenCode `abort_session`。
4. 等待持久化监听器收敛。
5. 前端保留已生成内容并显示“已停止”。

停止接口必须幂等：重复调用不能产生错误或重复中止。

### 10.2 关闭会话

关闭是会话级操作，不等同于停止回合。

如果会话有运行中回合：

- 默认先停止回合，再将会话置为 `closed`。
- 返回 `202` 和 `closing` 状态，前端轮询直到收敛。
- 停止失败时不能显示“已关闭”，应显示“关闭处理中”。

### 10.3 删除普通会话

删除采用软删除：

1. 会话标记为 `deleted`。
2. 撤销 session token。
3. 禁止新消息、文件上传和脚本执行。
4. 如果存在运行中回合，先发起停止。
5. 工作区异步清理。
6. 清理结果写入审计日志。

如果工作区清理失败，接口不能伪装成完全成功，应返回清理状态并提供后台重试。

### 10.4 删除批任务

批任务状态分为两类：

| 批任务状态 | 删除行为 |
|---|---|
| `completed/partial/failed` | 允许删除 |
| `pending/running/paused` | 先取消或停止，再删除 |

推荐交互：

```text
运行中的批任务不能直接删除。
是否先停止任务并删除？
[取消] [停止并删除]
```

后端必须在事务中锁定批任务行，确认没有可执行或正在执行的子任务后才能进入删除流程。

Worker 必须在认领、执行和写回前检查：

- 批任务是否已取消。
- 子任务是否仍属于该批任务。
- 会话是否已经标记为删除。

## 11. 统一错误模型

所有 AI Chat 相关接口统一返回：

```json
{
  "error": {
    "code": "OPENCODE_UNAVAILABLE",
    "message": "AI 运行时暂时不可用",
    "retryable": true,
    "operation": "send_message",
    "turnId": "turn-001",
    "requestId": "req-001"
  }
}
```

建议错误码：

| 错误码 | 说明 | 是否可重试 |
|---|---|---|
| `SESSION_NOT_FOUND` | 会话不存在或无权访问 | 否 |
| `SESSION_CLOSED` | 会话已关闭 | 否，先重开 |
| `SESSION_DELETED` | 会话已删除 | 否 |
| `TURN_ALREADY_RUNNING` | 当前已有回合执行 | 否，等待或停止 |
| `OPENCODE_UNAVAILABLE` | OpenCode 不可用 | 是 |
| `OPENCODE_TIMEOUT` | OpenCode 请求超时 | 视操作而定 |
| `PROVIDER_AUTH_ERROR` | 模型认证失败 | 否，修改配置后重试 |
| `CONTEXT_LIMIT` | 上下文超限 | 否，压缩或新建会话 |
| `ABORTED` | 用户主动停止 | 否 |
| `WORKSPACE_CLEANUP_FAILED` | 工作区清理失败 | 是 |
| `BATCH_NOT_TERMINAL` | 批任务尚未进入可删除状态 | 否，先停止 |
| `VERSION_CONFLICT` | 状态已被其他请求修改 | 是，重新加载 |

前端必须根据 `retryable` 和错误码决定是否显示重试按钮，不能只显示原始异常文本。

## 12. 前端错误态和恢复入口

### 12.1 会话主区域

增加以下状态展示：

- 消息加载中。
- 消息加载失败。
- 正在同步服务端状态。
- 连接已断开，正在重连。
- 连接恢复但状态不确定。
- 本轮执行失败。
- 本轮已停止。

### 12.2 文件和辅助数据

以下区域必须区分“空数据”和“加载失败”：

- 工作区文件。
- 变更文件。
- 子代理轨迹。
- Prompt 模板。
- 长期记忆。
- 批任务详情。

统一提供：

```text
加载失败
[重新加载]
```

### 12.3 防重复提交

以下操作执行期间必须禁用同一资源上的重复按钮：

- 发送。
- 停止。
- 关闭。
- 删除。
- 批任务取消。
- 批任务删除。
- 重试。
- 重新执行。

## 13. 后端实现边界

### 13.1 AI Chat

重点改造：

- `server/routes/ai_chat.py`
- `server/utils/chat_persist.py`
- `server/utils/opencode_client.py`
- `server/utils/session_token.py`
- `server/utils/workspace.py`

### 13.2 批任务

重点改造：

- `server/routes/ai_chat_batches.py`
- `server/utils/batch_repo.py`
- `server/utils/batch_engine.py`

### 13.3 前端

重点改造：

- `src/stores/aiChat.ts`
- `src/api/aiChat.ts`
- `src/views/ai-chat/AiChatView.vue`
- `src/components/ai-chat/BatchGroup.vue`
- `src/stores/aiChatBatches.ts`

## 14. 测试要求

### 14.1 后端单元测试

覆盖：

- 发送异常后回合最终为 `failed`。
- 相同 `clientRequestId` 不会重复执行。
- abort 接口重复调用保持幂等。
- 关闭运行中会话会先停止回合。
- 删除运行中批任务会返回明确的状态错误或进入停止流程。
- Worker 不会认领已取消或已删除批任务。
- 非本人会话和批任务不能读取或操作。
- 工作区清理失败会产生明确状态。
- 所有错误都返回结构化错误码。

### 14.2 前端单元测试

覆盖：

- 发送失败后 `streaming` 和 `thinking` 被清除。
- SSE 重连后执行状态同步。
- 消息补偿不会重复渲染。
- 终态会清除排队和加载状态。
- 重试会创建新回合，不重复提交原回合。
- 停止、关闭、删除按钮具备防重复状态。
- 加载失败与空数据显示不同 UI。

### 14.3 Playwright E2E

必须覆盖以下真实流程：

1. 正常发送并完成一轮对话。
2. 模拟发送接口失败，确认出现重试按钮且输入区恢复。
3. 中断 SSE，确认出现重连状态。
4. 重连后确认历史消息和回合状态正确收敛。
5. 发送期间点击停止，确认回合变为已停止。
6. 关闭运行中会话，确认先停止后关闭。
7. 运行中批任务执行“停止并删除”，确认 Worker 不再写回。
8. 删除失败或清理失败时，确认页面显示可操作错误。
9. 切换会话后返回原会话，确认状态和消息不丢失。
10. 刷新浏览器后，确认运行中和失败状态可以恢复显示。

涉及 SSE 的验证还必须通过生产代理 `:8080` 验证增量响应，不能只通过 Vite 开发代理。

## 15. 观测和日志

每个回合和关键操作必须带：

- `sessionId`
- `turnId`
- `clientRequestId`
- `requestId`
- `opencodeSessionId`

需要记录：

- 回合创建。
- OpenCode 请求开始和结束。
- SSE 断开和重连。
- 状态同步结果。
- abort 请求。
- 关闭、删除和清理结果。
- Worker 跳过已取消任务的原因。
- 自动恢复失败原因。

日志中不得记录 JWT、session token 或完整 Prompt 中的敏感内容。

## 16. 实施阶段

### P0.1：统一错误和前端状态收敛

- 统一发送异常处理。
- 修复 `streaming/thinking` 卡死。
- 增加结构化错误响应。
- 增加错误卡片和重试入口。
- 增加操作级 loading 和防重复。

### P0.2：服务端回合状态和断线恢复

- 增加回合状态持久化。
- 增加运行时状态接口。
- 增加消息同步机制。
- 重连后执行状态同步和消息补偿。
- 验证终态正确收敛。

### P0.3：生命周期和批任务删除治理

- 明确停止、关闭、取消、删除语义。
- 关闭运行中会话时先停止回合。
- 运行中批任务执行“停止并删除”。
- Worker 增加取消和删除状态检查。
- 工作区清理改为可追踪、可重试。

### P0.4：真实场景验证

- 完成后端单元测试。
- 完成 Store/API 测试。
- 完成 Playwright 端到端测试。
- 通过生产代理验证 SSE。
- 增加断线、重启和清理失败场景。

## 17. 验收标准

1. 任意发送失败后，输入区不会长期显示“正在回复”。
2. 用户可以在不重复创建回合的情况下重试失败操作。
3. SSE 断线恢复后，消息、文件和回合状态能够自动同步。
4. 错过 `session.idle` 不会导致会话永久处于运行中。
5. 停止操作能够真正中止当前 OpenCode 回合。
6. 关闭运行中会话时不会遗留继续执行的回合。
7. 运行中批任务不能被无保护地直接删除。
8. 批任务停止并删除后，Worker 不再写回消息、状态或文件。
9. 删除和工作区清理失败时，用户能看到明确状态和重试入口。
10. 普通会话、批任务子会话和管理员会话的状态语义一致。
11. 所有异步操作具备幂等或状态条件保护。
12. 刷新页面、切换会话和短暂断网不会丢失已持久化消息。
13. 所有错误状态都能区分“暂无数据”和“加载失败”。
14. 通过 Playwright 和生产代理完成断线、停止、关闭和删除场景验证。

## 18. 相关文档

- `docs/superpowers/specs/2026-09-15-batch-session-search-spec.md`
- `docs/superpowers/specs/2026-09-15-opencode-runtime-config-management-spec.md`
- `docs/superpowers/specs/2026-06-21-ai-chat-session-governance-design.md`
- `docs/superpowers/specs/2026-06-05-ai-chat-server-side-turn-persistence-design.md`
- `docs/user-guide/ai/assistant.md`
