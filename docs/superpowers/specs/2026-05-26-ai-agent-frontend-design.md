# AI Agent 前端集成设计（OpenCode + MCP）

- 日期：2026-05-26
- 状态：草案，待实施
- 目标读者：负责实施的工程师、未来维护者

## 1. 背景与目标

巡检用例管理平台目前没有交互式 AI 能力。已有的 `routes/ai.py` 仅做"自然语言 → MongoDB filter"翻译，不是对话型 Agent，前端也没有任何承载组件。

本期新建一套 **Claude 风格的 AI 交互面板**，定位是**多功能交互窗口**：

主线能力（巡检脚本开发）：
- 根据方案描述自动生成 Python 巡检脚本
- 通过 Skill 提供"巡检用例开发规范"约束
- 通过 Skill 提供"知识库搜索"用于规范/历史用例查找
- 支持多文件输入（上传方案、参考文档），多文件输出（生成的脚本集合）

附线能力（业务数据问答）：
- Agent 可调用工具查询 `dynamic_data` / `page_configs` / `menus`
- 用自然语言回答用户关于业务数据的问题

不在本期范围：
- 嵌入向量检索 / RAG（知识库第一期用 ILIKE + 全文索引）
- 多 Agent 编排
- Agent 自动执行/部署生成的脚本
- 移动端适配

## 2. 关键设计决策（已与用户确认）

| 决策 | 选项 | 决定 | 原因 |
|------|------|------|------|
| Agent 后端 | OpenCode / Claude Code / 双适配 | **OpenCode** | 用户既有方案 |
| OpenCode 部署位置 | 用户机器 / 服务器共享 / 混合 | **服务器共享** | 用户群包含非开发岗（业务人员），不能要求每人装 CLI |
| UI 承载 | 抽屉 / 全屏页 / 浮窗 | **侧边抽屉** | 任一页面可唤起，保留业务页面上下文 |
| 文件模式 | 工作区 / 一次性附件 / 项目路径 | **会话工作区** | 多文件多轮交互，符合脚本开发的真实工作流 |
| 工具挂接 | MCP 独立服务 / OpenCode 原生 skill / 不集成 | **MCP 独立 HTTP 服务** | 与 Flask 解耦，可独立部署/升级/换语言 |
| 浏览器到 OpenCode | 直连 / Flask 透传 | **Flask 透传** | 浏览器不能跨机访问开发者本地服务；统一鉴权与审计入口 |
| MCP 鉴权 | HMAC 签名 token / opaque token + 查库 | **opaque token + 查库** | 支持撤销；多一次 DB 查询可接受 |
| 前端选型 | assistant-ui (React) / Vue 自组装 / Vue 克隆改造 | **Vue 自组装** | 项目已有 md-editor-v3 / CodeMirror / Element Plus 覆盖 80% 视觉重活；避免框架混合 |
| 错误场景覆盖 | 12 个 | 接受 | 见 §7 |
| E2E 测试粒度 | 单冒烟 / 完整矩阵 | **单 Playwright 冒烟** | 第一期不上 E2E 矩阵 |

## 3. 非功能约束

### 3.1 可扩展性约束（硬约束）

**"加 skill 即可获得能力，前端零修改"** 是本设计的核心承诺：

- 任何加进 `mcp-server/tools/` 的新工具，OpenCode 自动通过 MCP `tools/list` 发现，前端 `ToolCallBubble` 通用渲染立即可用。
- 任何加进 OpenCode `.opencode/skills/*.md` 的提示词 skill，OpenCode 自动加载，前端无感。
- 任何在 OpenCode `.opencode/config.json` 挂载的第三方 MCP，等效于自家工具。

**实现层面强制约束**：
- `ToolCallBubble.vue` **禁止**写 `if (toolName === '...')` 的硬编码分支。
- 工具的"专用 UI"通过 `components/ai-chat/tool-renderers/index.ts` 注册表挂接，未注册的工具走默认 JSON/代码渲染。
- 此约束有专门测试用例（§8）；如要修改通用渲染路径，必须先更新这两条用例。

### 3.2 隔离约束

- 多用户：每会话独立工作区目录、独立 OpenCode session、独立 MCP token。
- 故障隔离：OpenCode 或 MCP server 任一不可用，**不影响平台其他功能**；AI 抽屉自身降级到红条提示。

### 3.3 安全约束

- Workspace 路径穿越防御（`Path.resolve()` 必须落在 `AI_WORKSPACE_ROOT` 下）。
- 上传文件名 slugify。
- 文件扩展名白名单（脚本/文本/图片，禁止可执行/压缩包套娃，详见 §6.3）。
- MCP token 在 URL 查询参数里传递，**只走 loopback**（OpenCode → MCP 都在 127.0.0.1），不落任何外部访问日志。
- 所有 AI 接口 Flask 现有 JWT + RBAC 覆盖；guest 只读，禁用生成型工具。

## 4. 系统架构

### 4.1 进程拓扑

```
Server (one host):
  ├── Flask          :3002    (existing, gateway)
  ├── opencode serve :4096    (NEW, managed by systemd)
  ├── mcp-server     :3003    (NEW, managed by systemd)
  └── PostgreSQL              (shared)

Filesystem:
  └── ${AI_WORKSPACE_ROOT}/<userId>/<sessionId>/
        ├── uploads/<原文件名>
        └── outputs/<agent 生成的脚本>
```

### 4.2 顶层目录

```
check-manage/
├── server/         # Flask (existing)
├── src/            # Vue 前端 (existing)
└── mcp-server/     # NEW: 独立 MCP HTTP 服务
    ├── pyproject.toml
    ├── main.py             # FastAPI app, /mcp endpoint (Streamable HTTP)
    ├── auth.py             # validate_session_token() 查 ai_chat_sessions
    ├── db.py               # 自有 psycopg2 pool
    ├── context.py          # 从 token 解出 user_id / session_id / project_menu_id
    └── tools/
        ├── __init__.py     # register_all(server)
        ├── list_collections.py
        ├── query_dynamic_data.py
        ├── search_knowledge_base.py
        └── generate_inspection_case.py
```

### 4.3 数据流：用户身份如何流到 MCP

1. 用户开新会话 → Flask `POST /ai/chat/sessions`
2. Flask 生成 opaque `session_token`（`secrets.token_urlsafe(32)`），写入 `ai_chat_sessions` 表
3. Flask 调 OpenCode `POST /session`（cwd = workspace 路径），拿 `opencode_session_id`
4. Flask 调 OpenCode `POST /mcp` 注册 MCP server，URL = `http://127.0.0.1:3003/mcp?token=<session_token>`
5. Agent 调工具时 OpenCode 带 token 访问 MCP；MCP 查表得 `user_id / session_id`，做 RBAC

## 5. 组件清单

### 5.1 数据库（2 张新表）

```sql
CREATE TABLE ai_chat_sessions (
    id                  VARCHAR(100) PRIMARY KEY,         -- 'sess_' + 12char
    user_id             VARCHAR(100) NOT NULL,
    title               VARCHAR(500),                     -- 建会话时写入 '新会话'；首条 assistant 响应 finished 后由 Flask 取响应前 80 字符 update 一次
    opencode_session_id VARCHAR(200),                     -- OpenCode 内部 id
    workspace_path      TEXT NOT NULL,                    -- 绝对路径
    session_token       VARCHAR(64) NOT NULL UNIQUE,      -- opaque, urlsafe_b64
    token_expires_at    TIMESTAMPTZ NOT NULL,             -- 默认 24h，活跃则续期
    project_menu_id     VARCHAR(100),                     -- 可选：会话归属项目
    branch_id           VARCHAR(100) DEFAULT 'main',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ DEFAULT NOW(),
    status              VARCHAR(20) DEFAULT 'active'      -- active / archived / error / degraded
);
CREATE INDEX idx_chat_sess_user ON ai_chat_sessions(user_id, last_active_at DESC);
CREATE INDEX idx_chat_sess_token ON ai_chat_sessions(session_token);

CREATE TABLE ai_chat_messages (
    id          VARCHAR(100) PRIMARY KEY,
    session_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,    -- user / assistant / tool
    content     JSONB NOT NULL,          -- 结构化：[{type:'text',...}, {type:'tool_use',...}, {type:'tool_result',...}]
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_chat_msg_sess ON ai_chat_messages(session_id, created_at);
```

DDL 加进 `server/init_db.py`。不动现有表。

### 5.2 Flask 后端（1 个新 blueprint）

`server/routes/ai_chat.py`：

| 方法 | 路径 | 鉴权装饰器 | 说明 |
|------|------|----------|------|
| POST | `/ai/chat/sessions` | `write_required` | 建会话，返回 `{id, title, workspacePath}` |
| GET | `/ai/chat/sessions` | `login_required` | 列当前用户会话（分页） |
| DELETE | `/ai/chat/sessions/:id` | `write_required` | 删 OpenCode session、撤销 token、清工作区 |
| PATCH | `/ai/chat/sessions/:id` | `write_required` | 改标题、归档 |
| GET | `/ai/chat/sessions/:id/messages` | `login_required` | 历史消息回放，支持 `?since=<msgId>` 增量 |
| POST | `/ai/chat/sessions/:id/messages` | `write_required` | 落库 user message → 转发 OpenCode `prompt_async` |
| GET | `/ai/chat/sessions/:id/events` | `login_required` | **SSE 透传** OpenCode 事件流，过滤本 session 事件，落库 assistant/tool messages |
| POST | `/ai/chat/sessions/:id/files` | `write_required` | multipart 上传到 `<workspace>/uploads/` |
| GET | `/ai/chat/sessions/:id/files` | `login_required` | 列工作区文件树 |
| GET | `/ai/chat/sessions/:id/files/<path>` | `login_required` | 下载/预览单文件 |

辅助模块：
- `server/utils/opencode_client.py`：HTTP/SSE 客户端薄封装。`requests` + 手写 SSE 迭代器（`urllib3` 的 `read_chunked`）。
- `server/utils/workspace.py`：mkdir/cleanup/quota/path-traversal 防御。
- `server/utils/session_token.py`：生成、续期、撤销、过期清理（后台任务每 5 分钟跑）。

配置项（`server/config.py`）：
```python
AI_WORKSPACE_ROOT     = os.getenv('AI_WORKSPACE_ROOT', '/var/lib/check-manage/ai-workspaces')
OPENCODE_BASE_URL     = os.getenv('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
MCP_SERVER_URL        = os.getenv('MCP_SERVER_URL',    'http://127.0.0.1:3003')
AI_SESSION_TTL_HOURS  = _to_int(os.getenv('AI_SESSION_TTL_HOURS'), 24)
AI_WORKSPACE_QUOTA_MB = _to_int(os.getenv('AI_WORKSPACE_QUOTA_MB'), 200)
```

`server/app.py` 在 `dynamic_bp` 之前注册 `ai_chat_bp`。**路径前缀 `/ai/chat`**（`/ai` 已被现有 `ai_bp` 占用，避免冲突）。

### 5.3 MCP server 工具

| 工具 | 入参 | 出参 | RBAC | 备注 |
|------|------|------|------|------|
| `list_collections` | — | `[{collection, label, fields[]}]` | 所有角色 | 按 `menus.roles` JSONB 与 token 解出的 user role 取交集过滤，仅返回有访问权的数据页 |
| `query_dynamic_data` | `collection: str, filter: dict (mongo style), limit: int=50, fields: list[str]?` | `{total, records}` | 所有角色（按菜单 RBAC 过滤） | 复用 `mongo_query.translate`，强制注入 `branch_id` |
| `search_knowledge_base` | `query: str, top_k: int=5, collection_hint?: str` | `[{collection, recordId, snippet, score}]` | 所有角色 | 第一期跨 `dynamic_data.data` JSONB 做 `ILIKE` + 全文索引 |
| `generate_inspection_case` | `topic: str, requirements: str, template_id?: str` | `{filename, content, language}` | admin / developer | 拼 prompt 生成脚本，由 Agent 调 OpenCode write 落到 `<workspace>/outputs/` |

每个工具入口流程：
```python
def handle(input, ctx):
    # token 一律走 URL 查询参数（与 §4.3 一致），不支持 header 形式
    user = validate_session_token(ctx.query['token'])
    check_rbac(user, tool_name)
    return _run(input, user)
```

### 5.4 前端组件（Vue 3）

```
src/
├── api/aiChat.ts                            # axios + EventSource 工厂
├── stores/aiChat.ts                         # Pinia
├── components/ai-chat/
│   ├── AiChatDrawer.vue                     # el-drawer 容器，三栏布局
│   ├── SessionList.vue                      # 左：会话列表 + 新建
│   ├── MessageList.vue                      # 中：消息流（虚拟滚动）
│   ├── MessageItem.vue                      # 单条消息分发
│   ├── ToolCallBubble.vue                   # 通用工具调用气泡（禁止 if-name 分支）
│   ├── tool-renderers/                      # 工具专用 UI 注册表
│   │   ├── index.ts                         # toolRenderers: Record<string, Component>
│   │   ├── DefaultToolRender.vue            # JSON + 代码块 fallback
│   │   └── QueryResultTable.vue             # query_dynamic_data 专用（示例）
│   ├── MarkdownView.vue                     # md-editor-v3 readonly 封装
│   ├── CodeBlock.vue                        # CodeMirror 封装，带"保存到工作区"
│   ├── ChatInput.vue                        # 输入框 + 拖拽上传 + 项目上下文切换
│   ├── FilePanel.vue                        # 右：工作区文件树
│   └── AgentSettings.vue                    # 模型/agent 切换、workspace 展示
└── layouts/MainLayout.vue                   # 改：右上角 [AI 助手] 按钮 + 挂 AiChatDrawer
```

**Pinia store 形状**：
```ts
interface AiChatState {
  sessions: AiSession[]
  activeSessionId: string | null
  messages: Record<string, AiMessage[]>
  files: Record<string, FileNode[]>
  streaming: Record<string, boolean>
  eventSource: EventSource | null
  drawerOpen: boolean
}
```

### 5.5 现有代码改动（最小）

- `src/router/index.ts`：**不加新路由**（抽屉式不占路由）
- `src/views/admin/AiSettings.vue`：加 `agentEnabled` / `openCodeUrl`（管理员只读）/ `workspaceQuotaMb`
- `server/routes/dynamic.py`：保持不变
- `server/init_db.py`：加 2 张表 DDL

## 6. 数据流（关键序列）

### 6.1 建会话

1. 浏览器 `POST /ai/chat/sessions`，可选 `{projectMenuId}`
2. Flask：插入 `ai_chat_sessions` 行（生成 token，写 workspace_path），`mkdir workspace/{uploads,outputs}`
3. Flask 调 OpenCode `POST /session`（cwd = workspace），存 `opencode_session_id`
4. Flask 调 OpenCode `POST /mcp` 注册 `{url:".../mcp?token=<token>"}`
5. 返回 `{id, title:'新会话', workspacePath}`
6. 浏览器立即 `GET /ai/chat/sessions/:id/events` 建立 EventSource（**不等用户发第一条消息**，避免漏事件）

### 6.2 发消息 + 流式响应 + 工具调用

1. 浏览器 `POST /ai/chat/sessions/:id/messages` 带 user content
2. Flask 落库 user message → 转发 OpenCode `POST /session/:ocId/prompt_async` → 立即返回 204
3. OpenCode 通过已建立的 SSE 通道推事件：`message.part.start` → 多次 `message.part.delta`（token by token）→ `tool.use`（执行 MCP 工具调用）→ ... → `message.finished`
4. Flask SSE 透传层只做 4 件事：保持连接 / 转发字节 / 心跳保活 / 在 `message.finished` 时触发落库回调（**不解析 LLM 内容**）
5. 浏览器把 delta 追加到 `messages[]`，`tool.use` 进 ToolCallBubble，`finished` 后冻结气泡

### 6.3 上传文件

1. 浏览器 `POST /ai/chat/sessions/:id/files`（multipart）
2. Flask 校验：sessionId 归属当前用户 / 配额（`du -s`）/ 扩展名白名单 / slugify 文件名 / `Path.resolve()` 防穿越
3. 写入 `<workspace>/uploads/<safe-name>`
4. **自动注入系统提示**：前端把"用户刚上传了 `xxx.pdf`，路径 `uploads/xxx.pdf`，请在需要时查看"作为隐藏前缀拼到**下一条用户消息**前面发给 Agent
5. Agent 用 OpenCode 内置 read/glob 工具自主读取，无需我方 MCP

**扩展名白名单**（第一期）：
- 文本：`.txt .md .json .yaml .yml .csv .log`
- 脚本：`.py .js .ts .sh .sql .html .css`
- 数据：`.xlsx .xls`
- 图片：`.png .jpg .jpeg .gif .svg`
- 禁止：`.exe .bat .dll .so .zip .tar .gz .rar .7z`（套娃风险）

### 6.4 Agent 输出文件

- Agent 用 OpenCode 内置 write 工具写到 `<workspace>/outputs/`
- 前端发现机制：
  - 被动：用户切到"文件"Tab 时 `GET .../files` 拉一次
  - 主动：SSE 事件流里收到 `tool.use(name='write')` 后触发 FilePanel 刷新

### 6.5 关闭/恢复

- **关浏览器/刷新**：EventSource 断开，OpenCode session 不动，DB 保留。重开 → `GET /messages` 回放 → 重订阅 SSE
- **删会话**：`DELETE /sessions/:id` → OpenCode `DELETE /session` → 清工作区目录 → DB cascade 删消息 → 撤销 token
- **token 过期**：MCP 校验失败返回 `401 token_expired`；Flask 后台每 5 分钟给"最近 30 分钟活跃"会话续期

## 7. 错误处理与失败模式

| # | 失败场景 | 检测 | 处理 | 用户感知 |
|---|---------|------|-----|---------|
| 1 | OpenCode 不可用 | `requests` 超时/refused | Flask 503 + 错误码 `OPENCODE_UNAVAILABLE` | el-message 红条 |
| 2 | MCP server 不可用 | Flask `POST /mcp` 失败 | 会话仍可建，标记 `status='degraded'` | 抽屉顶部黄条 |
| 3 | SSE 中途断开 | 浏览器 `onerror` | 指数退避自动重连（1→2→5→10s，5 次）；重连后 `GET /messages?since=<lastMsgId>` 补齐 | 顶部小条"连接已恢复" |
| 4 | session_token 过期 | MCP 校验失败 | MCP 返回 401；Agent 停下；Flask 后台续期 | 通常无感 |
| 5 | 工作区超配额 | 每次上传前 `du` 检查 | 413 拒绝；前端禁用上传 | 弹窗 + 跳到文件 Tab |
| 6 | 路径穿越 | `Path.resolve()` 比对 root | 拒绝 + 落操作日志 | el-message "文件名不合法" |
| 7 | LLM 限流/Key 失效 | OpenCode SSE `error` 事件 | 透传，标红当前消息，不关 SSE | 气泡内显示错误，可重试 |
| 8 | 流式中重复发消息 | 前端置灰 + 后端检查 `streaming[sessionId]` | 后端 409 `STREAM_BUSY` | 输入框置灰 |
| 9 | DB 写入失败 | psycopg2 异常 | 记日志，**不打断 SSE**；内存 ring buffer 兜底，定时重试 | 用户继续；后台告警 |
| 10 | 用户角色被撤权 | MCP 工具二次 RBAC 校验 | 拒绝 + Agent 收错误 | 气泡显示"无权限"，对话继续 |
| 11 | 删会话时 OpenCode 404 | `DELETE /session` 404 | 视为成功 | 无感 |
| 12 | Agent 试图读 workspace 外文件 | OpenCode cwd 沙箱 + MCP 工具拒绝绝对路径 | 双重防御 | 气泡显示拒绝 |

**全局约定**：
- Flask AI 接口错误统一为 `{ "error": "<message>", "code": "<UPPER_SNAKE>" }`，前端 axios 拦截器按 code 分发
- OpenCode/MCP 不可用不影响其他业务页面；AI 抽屉是独立模块，启动失败时按钮置灰

## 8. 测试方案

### 8.1 前端 Vitest

| 测试 | 文件 | 关键用例 |
|------|------|---------|
| Pinia store | `src/stores/__tests__/aiChat.test.ts` | 建会话/切会话清流/SSE 事件追加/工具调用合并/历史回放 |
| EventSource | `src/api/__tests__/aiChat.test.ts` | 自动重连指数退避/`since` 补齐 |
| **ToolCallBubble 通用渲染**（约束测试） | `__tests__/ToolCallBubble.test.ts` | (a) 未注册工具 → DefaultToolRender；(b) 注册工具 → 专用组件；**(c) `ToolCallBubble.vue` 源码不允许出现工具名硬编码分支——grep 正则 `\.name\s*===\s*['"]` 与 `\bswitch\s*\(\s*\w*[Nn]ame` 都必须 0 命中** |
| MessageList 虚拟滚动 | `__tests__/MessageList.test.ts` | 1000 条消息只渲染可视窗口 |
| FilePanel | `__tests__/FilePanel.test.ts` | uploads/outputs 树形展示；新文件触发刷新 |
| ChatInput | `__tests__/ChatInput.test.ts` | 拖拽上传 → 自动插入系统提示 |

复用 CLAUDE.md 记录的 ResizeObserver polyfill 与 Element Plus stubs。

### 8.2 Flask Pytest

| 测试 | 文件 | 关键用例 |
|------|------|---------|
| 路由 + RBAC | `server/tests/test_routes_ai_chat.py` | 10 个接口的鉴权矩阵，跨用户访问被拒 |
| opencode_client | `server/tests/test_opencode_client.py` | `responses` 库 mock HTTP；SSE 透传迭代器；超时/重试 |
| workspace 防御 | `server/tests/test_workspace.py` | `..` / 绝对路径 / 符号链接攻击 |
| session_token | `server/tests/test_ai_session_token.py` | 生成/续期/过期/撤销 |
| SSE 透传集成 | `server/tests/test_ai_chat_sse.py` | mock OpenCode 推 3 个事件，断言全部转发 + DB 落库一次 |

### 8.3 MCP server Pytest（`mcp-server/tests/`）

| 测试 | 用例 |
|------|------|
| auth | 有效/过期/不存在/SQL 注入 token |
| query_dynamic_data | mongo filter 转译正确 / RBAC 注入 branch_id / guest 字段过滤 |
| **工具自动发现**（约束测试） | 临时新增 `tools/foo.py` → `tools/list` 自动包含；移除即消失 |
| MCP 协议合规 | `mcp inspect` 跑一遍 |

### 8.4 端到端

**1 个 Playwright 冒烟**（playwright 已在 devDependencies）：
登录 → 开会话 → 发"hello" → 收响应 → 上传文件 → 触发工具调用 → 关会话。**只验贯通，不验细节**。

### 8.5 不测什么（明示边界）

- OpenCode 本身（上游负责）
- LLM 输出质量（不可重现）
- markdown 渲染视觉（依赖 md-editor-v3）
- 负载/压测（单服务器场景不需要）

## 9. 部署与运维

### 9.1 新增系统依赖

服务器需安装：
- Node.js 18+（已有约束）
- `npm i -g opencode-ai`
- Python 3.10+（已有）

### 9.2 服务管理

3 个 systemd unit（或等价 supervisor 配置）：
- `check-manage-flask.service`（已有）
- `check-manage-opencode.service`：`opencode serve --port 4096 --workspace ${AI_WORKSPACE_ROOT}`
- `check-manage-mcp.service`：`uvicorn mcp-server.main:app --host 127.0.0.1 --port 3003`

`start.sh` 增加：检测 3 个服务健康，未起则启动。

### 9.3 环境变量（汇总）

```
# Flask 侧
AI_WORKSPACE_ROOT=/var/lib/check-manage/ai-workspaces
OPENCODE_BASE_URL=http://127.0.0.1:4096
MCP_SERVER_URL=http://127.0.0.1:3003
AI_SESSION_TTL_HOURS=24
AI_WORKSPACE_QUOTA_MB=200

# MCP 侧（共用 server 的 DB 配置）
DB_HOST / DB_NAME / DB_USER / DB_PASSWORD / DB_PORT

# OpenCode 侧
~/.config/opencode/config.json 配 provider/apiKey（从 ai_settings 表读出后落盘，或用 env 注入）
```

### 9.4 监控点

- Flask `/ai/chat/sessions/:id/events` 长连接数（控制并发上限）
- OpenCode 进程内存与 CPU
- MCP server 工具调用错误率
- Workspace 总磁盘占用（接近 quota * user_count 时告警）

## 10. 路线（实施顺序建议）

**第一里程碑（最小可用闭环）**：DB schema → MCP server 骨架（仅 `list_collections`）→ Flask `/ai/chat/sessions` 建会话 + SSE 透传 → 最小 AiChatDrawer（无文件面板）→ 单 Playwright 冒烟通过

**第二里程碑（业务数据问答）**：`query_dynamic_data` / `search_knowledge_base` MCP 工具 → ChatInput 项目上下文切换 → 工具调用气泡渲染

**第三里程碑（脚本开发主线）**：文件上传 + 工作区 + FilePanel → `generate_inspection_case` 工具 → 拖拽上传 + 自动注入提示 → 完整错误处理 12 项

实施计划由 writing-plans skill 在下一步细化。

## 11. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| OpenCode 单实例并发上限未知，可能成为瓶颈 | 第一里程碑后做一次并发压测；必要时改多实例 + 负载均衡（不在第一期范围） |
| 不同 LLM provider 的工具调用语义有差异 | 第一期固定一个 provider（Anthropic / Qwen 二选一），运行稳定后再扩 |
| `search_knowledge_base` ILIKE 在大数据量下性能差 | 已建 GIN 索引（`idx_dynamic_data_gin`）；超出后再考虑 PGroonga / pgvector |
| 长会话 token 上下文爆炸 | OpenCode 自带消息压缩；前端可加"开新会话"提示 |
| Agent 生成的脚本可能含敏感操作（rm/curl 外网） | 第一期不自动执行，仅提供下载；执行权限由人决定 |
