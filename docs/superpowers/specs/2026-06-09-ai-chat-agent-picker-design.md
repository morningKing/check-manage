# AI Chat — OpenCode primary agent 选择器 设计

- 日期：2026-06-09
- 状态：已评审待实现
- 关联：AI Agent Chat (M1)（`server/routes/ai_chat.py`、`server/utils/opencode_client.py`、`src/stores/aiChat.ts`、`src/api/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`）；既有 model picker 模式

## 背景与目标

OpenCode CLI 支持用 Tab 在 **primary agent** 之间切换（如 `build` / `plan`），但当前 AI chat 无法选择 OpenCode 的 agent，永远走默认 agent。本设计给聊天输入区加一个 **primary agent 选择器**，与现有 model 选择器同构：按会话记忆、随时可切、默认 `build`。

### 关键事实（已用运行中的 OpenCode v1.2.26 验证）

- `GET /agent` 返回 agent 列表，每项含 `name`、`description`、`mode`（`primary` | `subagent`）等。
- 当前实例的 agent：primary = `build`(默认)、`plan`、`compaction`、`title`、`summary`；subagent = `general`、`explore`。其中 `compaction`/`title`/`summary` 是 OpenCode **内部系统 agent**（描述为空），不应出现在用户选择器。
- `POST /session/{id}/prompt_async` 的请求体**支持 `agent` 字符串字段**（与 `model`、`parts` 并列）。传入即以该 agent 运行本轮对话。

### 范围

- **仅 primary agent**（`build`/`plan` 及用户自定义 primary）。subagent（`general`/`explore`）仍由主 agent 通过 task 工具自动调用，不在选择器中。
- 作用于 AI chat 的**普通消息**发送路径（`POST /ai/chat/sessions/:id/messages` → `send_prompt_async`）。slash 命令路径（`run_command`）本期不改。
- 不涉及批任务（M1.5）、AI 扫描任务——它们可在后续按需复用同一参数。

### 非目标（YAGNI）

- 不做 subagent 直选、不做 agent 的增删改（agent 由 OpenCode/工作区配置定义）。
- 不做 agent 与 model 的联动约束。

## 架构与数据流

与 model 选择器**完全平行**，逐项对应：

| 维度 | 现有 model | 新增 agent |
|------|-----------|-----------|
| OpenCode 列表 | `GET /provider` | `GET /agent` |
| 后端列表路由 | `GET /ai/chat/models` | `GET /ai/chat/agents` |
| prompt body 字段 | `model: {providerID, modelID}` | `agent: "<name>"` |
| 前端 store | `modelBySession` | `agentBySession` |
| 持久化 key | `check-manage:ai-chat:model:<sid>` | `check-manage:ai-chat:agent:<sid>` |
| 组合发送 | `sendMessage(sid, content, paths, model)` | `sendMessage(sid, content, paths, model, agent)` |

数据流：用户在输入区选 agent → store `setSessionAgent` 持久化 → 发送时 `send()` 读取 `agentBySession[sid]` 随 `sendMessage` 一起发到后端 → `send_message` 路由读 `body.agent` → `send_prompt_async(agent=...)` → OpenCode 以该 agent 运行本轮。

## 后端改动

### `server/utils/opencode_client.py`

1. 新增 `list_agents(directory: str = "") -> list`：`GET /agent`（与 `list_skills`/`list_commands` 同形，带可选 `?directory=`）。
2. `send_prompt_async(...)` 增加形参 `agent: str = ""`；当 `agent` 非空时 `body["agent"] = agent`。其余不变（`model`、`parts`、`directory` 逻辑保持）。

### `server/routes/ai_chat.py`

3. 新增路由 `GET /ai/chat/agents`（`@login_required`），返回：
   ```json
   { "agents": [{ "name": "build", "description": "..." },
                { "name": "plan",  "description": "..." }],
     "default": "build" }
   ```
   - 取 `OpenCodeClient(...).list_agents()`，**过滤**：保留 `mode == 'primary'` 且 `name not in {'compaction','title','summary'}`。
   - `default`：若过滤后存在 `build` 则 `'build'`，否则取过滤后第一个的 name；列表为空时 `default` 为 `null`。
   - OpenCode 不可达：返回 `502` + `{ "agents": [], "default": null, "error": ... }`（与 `/models` 的 502 处理一致）。
4. `send_message` 路由：在已有 `requested_model` 旁读取 `requested_agent = (body.get('agent') or '').strip()`，调用 `send_prompt_async(..., agent=requested_agent)`。响应体附带 `"agent": requested_agent or None`（与现有 `"model"` 对称，便于前端/测试断言）。

> 内部 agent 名集合 `{'compaction','title','summary'}` 以模块级常量定义，附注释说明这是 OpenCode 内部系统 agent。

## 前端改动

### `src/api/aiChat.ts`

5. 新增 `listAgents()` → `GET /ai/chat/agents`，类型 `{ agents: AgentInfo[]; default: string | null }`，`AgentInfo = { name: string; description: string }`。
6. `sendMessage` 增加 `agent = ''` 形参，body 加 `agent`：
   `post(..., { content, attachments, model, agent })`。

### `src/stores/aiChat.ts`

7. 新增 state `agentBySession: Record<string, string>`（镜像 `modelBySession`）。
8. `send()` 中读取 `const agent = this.agentBySession[sid] || ''`，传入 `sendMessage(sid, content, paths, model, agent)`。
9. 新增 `setSessionAgent(sessionId, agent)` + `hydrateSessionAgent(sessionId)`（镜像 `setSessionModel`/`hydrateSessionModel`，localStorage key `check-manage:ai-chat:agent:<sid>`）。

### `src/views/ai-chat/AiChatView.vue`

10. 输入区在 model 下拉旁新增 **agent 下拉**：
    - 挂载/会话切换时 `listAgents()` 拉取选项并 `hydrateSessionAgent(sid)`。
    - 选项 = 后端 `agents`；当前值 = `agentBySession[sid]`，未设置时回落到后端 `default`。
    - 选择即 `setSessionAgent(sid, name)`，随时可切，下一条消息生效。
    - 拉取失败时下拉仅含 `build`（或后端 default），不阻断发送。

## 错误处理

- 空/未选 agent → body 不带 `agent` → OpenCode 用默认 agent（向后兼容，旧会话无感）。
- `/agent` 不可达：后端 502、前端容错为默认单项，发送不受影响。
- 选了一个 OpenCode 已不存在的 agent（配置变更）：OpenCode 自行回落/报错；前端按既有 SSE 错误处理展示，不额外特判。

## 测试

- **后端 `server/tests/test_routes_ai_chat.py`**（或同目录新增）：
  - `GET /ai/chat/agents` mock `list_agents` 返回含内部 agent + subagent 的列表，断言只保留 build/plan、`default == 'build'`、形状正确。
  - `send_message` 带 `agent` 时，断言 `send_prompt_async` 收到该 `agent`；不带时为 `''`（走默认）。
- **前端 `src/stores/__tests__/aiChat.*.test.ts`**：
  - `setSessionAgent` 写入并持久化；`hydrateSessionAgent` 从 localStorage 还原。
  - `send()` 把 `agentBySession[sid]` 透传给 `sendMessage`（mock 断言参数）。

## 影响文件清单

修改：
- `server/utils/opencode_client.py`（`list_agents` + `send_prompt_async` 加 `agent`）
- `server/routes/ai_chat.py`（`/ai/chat/agents` 路由 + `send_message` 读 agent）
- `src/api/aiChat.ts`（`listAgents` + `sendMessage` 加 agent）
- `src/stores/aiChat.ts`（`agentBySession` + set/hydrate + send 透传）
- `src/views/ai-chat/AiChatView.vue`（agent 下拉）
- 对应测试文件
