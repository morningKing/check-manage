# 10 · 管理配置与外部 MCP

> 上游：[00-总览与功能清单](./00-总览与功能清单.md) · [01-总体架构与运行时](./01-总体架构与运行时.md) · [02-对话助手](./02-对话助手.md) · [08-Skill与SkillOpt](./08-Skill与SkillOpt.md) · [09-长期记忆](./09-长期记忆.md)
> 边界声明：本册写 AI 管理配置面——`ai_settings` 单行配置本体与 `/ai/settings` 读写、外部 MCP 注册（`ai_mcp_servers` CRUD）、内置 MCP 开关与健康探测、设置中心 AI 四域入口与旧路径重定向、OpenCode 运行时治理权限拆分迁移。**运行时面**（`opencode.json` 如何被会话消费、per-session MCP 身份、三进程拓扑、MCP Server 3003 的工具面与 token 鉴权）**以分册 01 §4.1/§5 为准**，本册只写管理端如何「贡献」配置；`POST /ai/query` 与 NL 建页的执行语义**归分册 02**（02 册 §4.7/§4.8），本册只写它们共用的 `ai_settings` 配置通道；`/ai/memories` 用户面三端点与 mem0 消费细节**归分册 09**（09 册 §4.4）；OpenCode 运行时治理端点族（`/ai/opencode/*`）的端点表**归分册 08 §5.2**，本册只写其权限拆分迁移与前端入口挂载；AI 定时巡检（`ai-scan`）**归分册 07**；批任务/会话审计/编排管理的功能面分别**归分册 03/06/04**，本册只写它们在设置中心的入口合并。行号以本 worktree 实际代码为准。

## 1. 模块职责

本册覆盖平台的 **AI 能力管理配置面**，回答三个问题：**AI 用什么模型与密钥**（`ai_settings` 单行配置，一张卡读写）、**AI 会话能连哪些 MCP 服务**（内置 `check-manage` MCP 的开关与健康探测 + 管理员注册的外部 MCP）、**谁能管这些**（`admin.ai_settings` 与拆分出的 `admin.ai_runtime_*` 八键 RBAC，前端设置中心按权限裁剪入口）。配置的**消费面横贯全部 AI 分册**：NL 查询（02）、NL 建页（02）、动作门禁提炼器（05）、SkillOpt 拟合 AI 层（08）、长期记忆 mem0/嵌入（09）、OpenCode 会话默认模型（01/02）都从同一行配置读参——「一处配置、处处生效」是本册存在的核心理由。

按职责分四块：

| 职责 | 说明 | 证据 |
| --- | --- | --- |
| AI 设置读写 | `GET/PUT /ai/settings`：单行配置的读取（API Key 掩码）与全量更新（校验 + 掩码回传识别 + 记忆单例重置） | `server/routes/ai.py:68-142`；`server/utils/ai_query.py:61-129` |
| MCP 服务管理 | 内置 MCP（`check-manage`，:3003）的开关与 `/health` 探测；外部 MCP（remote/local）CRUD；两者合并进每个会话 `opencode.json` 的 `mcp` 映射 | `server/routes/ai.py:184-288`；`server/utils/mcp_servers.py`（171 行）；合并点 `server/utils/workspace.py:288-323` |
| 设置中心 AI 四域入口 | `settingsCatalog.ts` 唯一真源派生侧边栏/路由/权限门；AI 域四个条目页签化合并五张旧页面；旧路径两张重定向表 | `src/views/admin/hub/settingsCatalog.ts`（184 行）；`src/router/settingsRoutes.ts`（90 行）；`hub/SettingsTabShell.vue` |
| 运行时权限拆分 | 原 `admin.ai_settings` 一把抓拆出 8 个 `admin.ai_runtime_*` 细粒度键；历史角色授权幂等平移 | `server/migrations/2026_09_15_split_ai_runtime_permissions.py`（60 行）；`server/utils/permissions.py:17-29` |

## 2. 模块组成

### 2.1 后端

| 组件 | 职责 | 证据 |
| --- | --- | --- |
| `routes/ai.py` | 蓝图 `ai`（前缀 `/ai`，`ai.py:24`；注册 `app.py:116`）。本册认领：`/ai/settings` 读写 2 端点 + `/ai/mcp-servers` 族 6 端点（全部 `admin.ai_settings`）；同文件其余端点外归：`POST /ai/query` → 02 册、`/ai/memories` 三端点 → 09 册 | `server/routes/ai.py`（288 行） |
| `utils/ai_query.py` | **AI 配置通道本体**：`get_ai_settings`（snake_case 行 → camelCase dict，缺行回落内置默认）、`update_ai_settings`（全列 UPDATE + 回读）、共享连接池 `get_http_session`（Retry 2 次/429·5xx，仅 POST）；NL 翻译 `nl_to_mongo_filter` 挂在此文件但归 02 册 | `server/utils/ai_query.py:35-54,61-129`；翻译 `:186-273` |
| `utils/mcp_servers.py` | 外部 MCP CRUD（remote/local 归一化校验、name 唯一）+ `enabled_mcp_config`（启用项 → OpenCode `mcp` 配置映射，预留名防遮蔽）+ 内置 MCP 开关读写（`ai_settings.mcp_internal_enabled`） | `server/utils/mcp_servers.py:1-7`（模块头）、`:22-47,50-119,122-140,143-171` |
| `migrations/2026_09_15_split_ai_runtime_permissions.py` | 幂等迁移：持有 `admin.ai_settings` 的角色平移授予 8 个新键（NOT EXISTS 防重），平移后 `invalidate_cache()` 立即生效 | `2026_09_15_split_ai_runtime_permissions.py:22-31,34-56` |
| `utils/permissions.py` | 权限目录真源：`admin.ai_settings`（`:17`）+ 拆分注释（`:18-21`）+ 8 个运行时键（`:22-29`）+ 本册前端引用的 `ai_scan`（`:34`）/`ai_chat_admin`（`:47`）/`ai_orchestration_admin`（`:49`）；`invalidate_cache`（`:122`） | `server/utils/permissions.py:11-49` |
| `db_schema/rbac.py` | `role_permissions(role_id, permission_key)` 主键表——权限授予的唯一存储 | `server/db_schema/rbac.py:16-20` |

### 2.2 前端

| 组件 | 职责 | 证据 |
| --- | --- | --- |
| `views/admin/hub/settingsCatalog.ts` | 设置中心**唯一真源**：分组/条目/页签三级结构，侧边栏渲染（`SettingsSideMenu.vue:50,57` `filterGroups`）、路由生成（`settingsRoutes.ts:20-31`）、权限判定（`canItem` any-of 语义 `:140-142`）全部从此派生；两张旧路径重定向表 | `settingsCatalog.ts:1-8`（头注释）、`:43-125`（六组）、`:128-158`（派生函数）、`:166-184`（重定向表） |
| `router/settingsRoutes.ts` | 22 条功能路由 + 四类兼容重定向（`/admin` 首个有权限条目、分组 id 带 `?tab=`、AI 旧条目 → 域入口 tab、历史别名 → 新条目） | `settingsRoutes.ts:20-31,43-90` |
| `hub/SettingsTabShell.vue` | 域入口页签壳：按 `auth.can(t.perm)` 过滤页签、`?tab=` 深链双向同步（replace 不留历史）、`defineAsyncComponent` 恒定包装 + keep-alive 防切签重挂载 | `SettingsTabShell.vue:24,26-29,31-43` |
| `hub/AiConfigHub.vue` | 「AI 配置」域入口壳：`<SettingsTabShell item-id="ai-settings" />`（模型与密钥 + 运行时两页签）；同构壳还有 `AiSkillHub.vue`（技能域）、`AiExecutionHub.vue`（执行中心域） | `AiConfigHub.vue:1-6` |
| `components/admin/McpServersCard.vue` | MCP 服务管理卡：内置 MCP 行（开关 + 检测连接 + 延迟徽标）+ 外部 MCP 表格（增删改查、启停 switch、remote/local 双表单） | `McpServersCard.vue`（330 行） |
| `views/admin/AiSettings.vue` | 「模型与密钥」页：`GET/PUT /ai/settings` 表单（`:218,:252`），模型下拉数据来自 `GET /ai/chat/models`（`api/aiChat.ts:285` `listModels`，端点本身归 02 册） | `AiSettings.vue:218,242,252` |
| `api/aiMcpServers.ts` | MCP 前端 API 模块：6 函数对应后端 6 端点 | `aiMcpServers.ts:16,21,26,31,50,55` |

## 3. 数据模型

### 3.1 `ai_settings` —— 全平台 AI 能力的单行配置

`server/db_schema/core.py:155-168`（CREATE）+ `:172-180`（幂等补列）+ 迁移 `2026_09_29_embedding_provider.py:23-25`（嵌入提供方三列）。**单行表**：`id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1)`，建表即 `INSERT id=1`（`core.py:156,168`）——不存在「多套配置」，读写都落同一行。

| 列 | 类型/默认 | 语义 | 消费方 |
| --- | --- | --- | --- |
| `enabled` | BOOL FALSE | AI 能力总开关（NL 查询/建页/拟合/门禁提炼以它为闸门） | `ai_query.py:210-211`、`ai_schema_designer.py:200-201`、`skill_fit_ai.py:76-77`、`action_check_extractor.py:93-95` |
| `api_key` | VARCHAR(500) '' | OpenAI 兼容 API Key（**明文入库，出接口必打码**，见 §4.1/§7.1） | 全部 LLM 通道 `Authorization: Bearer` |
| `endpoint` | VARCHAR(1000) 默认 dashscope compatible-mode | OpenAI 兼容补全端点 | 同上（mem0 的 llm+api embedder 也经 `_base_url` 推导） |
| `model` | VARCHAR(200) 'qwen-plus' | 补全模型（NL 查询/建页/拟合/门禁/mem0 摘要共用） | 同上 |
| `timeout` / `max_tokens` | INT 30 / INT 1024 | 请求超时与生成长度上限 | 各通道 payload |
| `mem0_enabled` | BOOL FALSE（`core.py:172`） | 长期记忆开关 | `memory.py:100-103`（09 册） |
| `embedding_model` | VARCHAR(200) 'text-embedding-v3'（`core.py:173`） | 嵌入模型 | `memory.py:_build_config :65-80` |
| `default_chat_model` | VARCHAR(200) ''（`core.py:174`） | OpenCode 会话默认模型（`<providerID>/<modelID>`），优先于 env | `config.py:134-150` `get_default_chat_model` → `ai_chat.py:149` 写入 opencode.json `model` 键 |
| `mcp_internal_enabled` | BOOL TRUE（`core.py:177`） | **内置 MCP 开关**：FALSE 时新建/重置会话的 opencode.json 不写平台条目（外部条目照写） | `mcp_servers.py:143-157` → `workspace.py:292-300` |
| `max_batch_sessions` | INT 50（`core.py:180`） | 批任务子会话个数上限（每输入文件一个子会话） | `batch_repo.py:199-209` `get_max_files_per_batch`（缺省/非法回落 50，03 册） |
| `embedding_provider` / `embedding_ollama_url` / `embedding_dims` | 'api' / 'http://localhost:11434' / 1024（迁移 `2026_09_29:23-25`） | 嵌入提供方分支：api=原端点（openai 兼容）/ ollama=本地服务；仅影响 mem0 embedder，摘要 LLM 恒走原端点 | `memory.py:62-74`、`ai.py:110-120`（校验） |

### 3.2 `ai_mcp_servers` —— 管理员注册的外部 MCP

`server/db_schema/core.py:186-197`（模块头注释 `:182-185` 写明用途）。

| 列 | 类型/约束 | 语义 |
| --- | --- | --- |
| `id` | VARCHAR(64) PK | `uuid4` 字符串（`mcp_servers.py:69`） |
| `name` | VARCHAR(100) NOT NULL **UNIQUE** | **即 opencode.json `mcp` 映射的 key**——重名在应用层拦截（create `:73-75` / update 排除自身 `:96-99`），DB 层 UNIQUE 兜底 |
| `type` | VARCHAR(20) DEFAULT 'remote' | `remote`（url+headers）/ `local`（command argv+environment），合法值白名单 `VALID_TYPES`（`mcp_servers.py:19`） |
| `url` | VARCHAR(1000) DEFAULT '' | remote 必填（`mcp_servers.py:37-38`） |
| `command` | JSONB `'[]'` | local 的 argv 列表，必须含非空项（`:39-40`） |
| `headers` / `environment` | JSONB `'{}'` | remote 请求头 / local 环境变量，归一化为 string→string（`:44-45`） |
| `enabled` | BOOL DEFAULT TRUE | 行级开关：禁用项不进 `enabled_mcp_config`（`:129`） |
| `created_at` / `updated_at` | TIMESTAMPTZ | — |

### 3.3 RBAC 三表（本册相关部分）

`roles` / `role_permissions`（`db_schema/rbac.py:6-20`）/ `role_page_permissions`（`:22-32`）。权限键的**目录真源**在代码 `utils/permissions.py PERMISSION_CATALOG`（角色管理页渲染为开关），授予存储在 `role_permissions`；JWT 只带角色 slug，每次请求按角色查表解析权限集（进程内缓存，改角色即时生效无需重登，`permissions.py:1-9`）。

## 4. 核心流程

### 4.1 AI 设置读写与 API Key 打码闭环

```
GET /ai/settings ──► get_ai_settings() ──► 打码（前 n-4 位→'*'，留末 4 位）──► 前端表单
PUT /ai/settings ──► 校验（endpoint/model 必填、timeout/maxTokens/maxBatchSessions/
                     embeddingProvider/ollamaUrl/embeddingDims 逐项）
                     ──► 掩码识别：提交值去掉末 4 位后全为 '*' ⇒ 视为未修改，回读库中旧值
                     ──► update_ai_settings() 全列 UPDATE ──► reset_memory_singleton()
                     ──► 返回前再打码 ──► log_operation（「API Key 明文不记录」）
```

- **打码规则**：仅当 `len(key) > 4` 才掩码（`ai.py:74-76,136-138`）——短于等于 4 字符的 Key 会原样返回（事实记录，见 §7.6）。
- **掩码回传识别**（`ai.py:122-125`）：`set(api_key[:-4]) == {'*'} ⇒ api_key = current['apiKey']`。前端不回传明文也能「其余字段照改、密钥不动」；反之用户粘贴 `****...abcd` 形态的真 Key 会被误判为未修改（设计上接受，见 §7.1）。
- **配置变更的涟漪**：PUT 成功即 `reset_memory_singleton()`（`ai.py:134` → `memory.py:113-118`），mem0 单例下次调用按新配置重建（09 册 §4.1）；其余 LLM 通道无缓存、下次请求自然读到新值。
- **审计**：`log_operation('update','ai_settings',…)` 记录 enabled/model/endpoint/mem0/批上限等非敏感字段并显式注明密钥不入日志（`ai.py:139-141`）。

### 4.2 外部 MCP 注册与 opencode.json 贡献

```
管理员在 McpServersCard 表单提交（name/type/url|command/headers|environment/enabled）
  ──► POST/PUT /ai/mcp-servers ──► _mcp_payload（ai.py:188-197）
  ──► mcp_servers._normalize：type 白名单、remote 必有 url、local 必有非空 command、
      headers/environment 归一化为字符串字典（mcp_servers.py:22-47）
  ──► name 唯一校验（create 全表 / update 排除自身）──► INSERT/UPDATE ai_mcp_servers
  ──► log_operation('create'/'update','ai_mcp_server',…)

会话创建/清空重置/轨迹分析建分析会话时（运行时面归 01 册 §4.1，此处只列贡献点）：
  enabled_mcp_config(reserved_names=['check-manage'])          # ai_chat.py:85（create/clear 共用）/ ai_session_admin.py:287
    跳过禁用项与预留名（防遮蔽平台条目，mcp_servers.py:126-130）
    remote → {type:'remote', url, enabled:true, headers?}
    local  → {type:'local', command, enabled:true, environment?}
  write_opencode_config(..., extra_mcp=…, include_internal=…)  # workspace.py:288-323
    三处生产调用点：ai_chat.py:149-150（create_session 建会话）、
                    ai_chat.py:2127-2129（clear_session 清空/重置——同轴生效路径）、
                    ai_session_admin.py:288-292（轨迹分析分析会话）
    平台条目恒胜出（workspace.py:313 二次防遮蔽）
```

**生效边界：只对新建/清空（重置）的会话生效，已有工作区不回改**（`workspace.py:300-301`；前端提示 `McpServersCard.vue:15-17`）。外部注册**不校验目标可达性**——坏配置只会在会话内表现为工具缺失，管理端不提供外部 MCP 的健康探测（内置 MCP 才有，见 §4.3）。`ai_chat.py:_external_mcp` 对 DB 异常 best-effort 返回 `{}` 不阻断建会话（`ai_chat.py:81-86`）。

### 4.3 内置 MCP：开关语义与健康探测

- **开关**（`PUT /mcp-servers/internal`，`ai.py:235-248`）：写 `ai_settings.mcp_internal_enabled`（`mcp_servers.py:160-171`）。语义是「**之后的**新建/重置会话的 opencode.json 是否写平台条目」，不改任何既有工作区；`internal_mcp_enabled()` 查库异常时**回落 True（fail-open）**——内置 MCP 支撑数据查询/记忆/轨迹分析，宁可多写不可静默断供（`mcp_servers.py:147-157` docstring 明言）。前端禁用时弹确认框并说明后果（`McpServersCard.vue:144-161`）。
- **路由注册顺序**：`/mcp-servers/internal` 与 `/mcp-servers/internal/health` 刻意注册在 `/mcp-servers/<server_id>` 规则**之前**，否则 `'internal'` 会被当成 id 捕获（`ai.py:238-241` docstring）。
- **健康探测**（`GET /mcp-servers/internal/health`，`ai.py:215-232`）：服务端 `urllib` 直探 `{MCP_SERVER_URL}/health`（3s 超时），`ok = status==200 且 body 含 'ok'`，返回 `latencyMs`；网络层异常返回 502 带错误文本。**动机**（docstring :218-220）：一次轨迹分析事故中 MCP 已宕而会话持续失败，配置面却毫无信号——此端点即管理面的「盲区补丁」。消费侧两处印证：管理卡「检测连接」按钮（`McpServersCard.vue:132-142`，502 也渲染为不可达徽标）；轨迹分析建分析会话前的同型 pre-flight 探测 + 内置开关禁用时 409 拒跑（`ai_session_admin.py:180-199`——分析 agent 建立在 `analyze_trace`/`query_sessions` 内置工具上，禁用即拒绝，不静默降级）。
- **管理视图**（`GET /mcp-servers`，`ai.py:200-212`）：外部表行 + 内置伪行 `{name:'check-manage', url, enabled}` 同卡展示（内置不是 `ai_mcp_servers` 的行，是 `ai_settings` 的开关列 + `config.MCP_SERVER_URL`）。

### 4.4 `ai_settings` 配置通道：一处配置、八处消费

| 消费方 | 读什么 | 分册归属 |
| --- | --- | --- |
| `ai_query.nl_to_mongo_filter`（NL 查询） | enabled 闸门 + apiKey/endpoint/model/timeout/maxTokens（`ai_query.py:208-215,220-237`） | 02（§4.7） |
| `ai_schema_designer`（NL 建页草案） | 同通道：`:19` 复用 import；`cfg :198`、enabled 闸门 `:200-201`、Key 校验 `:203-205` | 02（§4.8） |
| `skill_fit_ai._llm_json`（拟合 AI 层） | 同通道 + 共享 `get_http_session`；失败 RuntimeError → 路由层 502 | 08（§4.4） |
| `action_check_extractor`（动作门禁提炼器） | `:16,:93` 同通道 | 05 |
| `memory`（mem0 单例与嵌入） | `mem0_enabled`+`apiKey` 闸门（`:100-103`）；`_build_config` 按 `embedding_provider` 分支 api/ollama embedder，摘要 llm 恒走原端点（`:60-88`） | 09（§4.1） |
| `config.get_default_chat_model` | `default_chat_model` 优先，回落 `OPENCODE_MODEL` env，再回落空（OC 自选） | 01（§4.1）/02 |
| `kefu_repo`（客服会话建会话） | `write_opencode_config(include_internal=internal_mcp_enabled())` + `model=instance.get('model') or get_default_chat_model()`；**不传 `extra_mcp`**——客服会话不并外部 MCP（`kefu_repo.py:204-210`；蓝图仍注册 `app.py:525-526`，前端入口已裁剪，`settingsCatalog.ts:65-67` 注释） | —（客服已裁剪入口） |
| `batch_repo.get_max_files_per_batch` | `max_batch_sessions`，缺省/非法回落 50 | 03 |

八处消费全部**只读**——写入口只有 `PUT /ai/settings` 一处（外加内置开关列经 `set_internal_mcp_enabled`），无「各功能单独配模型」的旁路。

### 4.5 设置中心：AI 四域入口的页签化合并

`settingsCatalog.ts` 三级结构：`SettingsGroup`（视觉分组，不可点不路由，`:34-41`）→ `SettingsItem`（= 路由 `/admin/<id>`，`perm: string|string[]` **any-of**）→ `SettingsTab[]`（页内页签，各有独立 `perm`）。AI 域（`settingsCatalog.ts:69-98`）：

| 条目（路由） | 条目 perm（any-of） | 页签 → 组件 | 页签 perm | 功能分册 |
| --- | --- | --- | --- | --- |
| `ai-settings` AI 配置 | `admin.ai_settings` ∨ `admin.ai_runtime_read`（`:70`） | `model` 模型与密钥 → AiSettings.vue（`:73-74`）<br>`runtime` 运行时 → AiOpencodeRuntime.vue（`:75-76`） | `admin.ai_settings`<br>`admin.ai_runtime_read` | 10<br>08/01 |
| `ai-scan` AI 定时巡检 | `admin.ai_scan`（`:78`） | 单页 AiScanTaskManager.vue | — | 07 |
| `ai-skills` AI 技能 | `admin.ai_settings` ∨ `admin.ai_chat_admin`（`:80`） | `square` 技能广场 → AiSkillManager.vue（`:83-84`）<br>`fit` 拟合优化 → AiSkillOpt.vue（`:85-86`） | `admin.ai_settings`<br>`admin.ai_chat_admin` | 08<br>08 |
| `ai-execution` AI 执行中心 | `admin.ai_chat_admin` ∨ `admin.ai_orchestration_admin`（`:88`） | `batches` 批量执行 → AiBatchAdmin.vue（`:91-92`）<br>`sessions` 会话审计 → AiSessionAdmin.vue（`:93-94`）<br>`orchestrations` 编排管理 → AiOrchestrationManager.vue（`:95-96`） | `admin.ai_chat_admin`<br>`admin.ai_chat_admin`<br>`admin.ai_orchestration_admin` | 03<br>06<br>04 |

派生链：侧边栏 `filterGroups(auth.can)` 剔除无权条目与空组（`settingsCatalog.ts:145-149`；`SettingsSideMenu.vue:50,57`）；路由 `buildSettingsRoutes()` 把 `ALL_SETTINGS_ITEMS` 平铺成 22 条路由并携带 `meta.perm`（`settingsRoutes.ts:19-31`）；`/admin` 动态 redirect 到首个有权限条目（`:52`；`firstAccessibleItemPath` `settingsCatalog.ts:155-158`）。页签壳 `SettingsTabShell.vue` 再按页签级权限二次过滤（`:24`），`?tab=` 深链与地址栏双向同步（`:31-43`）。

**旧路径重定向两张表**（`settingsRoutes.ts:34-41` 注释列明四类兼容重定向）：

1. `LEGACY_PATH_ALIASES`（`settingsCatalog.ts:166-171`）——拍平后历史命名与条目 id 不同的 4 条：`webhook-settings→webhook`、`ai-scan-tasks→ai-scan`、`menu-export→data-export`、`etl-tasks→etl`；删除即 404，必须保留。
2. `SETTINGS_REDIRECTS`（`:178-184`）——**AI 域合并前的 5 个旧条目** → 域入口对应页签（目标不是条目 id 而是条目内 tab，故单独一表、redirect 显式携带 `query.tab`）：

| 旧路径 | 重定向到 |
| --- | --- |
| `/admin/ai-opencode` | `/admin/ai-settings?tab=runtime` |
| `/admin/ai-skillopt` | `/admin/ai-skills?tab=fit` |
| `/admin/ai-batches` | `/admin/ai-execution?tab=batches` |
| `/admin/ai-sessions` | `/admin/ai-execution?tab=sessions` |
| `/admin/ai-orchestrations` | `/admin/ai-execution?tab=orchestrations` |

两表键空间不相交；`SETTINGS_REDIRECTS` 先于 `LEGACY_PATH_ALIASES` 生成仅是顺序约定（`settingsRoutes.ts:75-87`）。分组 id 重定向（`/admin/ai` 这类）还会校验 `?tab=` 指向的条目**当前用户有权**，否则落到组内首个有权条目——避免老书签把人引到守卫拦截页（`settingsRoutes.ts:56-73`）。

### 4.6 运行时治理权限拆分（迁移 `2026_09_15_split_ai_runtime_permissions`）

原 `admin.ai_settings` 一把管 Skill/Agent/MCP/重启（迁移 docstring `:1-3`）。拆分产出 **8 个新键**（`NEW_KEYS` `:22-31`；目录注册 `permissions.py:22-29`），与 `routes/ai_opencode_admin.py` 的强制点逐一对应：

| 权限键 | 迁移序 | 强制点（`ai_opencode_admin.py`） | 管辖操作 |
| --- | --- | --- | --- |
| `admin.ai_runtime_read` | `:23` | `:51,:79,:85,:185,:194,:234,:240,:328` | overview/列表/effect 状态/GET runtime（docstring `:13`） |
| `admin.ai_skill_write` | `:24` | `:94,:124,:141,:203,:218` | skill 与辅助文件增删改（docstring `:14`） |
| `admin.ai_agent_write` | `:25` | `:262,:277,:290,:302,:313` | agent 增删改/启停（docstring `:15`） |
| `admin.ai_runtime_publish` | `:26` | `:152` | 平台技能 → 全局发布 |
| `admin.ai_runtime_apply` | `:27` | `:359` | `POST /runtime/apply` 应用待生效配置 |
| `admin.ai_runtime_rollback` | `:28` | **无强制点（预留键）** | 目录有注册、迁移有平移，但二者之外无任何消费者——无路由 `require_permission` 它、前端亦无引用 |
| `admin.ai_runtime_restart` | `:29` | `:378` | `POST /restart` |
| `admin.ai_runtime_force` | `:30` | `:388`（restart 处理器内的**二次检查**，`force` 标志位） | 强制重启（中断运行中回合，docstring `:19`；08 册 §5.2 引 Spec §10.3） |

**平移语义**（`run()` `:34-56`）：`SELECT DISTINCT role_id FROM role_permissions WHERE permission_key='admin.ai_settings'` → 对每个角色 NOT EXISTS 幂等插入全部 8 键 → `invalidate_cache()`（`:54`，进程内权限缓存失效后新键立即可见）。效果：**已有管理员的有效操作面不变**（拆分不收紧），随后可在「角色权限」页按需回收细分项；可重复执行。

**前端入口挂载**：运行时页签 `runtime` 只要求 `ai_runtime_read`（`settingsCatalog.ts:75-76`）——只读可见与写操作分权在页签层成立；写操作由后端逐端点把关，前端不再按写键隐藏按钮。08 册 §5.2 以「权限七键分拆」称呼该族（8 键中 `ai_runtime_rollback` 为预留，实强制的确是七键，两处口径一致不冲突）。

## 5. 关键接口

### 5.1 AI 设置（蓝图 `ai`，权限 `admin.ai_settings`）

| 端点 | 方法 | 语义 | 证据 |
| --- | --- | --- | --- |
| `/ai/settings` | GET | 读全量配置；`apiKey` 打码为 `*(n-4)+末4位`（≤4 字符原样返回） | `ai.py:68-77` |
| `/ai/settings` | PUT | 全量更新；8 个校验分支（endpoint/model 必填 `:93-96`、timeout/maxTokens 正整数 `:97-100`、maxBatchSessions 1-10000 `:106-108`、embeddingProvider ∈ {api,ollama} `:112-114`、ollama 时 URL 必填 `:115-117`、embeddingDims 64-4096 `:118-120`）；掩码回传识别保旧 Key `:122-125`；成功后 `reset_memory_singleton()` `:134`、返回前再打码 `:136-138`、`log_operation`（Key 明文不记录）`:139-141` | `ai.py:80-142`；落库 `ai_query.py:109-129` |

### 5.2 MCP 服务族（蓝图 `ai`，权限 `admin.ai_settings`，6 端点）

| 端点 | 方法 | 语义 | 证据 |
| --- | --- | --- | --- |
| `/ai/mcp-servers` | GET | 外部表全行 + 内置管理伪行 `{name:'check-manage', url, enabled}` | `ai.py:200-212` |
| `/ai/mcp-servers/internal/health` | GET | 服务端探测内置 MCP `/health`（3s 超时），返回 `ok/latencyMs/url`；不可达 502 带错误 | `ai.py:215-232` |
| `/ai/mcp-servers/internal` | PUT | 内置 MCP 开关（只影响之后新建/重置的会话）；写 `ai_settings.mcp_internal_enabled`；`log_operation('update','ai_mcp_internal',…)` | `ai.py:235-248`；`mcp_servers.py:160-171` |
| `/ai/mcp-servers` | POST | 注册外部 MCP；归一化/唯一性违规 → 400（`McpServerError`）；成功 201 | `ai.py:251-261`；`mcp_servers.py:65-86` |
| `/ai/mcp-servers/<server_id>` | PUT | 更新；不存在 404 | `ai.py:264-276`；`mcp_servers.py:89-110` |
| `/ai/mcp-servers/<server_id>` | DELETE | 删除；**已知缺陷：`get_server` 未导入，运行时 NameError**，见 §7.6 | `ai.py:279-288`（缺陷行 `:282`）；`mcp_servers.py:113-119` |

外部配置项校验（两层同契约）：路由层 `_mcp_payload` 仅做字段搬运（`ai.py:188-197`），实质校验在 `mcp_servers._normalize`（type 白名单 `:26-28`、command 须列表 `:33-34`、headers/environment 须对象 `:35-36`、remote 必有 url `:37-38`、local 必有非空 command `:39-40`、字符串归一化 `:43-45`）。

### 5.3 设置中心派生面（前端）

| 出口 | 行为 | 证据 |
| --- | --- | --- |
| 侧边栏 | `filterGroups`：any-of 权限过滤 → 空组剔除 | `settingsCatalog.ts:145-149`；`SettingsSideMenu.vue:50,57` |
| 路由 | `buildSettingsRoutes()`：22 条 `/admin/<id>`，`meta.perm` 原样携带（数组也合法） | `settingsRoutes.ts:19-31` |
| `/admin` redirect | `firstAccessibleItemPath`：首个有权限条目，全无则 `/home` | `settingsRoutes.ts:46-54`；`settingsCatalog.ts:155-158` |
| 分组 redirect | `?tab=` 指向的条目须存在**且有权**，否则落组内首个有权条目 | `settingsRoutes.ts:56-73` |
| AI 旧条目 redirect | `SETTINGS_REDIRECTS` 5 条 → 域入口 + `query.tab` | `settingsRoutes.ts:78-83`；`settingsCatalog.ts:178-184` |
| 历史别名 redirect | `LEGACY_PATH_ALIASES` 4 条 → 新条目 id | `settingsRoutes.ts:85-87`；`settingsCatalog.ts:166-171` |
| 页签壳 | 页签按权限二次过滤 + `?tab=` 深链同步 + keep-alive | `SettingsTabShell.vue:24,31-43,26-29` |

## 6. 依赖与协作关系

- **下游被依赖（配置通道）**：02（NL 查询/建页）、03（批子会话上限）、05（门禁提炼器）、08（拟合 AI 层）、09（mem0/嵌入）、01（默认会话模型）——全部经 §4.4 表列的读取点，写侧唯一。
- **下游被依赖（MCP 配置贡献）**：`ai_chat.create_session`（`ai_chat.py:146-150`）、`ai_chat.clear_session` 清空/重置（`ai_chat.py:2127-2129`）、`ai_session_admin` 轨迹分析建分析会话（`ai_session_admin.py:285-292`）经 `write_opencode_config`（`workspace.py:288-323`）把 `enabled_mcp_config` + `internal_mcp_enabled` 落进 per-session `opencode.json`；`utils/kefu_repo`（客服会话，`kefu_repo.py:204-210`）只消费 `internal_mcp_enabled` + `get_default_chat_model`、**不并外部 MCP**；运行时消费语义归 01 册 §4.1（其第 3 步已写明「外部注册项不得遮蔽平台条目、管理端关闭时整个平台条目不写」）。
- **上游依赖**：`auth.require_permission`（`admin.ai_settings` 门）、`utils.permissions`（目录与缓存）、`utils.operation_log`（全部写操作留痕：`ai.py:139,179,246,259,274,285`）、`config.MCP_SERVER_URL`（内置 MCP 地址，`ai.py:203,223,244`）。
- **与 08 册**：08 册边界声明「AI 设置（`ai_settings`/`get_ai_settings`）归分册 10」；其 §4.4 的 `_llm_json` 通道与 §5.2 的权限七键表与本册 §4.4/§4.6 互为印证，端点细节不重复。
- **与 09 册**：`/ai/memories` 三端点与 mem0 机制归 09；本册只认领 `ai_settings` 的 mem0 五列（`mem0_enabled/embedding_model/embedding_provider/embedding_ollama_url/embedding_dims`）与 PUT 里的对应校验、`reset_memory_singleton` 触发点。
- **与 02 册**：02 册设计决策第 10 条已登记 `ai.py:282` 缺陷并注明「该端点属分册 10」；本册 §7.6 为权威登记条目。

## 7. 设计决策

1. **API Key 明文入库、出接口打码、回传掩码识别**（`ai.py:74-76,122-125,136-138`）：不引入加密存储，赌的是 DB 访问面已收口；管理面前端永不持有完整明文（只显示末 4 位），PUT 以「去末 4 位全 `*`」判定未修改。代价：形如 `****abcd` 的真实 Key 无法录入（会被当未修改丢弃）——概率极低、可换 Key 解决，接受。短 Key（≤4 字符）不打码是规则的边界事实（`len(key) > 4` 才掩码），正常 API Key 长度远超 4，未再加固。
2. **内置 MCP 开关与外部注册分离**：内置不是 `ai_mcp_servers` 的一行，而是 `ai_settings` 的布尔列 + `config.MCP_SERVER_URL` 常量——它由部署拓扑决定（`ai.py:206-211` 管理伪行拼装），不应被当成可删除的外部条目。开关语义钉死在「**之后**新建/重置的会话」，既有工作区永不回改（`workspace.py:300-301`），与外部 MCP 的增删改同一生效模型；前端两处提示文案同源（`McpServersCard.vue:15-17,41-43`）。读取 fail-open（异常回落 True，`mcp_servers.py:156-157`）：内置 MCP 是数据查询/记忆/轨迹分析的工具源，静默断供比多写一条配置危害大。该开关的服务端消费者有四类建会话路径（交互 create/clear、轨迹分析、客服），其中**拒绝型消费者唯一**——轨迹分析在禁用时 409 拒跑（`ai_session_admin.py:190-199`），因为它构建在内置工具集上无法降级；交互与客服会话不禁（少平台工具可用，静默降级，用户可感知自行判断）。
3. **外部 MCP 只贡献配置、平台是 opencode.json 唯一写者**（`mcp_servers.py:1-7` 模块头）：外部服务永远以 `enabled_mcp_config()` 的产出并入 `write_opencode_config`，平台条目名 `check-manage` 同时在 `reserved_names`（`mcp_servers.py:126-130`）与写入处 `name != mcp_name`（`workspace.py:313`）双重防遮蔽——外部注册一个叫 `check-manage` 的服务不能劫持会话的工具面。外部项不做可达性探测（与内置的 health 端点不对称）：外部服务类型多样（remote/local）、探测语义无法统一，坏配置留给会话内暴露。
4. **四域入口合并的菜单演进**：设置中心早期为 AI 能力开过 5 个平级条目（ai-settings/ai-opencode/ai-skillopt/ai-batches/ai-sessions/ai-orchestrations 中除 ai-settings 外的 5 个），随功能增长收敛为 4 个域入口（AI 配置/定时巡检/技能/执行中心），旧路径以 `SETTINGS_REDIRECTS` 永久重定向而非删除（`settingsCatalog.ts:173-177` 注释写明与别名的差异）。合并的原则：**同一治理受众的页面进同一域入口**（配置类 → AI 配置；技能治理 → AI 技能；运行观察类 → 执行中心），页签级权限让一个入口服务多种角色（any-of 条目 perm + 每页签独立 perm + 壳层二次过滤，`SettingsTabShell.vue:24`）。派生式目录（`settingsCatalog.ts:1-8`「唯一真源」头注释）消灭「加了功能忘了加路由」：侧边栏/路由/权限门/重定向全部由同一份数据生成，并有单测钉住（`hub/__tests__/settingsCatalog.test.ts`、`router/__tests__/settingsHubRoutes.test.ts`）。
5. **权限拆分「平移不回退」**（迁移 docstring `:6-9`）：拆分是治理细化而非收紧——现持 `admin.ai_settings` 的角色自动获得全部 8 个新键，操作面不变，之后再按需回收。`ai_runtime_force` 设计为 restart 的**标志位权限**而非独立端点（`ai_opencode_admin.py:366,382,388`）：同一动作的普通/强制两档分权，避免复制端点。`ai_runtime_rollback` 为预留键（§4.6 表）：目录/迁移/授权链已就位，路由侧尚未有回滚操作可管——先占键位保角色平移的完备性，后续补操作时无需再动迁移。
6. **已知缺陷登记（只登记不修）**：`server/routes/ai.py:282` 的 `delete_mcp_server` 处理器调用 `row = get_server(server_id)`，但模块顶部的导入清单（`ai.py:19-22`：`list_servers, create_server, update_server, delete_server, McpServerError, internal_mcp_enabled, set_internal_mcp_enabled`）**不含 `get_server`**（其定义在 `mcp_servers.py:57-62`）——运行时执行到该行必抛 `NameError`，**删除外部 MCP 服务器当前不可用**（GET/POST/PUT 均正常）。该函数本意是删除前取名供 `log_operation`（`:285-287` 已对 `row` 为 None 做了防御，说明作者预期它可能失败，但漏了导入）。Task 3 对码时发现、审核证实；按全局约束零产品代码改动，在此登记为权威条目（02 册设计决策第 10 条有同一条目的转发引用）。次要关联事实：`test_mcp_servers.py` 只覆盖 utils 层 CRUD 与合并逻辑，未覆盖路由层 DELETE，故测试全绿而端点不可用。

## 代码索引

**后端路由（本册认领的 `ai.py` 段）**

- `server/routes/ai.py`（288 行）——docstring `:1-8`；导入（**无 `get_server`**，缺陷证据）`:19-22`；蓝图 `:24`（注册 `app.py:116`）；`POST /query :27-65`（归 02）；`GET /settings :68-77`、`PUT /settings :80-142`（掩码识别 `:122-125`、reset `:134`、审计 `:139-141`）；`/memories :145-181`（归 09）；`_mcp_payload :188-197`；`GET /mcp-servers :200-212`；`GET /mcp-servers/internal/health :215-232`；`PUT /mcp-servers/internal :235-248`；`POST /mcp-servers :251-261`；`PUT /mcp-servers/<id> :264-276`；`DELETE /mcp-servers/<id> :279-288`（**缺陷行 `:282`**）

**后端工具与配置**

- `server/utils/mcp_servers.py`（171 行）——模块头（唯一写者口径）`:1-7`；`McpServerError :15`；`VALID_TYPES :19`；`_normalize :22-47`；`list_servers :50`、`get_server :57`、`create_server :65-86`（uuid `:69`、重名 `:73-75`）、`update_server :89-110`、`delete_server :113-119`；`enabled_mcp_config :122-140`（预留名 `:126-130`）；`internal_mcp_enabled :143-157`（fail-open `:156-157`）；`set_internal_mcp_enabled :160-171`
- `server/utils/ai_query.py`（273 行）——共享 Session `:31-54`；`get_ai_settings :61-106`（默认值 `:74-89`）；`update_ai_settings :109-129`；`nl_to_mongo_filter :186-273`（归 02，通道在此）
- `server/utils/ai_schema_designer.py`（254 行）——通道复用 `:19`、`:198-229`（归 02）
- `server/config.py:134-150`——`get_default_chat_model`（`default_chat_model` → `OPENCODE_MODEL` env → 空）
- `server/utils/batch_repo.py:199-209`——`get_max_files_per_batch`（`max_batch_sessions` 消费）
- `server/utils/workspace.py:288-323`——`write_opencode_config`（include_internal 分支 `:305-311`、防遮蔽 `:313`）
- 合并调用点：`server/routes/ai_chat.py:78,81-86,146-150,2127-2129`（create/clear 共用 `_external_mcp`）；`server/routes/ai_session_admin.py:169,180-199,285-292`；`server/utils/kefu_repo.py:204-210`（客服：仅 internal+model，无 `extra_mcp`）
- mem0 嵌入分支：`server/utils/memory.py:60-88`（机制归 09）

**权限与迁移**

- `server/utils/permissions.py`——`admin.ai_settings :17`；拆分注释 `:18-21`；八键 `:22-29`；`ai_scan :34`、`ai_chat_admin :47`、`ai_orchestration_admin :49`；`invalidate_cache :122`
- `server/migrations/2026_09_15_split_ai_runtime_permissions.py`（60 行）——docstring `:1-12`；`NEW_KEYS :22-31`；`run :34-56`（平移 `:37-51`、缓存失效 `:54`）
- `server/migrations/2026_09_29_embedding_provider.py:23-25`——嵌入提供方三列
- `server/db_schema/rbac.py:6-32`——roles/role_permissions/role_page_permissions DDL
- `server/routes/ai_opencode_admin.py`——权限目录 docstring `:13-19`；强制点 `:51,:79,:85,:94,:124,:141,:152,:185,:194,:203,:218,:234,:240,:262,:277,:290,:302,:313,:328,:359,:378`；force 二次检查 `:388`（端点表归 08 §5.2）

**前端**

- `src/views/admin/hub/settingsCatalog.ts`（184 行）——接口 `:11-41`；六组 `:43-125`（AI 域 `:69-98`）；`ALL_SETTINGS_ITEMS :128`、`findSettingsItem :132`、`itemPerms :136`、`canItem :140-142`、`filterGroups :145-149`、`firstAccessibleItemPath :155-158`；`LEGACY_PATH_ALIASES :166-171`；`SETTINGS_REDIRECTS :178-184`
- `src/router/settingsRoutes.ts`（90 行）——`buildSettingsRoutes :20-31`；`buildSettingsRedirects :43-90`（`/admin :46-54`、分组 `:56-73`、AI 旧条目 `:78-83`、别名 `:85-87`）
- `src/views/admin/hub/SettingsTabShell.vue`（44 行）——页签过滤 `:24`、恒定包装 `:26-29`、深链同步 `:31-43`
- `src/views/admin/hub/AiConfigHub.vue`（6 行）——壳 `:1-2`；同构 `AiSkillHub.vue`/`AiExecutionHub.vue`
- `src/components/admin/McpServersCard.vue`（330 行）——头注 `:1-3`、生效提示 `:15-17`、内置 MCP 行 `:19-44`、表格 `:46-68`、表单弹窗 `:71-105`、`checkInternalHealth :132-142`、`toggleInternal :144-161`、KV/行解析 `:163-179`、`loadMcp :181-188`、`mcpPayload :213-223`、`saveMcp :225-242`、`toggleMcp :244-251`、`removeMcp :253-264`
- `src/views/admin/AiSettings.vue`（313 行）——GET `:218`、listModels `:242`（`api/aiChat.ts:285`）、PUT `:252`
- `src/api/aiMcpServers.ts`——6 函数 `:16,:21,:26,:31,:50,:55`
- 消费方：`src/components/layout/SettingsSideMenu.vue:50,57`；单测 `src/views/admin/hub/__tests__/settingsCatalog.test.ts`、`src/router/__tests__/settingsHubRoutes.test.ts`、`hub/__tests__/SettingsTabShell.test.ts`

**表**

- `ai_settings`：`server/db_schema/core.py:155-168`（CREATE+种子）、`:172-180`（幂等补列）；`server/migrations/2026_09_29_embedding_provider.py:23-25`
- `ai_mcp_servers`：`server/db_schema/core.py:186-197`
- 测试：`server/tests/test_mcp_servers.py`（utils 层 CRUD 与合并；未覆盖路由层 DELETE，见 §7.6）
