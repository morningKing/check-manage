# AI Chat 功能说明与方案设计

> 面向 check-manage 平台的「类 Claude」AI 助手。前端对接 **OpenCode** agent 运行时，通过独立 **MCP server** 打通平台数据，服务于巡检用例脚本开发、自测试与业务数据问答。
>
> 适用范围：本文档描述第一里程碑（M1）已落地并经实测验证的功能与设计。文末标注了 M1 边界与后续计划。

---

## 第一部分 · 功能使用说明

### 1. 这是什么

右侧抽屉式 AI 助手。它不是直连大模型，而是对接一个具备工具调用能力的 **agent 运行时（OpenCode）**：

- 可与你多轮对话、流式返回 Markdown 答复；
- 通过 MCP 工具读取本平台的业务数据（按你的角色做权限过滤）回答问题；
- 每个会话拥有独立的工作目录（`uploads/` 与 `outputs/`），为「读取上传文件 / 产出脚本文件」打底（上传/下载 UI 在后续里程碑提供）。

当前使用的大模型：**MiMo（`mimo-v2.5`）**，通过 OpenCode 的自定义 provider 接入。

### 2. 开始使用

1. 顶部导航栏点击 **「AI 助手」** 按钮，右侧抽屉打开。
2. 首次打开会自动创建一个新会话（也可在空态点「开启新会话」）。
3. 在底部输入框输入问题，**Enter 发送，Shift+Enter 换行**。
4. 助手的回复以 Markdown 流式呈现；生成期间输入框禁用，生成结束后恢复。

### 3. 权限

| 角色 | 能否使用 |
|------|----------|
| admin | ✅ 完整使用；MCP 工具可见全部数据集 |
| developer | ✅ 可对话、可让助手读取其有权限的数据集 |
| guest | ❌ 只读账号：创建会话 / 发送消息 / 删除会话均被拦截（403） |

助手通过 MCP 读取业务数据时，**按当前用户角色做 RBAC**：例如 `list_collections` 只返回该角色在菜单 `roles` 中可见的数据页（admin 全可见）。

### 4. 运行前置条件（运维/部署）

AI 助手依赖以下进程同时在线：

| 进程 | 默认地址 | 说明 |
|------|----------|------|
| Flask 后端 | `:3002` | 网关 + SSE 透传 |
| OpenCode | `opencode serve` `:4096` | agent 运行时（共享单实例） |
| MCP server | `mcp-server/` `:3003` | 独立服务，暴露平台工具 |
| PostgreSQL | `:5432` | 会话、消息、业务数据 |

并且：

- 已执行 `cd server && python init_db.py`（创建 `ai_chat_sessions` / `ai_chat_messages` 等表）。
- OpenCode 全局配置（`~/.config/opencode/opencode.json`）中已配置 **MiMo provider**（含 endpoint 与 API key）。**密钥只存在 OpenCode 自己的配置里，不进本仓库。**

### 5. 常见问题

- **助手不回复 / 一直转圈**：确认 `opencode serve` 在线、MiMo provider 配置正确、API key 有效。
- **「拒绝访问」**：当前账号是 guest，无写权限。
- **助手看不到某数据页**：该数据页的菜单 `roles` 不含你的角色（admin 例外）。
- **浏览器流式无响应但接口正常**：检查 EventSource 是否带上了 `?access_token=`（见设计 §4.4）。

---

## 第二部分 · 方案设计文档

### 1. 背景与目标

- **核心目标**：巡检用例脚本开发与自测试 —— 根据方案输出 Python 脚本，集成知识库搜索、用例开发等 skill 能力。
- **次要目标**：打通平台数据，回答业务数据问题。
- **关键理念**：**「加 skill 即获得能力，前端零修改」** —— 能力下沉到 OpenCode，前端用通用工具调用渲染，新增 skill 不需要改前端。

### 2. 系统架构

```
浏览器(Vue)
   │  REST + SSE（仅与 Flask 通信）
   ▼
Flask 后端(:3002)  ── 网关 / SSE 透传 / 会话与令牌管理
   │  HTTP + SSE
   ▼
OpenCode(:4096, 共享单实例)  ── agent 运行时 / 工具调用 / 模型(MiMo)
   │  MCP (Streamable-HTTP, 带会话令牌)
   ▼
MCP server(:3003, 独立服务)  ── 平台工具(list_collections…) + RBAC
   │  SQL
   ▼
PostgreSQL(:5432)
```

设计要点：

- **浏览器只与 Flask 通信**，绝不直连 OpenCode/MCP；Flask 是唯一网关。
- **OpenCode 共享单实例**（P2 决策）：业务人员无需本地安装，部署侧统一提供。
- **MCP 独立于 Flask**：解耦，单独进程、单独依赖（`mcp` SDK + FastAPI + uvicorn）。
- **每会话隔离**：独立 workspace 目录 + 独立 `opencode.json` + 独立 opaque 令牌。

### 3. 组件清单

#### 3.1 数据库（2 张表）

- `ai_chat_sessions`：`id`、`user_id`(FK users)、`title`、`opencode_session_id`、`workspace_path`、`session_token`(唯一, opaque)、`token_expires_at`、`project_menu_id`、`branch_id`、`status`(active/revoked/deleted…)。
- `ai_chat_messages`：`id`、`session_id`(FK)、`role`(user/assistant)、`content`(JSONB，typed parts 数组)、`created_at`。

#### 3.2 Flask 后端（blueprint `ai_chat_bp`，前缀 `/ai/chat`）

| 方法 | 路由 | 鉴权 | 作用 |
|------|------|------|------|
| POST | `/sessions` | write_required | 建会话：建 workspace、生成令牌、写 `opencode.json`、`POST /session?directory=` |
| GET | `/sessions/:id/messages` | login_required | 历史消息（支持 `?since=`） |
| POST | `/sessions/:id/messages` | write_required | 落库用户消息 + `prompt_async`（带模型） |
| GET | `/sessions/:id/events` | login_required_sse | SSE 透传 OpenCode 事件，落库 assistant 消息 |
| DELETE | `/sessions/:id` | write_required | 删 OpenCode 会话、吊销令牌、标记删除、尽力清理目录 |

关键工具模块：

- `server/utils/opencode_client.py`：OpenCode HTTP/SSE 封装（见 §4.1–4.3）。
- `server/utils/workspace.py`：每会话目录（`uploads/`、`outputs/`）、`write_opencode_config`、`safe_resolve` 路径穿越防护。
- `server/utils/session_token.py`：opaque 令牌 `generate/renew/revoke`。
- `server/auth.py`：`login_required_sse`（见 §4.4）。
- `server/config.py`：环境变量（见 §6）。

#### 3.3 MCP server（`mcp-server/`，独立进程）

- `main.py`：FastAPI + MCP `StreamableHTTPSessionManager`，`/health`、`/mcp`；`TokenMiddleware` 从 `?token=` 取令牌，401 拦截无效/缺失。
- `auth.py`：`validate_session_token`（查 `ai_chat_sessions JOIN users`，校验 active + 未过期）。
- `context.py`：`ToolContext(session_id, user_id, role)`。
- `tools/list_collections.py`：返回当前角色可见的数据集（RBAC）。
- 新增工具：实现 `handle(input, ctx)` 并在 `tools/__init__.py` 注册即可，**前端无需改动**。

#### 3.4 前端（Vue 3）

- `src/api/aiChat.ts`：REST 封装 + `createEventStream`（EventSource，1s→2s→5s→10s 自动重连）。
- `src/stores/aiChat.ts`：Pinia store，按 part id「快照覆盖」累积流式文本，仅渲染 assistant 消息的 part。
- `src/components/ai-chat/`：`AiChatDrawer`、`MessageList`、`MessageItem`、`MarkdownView`(md-editor-v3)、`ChatInput`。
- 挂载点：`src/components/layout/AppLayout.vue` 顶栏「AI 助手」按钮 + 抽屉。

### 4. OpenCode 实测契约（v1.2.26）

> 注：以下契约均经对运行中的 OpenCode 实测确认，与最初设想存在差异（详见 `docs/superpowers/specs/2026-05-26-ai-agent-frontend-design.md` §12 课程修正）。

#### 4.1 会话与目录绑定

- `POST /session?directory=<abs path>`，body `{title}` → `{id:"ses_…", directory, …}`。
- **目录必须用 query 参数 `?directory=` 绑定**（body 内的 `cwd`/`directory` 被忽略）。
- `DELETE /session/{id}` → 200。

#### 4.2 发消息与模型选择

- `POST /session/{id}/prompt_async`，body `{parts:[{type:"text","text":…}], model:{providerID, modelID}}` → 204。
- **模型必须在 prompt 上显式传**：OpenCode **不**采用 `opencode.json` 的 `model` 字段做 prompt 选择，缺省会回退到它自己的默认模型。我们从 `OPENCODE_MODEL`（`mimo/mimo-v2.5`）拆出 `providerID/modelID` 传入。

#### 4.3 事件流（SSE）

- `GET /event?directory=<ws>`：**仅 `data:` 帧**（无 SSE `event:` 行），事件名在 JSON 的 `type` 字段：`data: {"type":"…","properties":{…}}`。
- 会话标识为 **`sessionID`（大写 ID）**，分别嵌于 `properties.sessionID` / `properties.part.sessionID` / `properties.info.sessionID`。
- 相关事件：

| 事件 | 含义 | 处理 |
|------|------|------|
| `message.updated` | 消息元数据 | `properties.info.role=='assistant'` → 记录 assistant 消息 id |
| `message.part.updated` | 流式内容片段 | `part.text` 为**当前快照**（按 `part.id` 覆盖，非累加 delta）；仅取 assistant 消息的 `type=='text'` part |
| `session.idle` | 一轮结束 | 落库 assistant 消息、关流式标志 |
| `session.error` | 出错 | 关流式标志 |

> MiMo v2.5 为推理模型，会先发 `reasoning` part 再发 `text` part；M1 仅渲染 `text`，`reasoning` 暂不展示。

#### 4.4 MCP 注册（每会话令牌）

- OpenCode **无每会话 MCP API**，只有按目录读取的配置。因此在每个会话的 workspace 写 `opencode.json`：

```json
{ "$schema": "https://opencode.ai/config.json",
  "model": "mimo/mimo-v2.5",
  "mcp": { "check-manage": { "type": "remote",
    "url": "http://127.0.0.1:3003/mcp?token=<session_token>", "enabled": true } } }
```

- 以 `?directory=<ws>` 创建会话后，OpenCode 按目录加载该配置并带令牌连接我们的 MCP（实测 `GET /mcp?directory=<ws>` 状态为 `connected`）。

#### 4.5 浏览器 EventSource 鉴权

- 浏览器原生 `EventSource` **无法设置请求头**，故 SSE 路由用 `login_required_sse`：除 `Authorization: Bearer` 外，也接受 `?access_token=<JWT>` query 参数；前端从 `localStorage('check-manage:token')` 取 JWT 追加到 URL。

### 5. 关键数据流

**建会话**：建 workspace → 落 `ai_chat_sessions` 行 → 生成 opaque 令牌 → 写 `opencode.json`（令牌 MCP url + 模型）→ `POST /session?directory=<ws>` → 回填 `opencode_session_id`。

**发消息 + 流式**：落库 user 消息 → `prompt_async(parts, model)` → 前端 `EventSource` 收 SSE → store 按 part id 覆盖累积 → `session.idle` 时后端落库 assistant 消息。

**身份链路**：Flask 生成令牌 → 写入 workspace 的 `opencode.json` → OpenCode 带令牌连 MCP → MCP 查 `ai_chat_sessions` 得 `user_id/role` → 工具按角色 RBAC。

### 6. 环境变量

```
# Flask 侧（server/config.py）
AI_WORKSPACE_ROOT      每会话工作目录根（默认 ../ai-workspaces）
OPENCODE_BASE_URL      OpenCode 地址（默认 http://127.0.0.1:4096）
MCP_SERVER_URL         MCP server 地址（默认 http://127.0.0.1:3003）
AI_SESSION_TTL_HOURS   令牌有效期小时数（默认 24）
AI_WORKSPACE_QUOTA_MB  工作区配额（默认 200，M1 未强制）
OPENCODE_MODEL         模型 "<providerID>/<modelID>"（默认 mimo/mimo-v2.5）

# MCP 侧（mcp-server/db.py，复用同一套 DB 配置）
DB_HOST / DB_NAME / DB_USER / DB_PASSWORD / DB_PORT

# OpenCode 侧（~/.config/opencode/opencode.json，不进仓库）
provider.mimo.options.{baseURL, apiKey} + provider.mimo.models
```

MiMo provider 配置样例（OpenCode 全局配置）：

```json
"mimo": {
  "name": "MiMo",
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "https://token-plan-cn.xiaomimimo.com/v1", "apiKey": "<KEY>" },
  "models": { "mimo-v2.5": { "name": "MiMo v2.5" }, "mimo-v2.5-pro": { "name": "MiMo v2.5 Pro" } }
}
```

### 7. 安全

- **令牌**：opaque、每会话唯一、可吊销、有 TTL；删除会话时**先吊销令牌 + 标记删除，再尽力清理目录**（避免文件锁导致会话残留为活跃态）。
- **RBAC**：MCP 工具按令牌反查的用户角色过滤数据。
- **路径穿越防护**：`safe_resolve` 用 `Path.resolve()` + `relative_to()`，并以 `os.path.isabs()` 兼容 Windows 拒绝 Unix 绝对路径。
- **密钥**：模型 provider 密钥仅存 OpenCode 全局配置，不进 git。

### 8. 测试

- 单元：前端 Vitest、后端 Pytest、MCP Pytest（`npm run test` / `npm run test:server` / `mcp-server` 内 `.venv` pytest）。
- 实库集成（默认跳过，opt-in）：`RUN_DB_INTEGRATION=1 … pytest mcp-server/tests/test_integration_db.py`，验证令牌校验 + `list_collections` RBAC。
- 端到端冒烟：`e2e/ai-chat-smoke.spec.ts`（`npm run test:e2e`，需 OpenCode + MCP 在线）。
- 实测验证（本机）：身份链路（OpenCode MCP `connected`）、MiMo 经 Flask 全链路回复（`PONG`）。

### 9. M1 边界与后续

| 已落地（M1） | 后续里程碑 |
|--------------|------------|
| 抽屉 UI、单会话、流式文本、Markdown 渲染 | 多会话列表、会话恢复 |
| MCP `list_collections` + RBAC | 知识库搜索、巡检用例开发等 skill / MCP 工具 |
| 工具调用以 JSON 文本展示 | `ToolCallBubble` + tool-renderers 注册表（通用渲染） |
| workspace 目录（服务端就绪） | 文件上传 / 输出文件下载 UI、配额强制 |
| 仅渲染 `text` part | 展示 `reasoning`、工具结果富渲染 |
