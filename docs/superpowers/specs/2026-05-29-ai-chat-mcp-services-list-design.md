# AI 助手「MCP 服务」列表 — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 后端(MCP server `/tools` + opencode_client + ai_chat 路由) + 前端(api/store/视图 + 新组件)

## 背景

用户在 AI 助手输入 `/mcps`，期望看到本系统的 MCP 服务，却得到模型的瞎猜回答「当前环境中没有 MCP 服务配置文件」。

### 关键发现（已验证）

- 前端**不做任何斜杠命令处理**：输入框内容原样发给 OpenCode/模型。`/mcps` 因此被当成普通 prompt 发给 MiMo，模型去找「MCP 配置文件」没找到，于是瞎答。
- 确定性事实：本系统有 **1 个 MCP 服务 `check-manage`（状态 connected）**，暴露 6 个工具：`list_collections`、`query_collection`、`export_collection_excel`、`run_python`、`read_upload`、`save_artifact`。
- OpenCode `GET /mcp?directory=<workspace>` 返回 `{"check-manage": {"status": "connected"}}`（服务名 + 连接状态）。
- OpenCode 的 `/experimental/tool/ids` **只列内置工具**，不含 MCP 工具；故工具清单要从**我方 MCP server** 取，不能依赖 OpenCode。
- 配置常量：`server/config.py` `MCP_SERVER_URL='http://127.0.0.1:3003'`；`server/routes/ai_chat.py` `MCP_NAME='check-manage'`。

## 目标 / 非目标

**目标**：输入 `/mcps`（或 `/mcp`）→ 在对话流里**确定性渲染**一个块，列出已配置的 MCP 服务（名称 + 连接状态）及每个服务的工具（名称 + 描述）。**不经过模型**，所以永远准确。

**非目标**：
- 不持久化（临时块，刷新即消失，像终端命令输出）。不进 DB。
- 不做工具的在线调用/参数表单（本轮只列清单）。
- 不改 OpenCode；不依赖模型主动调工具。
- 不做通用斜杠命令框架（只硬编码 `/mcps`、`/mcp` 两个触发词，YAGNI）。

## 设计

### 数据流

```
用户输入 /mcps
  → 前端 send() 拦截（trim 后等于 /mcps 或 /mcp）→ 不发给模型
  → store.showMcpServices()
       → GET /ai/chat/sessions/:id/mcp   (Flask)
            ├─ OpenCode  GET /mcp?directory=<workspace>   → {name: {status}}
            └─ 我方 MCP  GET {MCP_SERVER_URL}/tools         → [{name, description}]
       → 合并 { servers: [{ name, status, tools: [{name, description}] }] }
  → push 一条本地临时消息 { role:'assistant', content:[{ type:'mcp_services', servers }] }
  → AiChatView 用 McpServicesBlock 渲染
```

### 1. 后端

**`mcp-server/main.py`** — 新增 `GET /tools`：
- 返回 `[{ "name": <tool.name>, "description": <tool.description or ""> } for spec, _ in _TOOLS.values()]`。
- 从 `tools.register_all` 暴露的注册表取（在 `tools/__init__.py` 把 `_TOOLS` 提供出来，或加一个 `tool_specs()` 辅助函数返回 `[(name, description)]`，`main.py` 调用它）。
- 无需鉴权：只是工具清单（名称+描述），不含任何业务数据；与 `/health` 同级，**不**走 `TokenMiddleware`（路径不以 `/mcp` 开头，天然跳过）。

**`server/utils/opencode_client.py`** — 新增 `list_mcp(directory: str = "") -> dict`：
- `GET /mcp`，`params = {"directory": directory} if directory else None`。
- `resp.raise_for_status()`；返回 `resp.json()`（形如 `{"check-manage": {"status": "connected"}}`）。
- 失败时抛 `OpenCodeError`（与现有方法一致）。

**`server/routes/ai_chat.py`** — 新增 `GET /sessions/:id/mcp`（`@login_required`）：
- 校验会话归属（同其它会话路由）；取 `workspace_path`。
- `servers_raw = OpenCodeClient(OPENCODE_BASE_URL).list_mcp(workspace_path)`；OpenCode 失败 → 返回 `{"servers": [], "error": "opencode unavailable"}`（HTTP 200，前端展示提示）。
- 取工具：`requests.get(f"{MCP_SERVER_URL}/tools", timeout=5).json()`；失败 → `tools=[]`（非致命）。
- 合并：对 `servers_raw` 每个 `name`，`tools = our_tools if name == MCP_NAME else []`；`status = servers_raw[name].get("status", "unknown")`。
- 返回 `{"servers": [{"name", "status", "tools": [{"name","description"}]}]}`，按 name 排序。

### 2. 前端

**`src/api/aiChat.ts`**：
- 类型 `McpTool = { name: string; description: string }`；`McpServer = { name: string; status: string; tools: McpTool[] }`。
- 在 `AiContentPart` 联合类型里加 `{ type: 'mcp_services'; servers: McpServer[] }`。
- `getMcpServices(sid) → GET /ai/chat/sessions/:id/mcp`，返回 `{ servers: McpServer[]; error?: string }`。

**`src/stores/aiChat.ts`** — 新增 `showMcpServices()`：
- 取 `activeSessionId`；`const { servers, error } = await getMcpServices(sid)`；
- `this.messages[sid].push({ id: 'mcp_' + Date.now(), role: 'assistant', content: [{ type: 'mcp_services', servers: error ? [] : servers }] })`。
- 失败静默不抛（模仿 `loadFiles`），但块内会因 `servers` 空而显示「无法获取」。

**`src/views/ai-chat/AiChatView.vue`**：
- `send()` 拦截：`const cmd = text.toLowerCase(); if (cmd === '/mcps' || cmd === '/mcp') { input.value=''; if(!activeId.value) await newSession(); await store.showMcpServices(); return }`（在现有 `sendUserMessage` 之前）。
- 消息渲染里加分支：`<McpServicesBlock v-else-if="p.type === 'mcp_services'" :servers="p.servers" />`。

**`src/components/ai-chat/McpServicesBlock.vue`**（新）：
- props `servers: McpServer[]`。
- 标题「MCP 服务 (N)」；每个服务一行：状态点（connected=绿/其它=灰）+ 服务名 + 状态文案；其下列工具 `• name — description`。
- `servers` 为空时显示「无法获取 MCP 服务（OpenCode 不可用）」。

## 错误处理

| 失败点 | 行为 |
|--------|------|
| OpenCode `/mcp` 不可用 | 路由返回 `{servers:[], error}`；块显示「无法获取」 |
| 我方 MCP `/tools` 不可用 | 该服务 `tools=[]`，仍显示服务名+状态 |
| 会话不属于当前用户 | 403（同其它会话路由） |

## 测试

**后端**
- `server/tests/test_opencode_client.py`：`list_mcp` 传 `?directory=`、解析 JSON（mock `requests.get`）。
- `mcp-server/tests/`：`GET /tools` 经 FastAPI TestClient 返回 6 个注册工具名（断言含 `list_collections`、`query_collection` 等，且每项有 `description`）。
- `server/tests/test_routes_ai_chat.py`：`GET /sessions/:id/mcp` 归属校验；合并逻辑（mock `OpenCodeClient.list_mcp` 返回 `{check-manage:{status:connected}}` + mock `/tools` 取的工具）→ 断言返回结构、工具挂到 `check-manage`；OpenCode 失败 → `{servers:[],error}`。

**前端（Vitest，stub Element Plus）**
- `stores`：`showMcpServices` 调 `getMcpServices` 后向 `messages[sid]` push 一条含 `mcp_services` part 的消息；`getMcpServices` 失败时 push 空 `servers`。
- `AiChatView`/send 拦截：输入 `/mcps` 调 `store.showMcpServices` 而非 `sendUserMessage`（可在组件测试或抽出的纯函数里验证）。
- `McpServicesBlock`：渲染服务名/状态点/工具列表；`servers` 空时显示「无法获取」。

**真机**：在 AI 助手输入 `/mcps` → 出现「MCP 服务 (1)」块，`check-manage` connected + 6 个工具。

## 影响文件清单

- 改：`mcp-server/main.py`、`mcp-server/tools/__init__.py`（暴露工具名+描述）
- 改：`server/utils/opencode_client.py`、`server/routes/ai_chat.py`
- 改：`src/api/aiChat.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`
- 新增：`src/components/ai-chat/McpServicesBlock.vue`
- 测试：`server/tests/test_opencode_client.py`、`mcp-server/tests/test_tools_endpoint.py`(新)、`server/tests/test_routes_ai_chat.py`、前端相应 `__tests__`
