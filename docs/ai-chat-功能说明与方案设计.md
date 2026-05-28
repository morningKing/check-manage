# AI Chat 功能说明与方案设计

> 面向 check-manage 平台的「类 Claude」AI 助手。前端对接 **OpenCode** agent 运行时,通过独立 **MCP server** 打通平台数据,服务于巡检用例脚本开发、自测试与业务数据问答。
>
> 本文档描述当前已落地并经实时浏览器(Playwright)+ 真实 OpenCode 验证的功能与设计。文末列出已知边界与后续计划。

---

## 第一部分 · 功能使用说明

### 1. 这是什么

一个**全屏对话页**(顶栏「AI 助手」进入 `/ai-chat`)。它不是直连大模型,而是对接一个具备工具调用/技能的 **agent 运行时(OpenCode)**:

- 多轮对话、**流式** Markdown 答复,展示**思考过程**;
- 通过 MCP 工具读取本平台业务数据(按角色做权限过滤)回答问题;
- **文件上传**:上传文件让 agent 读取;
- **制品(Artifact)**:把脚本/文档整理成文件,在对话里以卡片呈现,支持**预览、版本切换、复制、下载**,HTML/SVG 可**实时渲染**;
- **多会话**:左侧会话列表,可新建/切换/重命名/删除,刷新后保留。

当前模型:**MiMo(`mimo-v2.5`)**,通过 OpenCode 的自定义 provider 接入。

### 2. 界面与操作

- **左侧会话栏**:`新建会话`;每个会话悬停出现「重命名 / 删除」图标;点击切换,各自加载自己的历史。
- **中间消息区**(居中文档列,约 780px):
  - 用户消息:右侧灰色圆角块;助手消息:无框、整宽、文档式 Markdown。
  - **思考过程**:可折叠的「思考完成」面板(reasoning),完成后自动收起。
  - **工具调用**:折叠卡片,显示「调用工具 · 工具名」+ 状态(运行中/完成/出错)+ 输入/结果。
  - **制品卡片**:显示文件名、语言、行数、「共 N 版」;按钮「预览 / 复制 / 下载」;Python 制品额外有「运行」按钮——用户手动触发在会话工作区执行,产出的结果文件出现在「产出文件」(确定性、不依赖模型;自动剥除模型常见的"首行文件名"瑕疵)。
  - **产出文件**:agent 写入 `outputs/` 的真实文件,列在"产出文件"区,可直接下载。
- **底部输入框**(Claude 风格统一圆角卡片):
  - 无边框自适应输入(1–8 行),聚焦时整卡出现蓝色光环;
  - 左下 `＋` 上传文件(附件 chip 显示在卡片顶部);右下「MiMo」+ 圆形发送按钮;
  - **Enter 发送,Shift+Enter 换行**。
- **制品预览抽屉**(右侧滑出):
  - 多版本时顶部有版本下拉(默认最新);
  - HTML/SVG 有「渲染 / 源码」切换,渲染用沙箱 iframe;其它语言为语法高亮源码;
  - 复制 / 下载。

### 3. 权限

| 角色 | 能否使用 |
|------|----------|
| admin | ✅ 完整;MCP 工具可见全部数据集 |
| developer | ✅ 对话、读取其有权限的数据集、上传、产出 |
| guest | ❌ 只读账号:创建会话 / 发消息 / 上传 / 删除均被拦截(403) |

助手通过 MCP 读业务数据时**按当前用户角色 RBAC**(如 `list_collections` 只返回该角色在菜单 `roles` 中可见的数据页;admin 全可见)。

### 4. 运行前置条件(运维/部署)

依赖四个进程在线:

| 进程 | 默认地址 | 说明 |
|------|----------|------|
| Flask 后端 | `:3002` | 网关 + SSE 透传 + 文件/会话 |
| OpenCode | `opencode serve` `:4096` | agent 运行时(共享单实例) |
| MCP server | `mcp-server/` `:3003` | 独立服务,暴露平台工具 |
| PostgreSQL | `:5432` | 会话、消息、业务数据 |

并且:
- 已执行 `cd server && python init_db.py`(创建 `ai_chat_sessions` / `ai_chat_messages` 等表;默认管理员需 `INIT_ADMIN_PASSWORD`)。
- OpenCode 全局配置(`~/.config/opencode/opencode.json`)中已配置 **MiMo provider**(含 endpoint 与 API key)。**密钥只在 OpenCode 自己的配置里,不进本仓库。**

### 5. 常见问题

- **不回复 / 一直转圈**:确认 `opencode serve` 在线、MiMo provider 配置正确、API key 有效。
- **"拒绝访问"**:当前账号是 guest,无写权限。
- **助手看不到某数据页**:该数据页菜单 `roles` 不含你的角色(admin 例外)。
- **上传的文件 agent "看不到"**:目前可靠路径是上传后随消息发送(服务端会把文本内容内联给 agent);二进制/超大文件不内联(见 §设计·限制)。
- **浏览器流式无响应但接口正常**:确认 EventSource 带上了 `?access_token=`(见设计 §4.5)。

---

## 第二部分 · 方案设计文档

### 1. 背景与目标

- **核心目标**:巡检用例脚本开发与自测试 —— 根据方案产出 Python 脚本,集成知识库搜索、用例开发等 skill。
- **次要目标**:打通平台数据,回答业务数据问题。
- **理念**:**「加 skill 即获得能力,前端零修改」** —— 能力下沉到 OpenCode,前端用通用工具调用渲染。

### 2. 系统架构

```
浏览器(Vue 全屏页 /ai-chat)
   │  REST + SSE（仅与 Flask 通信）
   ▼
Flask 后端(:3002)  ── 网关 / SSE 透传 / 会话·文件·令牌管理
   │  HTTP + SSE
   ▼
OpenCode(:4096, 共享单实例)  ── agent 运行时 / 工具调用 / 模型(MiMo)
   │  MCP (Streamable-HTTP, 带会话令牌)
   ▼
MCP server(:3003, 独立服务)  ── 平台工具 + RBAC
   │  SQL
   ▼
PostgreSQL(:5432)
```

要点:
- **浏览器只与 Flask 通信**,绝不直连 OpenCode/MCP。
- **OpenCode 共享单实例**(P2 决策):业务人员无需本地安装。
- **MCP 独立于 Flask**:单独进程、单独依赖。
- **每会话隔离**:独立 workspace 目录 + 独立 `opencode.json` + 独立 opaque 令牌。

### 3. 组件清单

#### 3.1 数据库(2 张表)
- `ai_chat_sessions`:`id`、`user_id`(FK)、`title`、`opencode_session_id`、`workspace_path`、`session_token`(唯一,opaque)、`token_expires_at`、`project_menu_id`、`status`(active/revoked/deleted…)。
- `ai_chat_messages`:`id`、`session_id`(FK)、`role`(user/assistant)、`content`(JSONB,typed parts:text/file/tool_use)、`created_at`。

#### 3.2 Flask 后端(blueprint `ai_chat_bp`,前缀 `/ai/chat`)

| 方法 | 路由 | 鉴权 | 作用 |
|------|------|------|------|
| POST | `/sessions` | write_required | 建会话:建 workspace、生成令牌、写 `opencode.json`、`POST /session?directory=` |
| GET | `/sessions` | login_required | 会话列表(侧栏) |
| PATCH | `/sessions/:id` | write_required | 重命名 |
| DELETE | `/sessions/:id` | write_required | 删 OpenCode 会话、吊销令牌、标记删除、尽力清理目录 |
| GET | `/sessions/:id/messages` | login_required | 历史消息(`?since=`) |
| POST | `/sessions/:id/messages` | write_required | 落库用户消息 + `prompt_async`(带模型),附件文本内联进 prompt |
| GET | `/sessions/:id/events` | login_required_sse | SSE 透传 OpenCode 事件,落库 assistant 消息 |
| POST | `/sessions/:id/files` | write_required | 上传文件到 `uploads/` |
| GET | `/sessions/:id/files` | login_required | 列出 `uploads/`+`outputs/` |
| GET | `/sessions/:id/files/download` | login_required_sse | 下载(`?path=`) |
| POST | `/sessions/:id/run` | write_required | 用户手动运行脚本(Python),在会话工作区执行,产出文件落 `outputs/` |

工具模块:
- `server/utils/opencode_client.py`:OpenCode HTTP/SSE 封装(§4)。
- `server/utils/workspace.py`:每会话目录、`write_opencode_config`、`safe_resolve` 路径穿越防护。
- `server/utils/session_token.py`:opaque 令牌 `generate/renew/revoke`。
- `server/auth.py`:`login_required_sse`(Bearer 或 `?access_token=`)。
- `server/config.py`:环境变量(§6)。

#### 3.3 MCP server(`mcp-server/`,独立进程)
- `main.py`:FastAPI + MCP `StreamableHTTPSessionManager`,`/health`、`/mcp`;`TokenMiddleware` 从 `?token=` 取令牌,401 拦截无效/缺失;`_resolve_context()` 由令牌得 `ToolContext`。
- `auth.py`:`validate_session_token`(查 `ai_chat_sessions JOIN users`,校验 active+未过期)。
- `context.py`:`ToolContext(session_id, user_id, role)`。
- `tools/__init__.py`:**集中注册表**(MCP 只允许一个 list_tools/call_tool,按名分发)。
- 工具:
  - `list_collections` — 当前角色可见的数据集(RBAC)。
  - `save_artifact(filename, content)` — 写入会话 `outputs/`,用户可下载。
  - `read_upload(filename?)` — 读会话 `uploads/`(无参列出文件),会话作用域、限 `uploads/`、200KB、二进制/超大返回错误。
  - `export_collection_excel(collection)` — 把某集合的**真实数据**导出为 `.xlsx` 写入 `outputs/`(RBAC、字段标签作表头),产出**结果文件**而非脚本。
  - `run_python(code)` — 在会话工作目录执行 Python(超时、cwd 限会话目录、guest 禁用),返回 stdout + 写入 `outputs/` 的结果文件;非强沙箱(见限制)。
- 新增工具只需实现 `NAME/TOOL/handle` 并在注册表挂上,**前端无需改动**。

#### 3.5 导出意图后端兜底(不依赖模型)
鉴于 MiMo 常不主动调 MCP 工具,`send_message` 内置**导出意图兜底**:`utils/data_export.py` 的 `is_export_intent` + `resolve_collection_from_text` 命中(如"把巡检用例数据导出成 excel")时,Flask **确定性地**用真实数据生成 `.xlsx` 写入会话 `outputs/`,并在 prompt 里告知 agent。无论模型是否调用工具,用户都能在「产出文件」拿到真实结果文件。

#### 3.4 前端(Vue 3)
- `src/views/ai-chat/AiChatView.vue`:全屏页(会话栏 + 消息区 + 统一圆角输入卡 + 预览抽屉)。
- `src/components/ai-chat/`:`MarkdownView`(md-editor-v3,透明背景、不折叠代码)、`ToolCallBubble`(工具调用卡)、`ArtifactCard`(制品卡)、`ArtifactPreview`(预览+版本+沙箱渲染)。
- `src/stores/aiChat.ts`:多会话状态、流式 part 累积(按 part id 快照覆盖)、reasoning、附件、outputs。
- `src/api/aiChat.ts`:REST 封装 + `createEventStream`(EventSource,1s→2s→5s→10s 自动重连,token 走 query)。
- `src/utils/artifacts.ts`:制品提取(`splitArtifacts`)、语言推断(`sniffLang`)、文件名/扩展名、HTML/SVG 可渲染判定。

### 4. OpenCode 实测契约(v1.2.26)

> 均经对运行中的 OpenCode 实测确认。

#### 4.1 会话与目录绑定
- `POST /session?directory=<abs path>`,body `{title}` → `{id:"ses_…", directory}`。**目录用 query 参数绑定**(body cwd 被忽略)。
- `DELETE /session/{id}` → 200。

#### 4.2 发消息与模型
- `POST /session/{id}/prompt_async`,body `{parts:[{type:"text","text":…}], model:{providerID, modelID}}` → 204。
- **模型必须在 prompt 上显式传**:OpenCode 不采用 `opencode.json` 的 `model` 字段做 prompt 选择,缺省会回退默认模型。我们从 `OPENCODE_MODEL`(`mimo/mimo-v2.5`)拆出 `providerID/modelID`。

#### 4.3 事件流(SSE)
- 订阅 **全局 `/event`**(**不带** `?directory=`;带 directory 的流不投递会话事件)。
- 仅 `data:` 帧,事件名在 JSON `type`,会话键为 **`sessionID`**(可能嵌于 `properties` / `properties.part` / `properties.info`)。

| 事件 | 含义 | 处理 |
|------|------|------|
| `message.updated` | 消息元数据 | `info.role=='assistant'` → 记录 assistant 消息 id |
| `message.part.updated` | 流式片段(**全量快照,按 `part.id` 覆盖**) | `type=text`→正文;`type=reasoning`→思考;`type=tool`→工具卡 |
| `session.idle` | 一轮结束 | 落库 assistant 文本、关流式、刷新 outputs |
| `session.error` | 出错 | 关流式 |

> MiMo 是推理模型,先发 `reasoning` 再发 `text`;工具调用为 `type=tool` 的 part(`{tool, state:{status,input,output}}`)。

#### 4.4 MCP 注册(每会话令牌)
OpenCode 无每会话 MCP API,只按目录读配置。每会话 workspace 写 `opencode.json`:
```json
{ "$schema": "https://opencode.ai/config.json",
  "model": "mimo/mimo-v2.5",
  "mcp": { "check-manage": { "type": "remote",
    "url": "http://127.0.0.1:3003/mcp?token=<session_token>", "enabled": true } } }
```
以 `?directory=<workspace>` 创建会话后,OpenCode 按目录加载并带令牌连接 MCP(实测 `GET /mcp?directory=` 状态 `connected`)。

#### 4.5 浏览器 EventSource / 下载鉴权
浏览器 `EventSource` 无法设请求头,故 SSE 与下载路由用 `login_required_sse`:除 `Authorization: Bearer` 外接受 `?access_token=<JWT>`;前端从 localStorage 取 token 追加到 URL。

#### 4.6 文件工具 cwd(重要)
OpenCode 的内置文件工具(`read`/`glob`/`bash`)以 **`opencode serve` 的启动目录(仓库根)** 为 cwd,而非会话 `directory`。因此 **agent 无法用内置工具按工作区相对路径定位上传文件**。访问上传文件靠:① 服务端内联;② `read_upload` MCP 工具。

### 5. 关键数据流

- **建会话**:建 workspace(仓库外)→ 落库 → 生成令牌 → 写 `opencode.json`(令牌 MCP url + 模型)→ `POST /session?directory=` → 回填 `opencode_session_id`。
- **发消息+流式**:落库 user 消息(含附件 chip)→ 附件文本内联进 prompt → `prompt_async(parts, model)` → 前端 `EventSource` 收全局事件、按 sessionID 过滤 → store 按 part id 覆盖累积(text/reasoning/tool)→ `session.idle` 落库 assistant 文本 + 刷新 outputs。
- **身份链路**:Flask 生成令牌 → 写入 workspace 的 `opencode.json` → OpenCode 带令牌连 MCP → MCP 查表得 `user_id/role` → 工具 RBAC。
- **制品**:助手回复里的大代码/文档块由前端提取成制品卡(同名跨消息归组为多版本);HTML/SVG 在沙箱 iframe 渲染。

### 6. 环境变量

```
# Flask 侧（server/config.py）
AI_WORKSPACE_ROOT      每会话工作目录根（默认 ~/.check-manage/ai-workspaces，仓库外）
OPENCODE_BASE_URL      OpenCode 地址（默认 http://127.0.0.1:4096）
MCP_SERVER_URL         MCP server 地址（默认 http://127.0.0.1:3003）
AI_SESSION_TTL_HOURS   令牌有效期小时数（默认 24）
AI_WORKSPACE_QUOTA_MB  工作区配额（默认 200，未强制）
OPENCODE_MODEL         模型 "<providerID>/<modelID>"（默认 mimo/mimo-v2.5）

# MCP 侧（mcp-server/db.py，复用同一套 DB 配置）
DB_HOST / DB_NAME / DB_USER / DB_PASSWORD / DB_PORT

# OpenCode 侧（~/.config/opencode/opencode.json，不进仓库）
provider.mimo.options.{baseURL, apiKey} + provider.mimo.models
```

MiMo provider 配置样例:
```json
"mimo": {
  "name": "MiMo",
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "https://token-plan-cn.xiaomimimo.com/v1", "apiKey": "<KEY>" },
  "models": { "mimo-v2.5": { "name": "MiMo v2.5" } }
}
```

### 7. 安全与隔离

- **令牌**:opaque、每会话唯一、可吊销、有 TTL。删除会话**先吊销令牌+标记删除,再尽力清理目录**(避免文件锁导致活跃会话残留)。
- **RBAC**:MCP 工具按令牌反查的用户角色过滤数据。
- **API 授权**:所有会话操作过 `_load_session_for_user(sid, user_id)`(`WHERE id AND user_id`),跨用户访问返回 404。
- **路径穿越防护**:`safe_resolve`(`Path.resolve()`+`relative_to`,`os.path.isabs` 兼容 Windows)、`secure_filename`、`basename`、`commonpath`。
- **工作区隔离**:`~/.check-manage/ai-workspaces/<user_id>/<session_id>/`,**在仓库外**;因 agent 内置文件工具 cwd 是仓库根,工作区放在仓库外后,**agent 内置工具扫不到任何会话的上传**。访问上传只能经 `read_upload`(会话作用域)或服务端内联。
- **密钥**:模型 provider 密钥仅存 OpenCode 全局配置,不进 git。

### 8. 测试

- **前端 Vitest**:`src/api/__tests__/aiChat`(REST + EventSource 重连)、`src/stores/__tests__/aiChat`(多会话、流式 part 快照、tool_use、reasoning、outputs)、`src/utils/__tests__/artifacts`(提取/语言推断/扩展名/可渲染)。
- **后端 Pytest**:`tests/test_routes_ai_chat`(会话 CRUD、消息、SSE 词表+鉴权、上传/列举/下载+路径安全+归属)、`test_workspace`、`test_opencode_client`、`test_ai_session_token`。
- **MCP Pytest**:`test_list_collections`、`test_save_artifact`、`test_read_upload`;`test_integration_db`(opt-in,`RUN_DB_INTEGRATION=1`,真实库,验证令牌+RBAC)。
- **E2E**:`e2e/ai-chat-smoke.spec.ts`(`npm run test:e2e`,需 OpenCode+MCP);并经 Playwright MCP 实时驱动浏览器联调(登录、多会话切换/重命名/删除、上传读取、制品预览/版本切换/SVG 渲染、布局)。

### 9. 已知边界与后续

| 已落地 | 后续 |
|--------|------|
| 全屏页、多会话、流式文本/思考/工具卡 | 多模型可切换(下拉)、语音输入 |
| 制品卡 + 预览 + 版本 + HTML/SVG 沙箱渲染 + 语言推断 | 制品**实时执行**(Claude 那种 React/HTML 交互沙箱)、可编辑工作区 |
| MCP:list_collections / save_artifact / read_upload | 知识库搜索、巡检用例开发等 skill |
| 上传文本内联 + read_upload 工具 | 二进制/大文件处理、视觉模型读图 |

**重要限制(诚实说明)**:
- **MiMo 工具调用不可靠**:实测它常绕过 MCP 工具(`list_collections`/`save_artifact`/`read_upload`)改用内置工具,甚至误报工具不可用。因此**服务端内联是访问上传文件的可靠默认路径**,MCP 工具是"模型配合时"的补充。换工具调用更强的模型可改善。
- **内联限制**:仅文本、200KB 上限;二进制(图片/xmind/pdf)不内联。
- **共享 OpenCode 进程**:cwd=仓库根,agent 内置 `read`/`bash` 理论上仍能读仓库源码(用户上传数据已隔离出仓库)。彻底消除需容器/每会话进程沙箱。
- **模型固定**:当前单模型 MiMo,界面模型名为静态展示。
