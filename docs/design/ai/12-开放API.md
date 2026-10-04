# 12 · 开放API

> 上游：[00-总览与功能清单](./00-总览与功能清单.md) · [01-总体架构与运行时](./01-总体架构与运行时.md) · [03-批任务与执行引擎](./03-批任务与执行引擎.md)
> 边界声明：批状态机（`BATCH_TERMINAL_STATUSES`）、预算、outbox 交付与 webhook HMAC 的**机制主描述在分册 03**（容灾视角在分册 11）；编排 run 的引擎侧语义（冻结/推进/归属）**归分册 04**；`/v1/ai-approvals` 审批端点的门禁语义（decision_hash/edits/点名权限）**归分册 05**；执行审计来源五源（含 kefu）口径**归分册 06**；定时扫描任务的引擎侧**归分册 07**；长期记忆 mem0 机制**归分册 09**。本册从**对外契约视角**汇总 `/v1` 家族：鉴权链、端点面、幂等语义与回调契约，行号以本 worktree 实际代码为准。

## 1. 模块职责

开放 API 是系统对**外部系统**（非浏览器前端）暴露的统一 HTTP 契约层，挂在 `/v1` 前缀下（公网经 `proxy.py` 以 `/api/v1/...` 访问，代理契约「`/api/X` → 后端 `/X`」，`docs/user-guide/integration/ai-batch-api.md` §1）。它分两大块：**数据面**（`/v1/collections`、`/v1/branches`、`/v1/files`，按「页面是否开放 API」授权读写业务数据）与 **AI 能力面**（批任务/单会话/编排/审批/扫描/记忆/Prompt 模板/行操作，按「密钥是否绑定用户」授权并严格按密钥属主隔离）。对外契约层的统一纪律：**只调底层 repo/engine 函数，不复用内部路由的处理器**——内部返回体含 `opencode_session_id`/`workspace_path` 等实现细节，一旦对外就成了公开承诺（`open_api_batches.py:1-7` 文件头）。

按职责分九个蓝图（glob `open_api_*.py` 匹配 7 个 + `open_api.py` + 同前缀的 `ai_approvals.py`；注册于 `server/app.py:99-106` 与 `:521`）：

| 职责 | 文件 | 前缀 | 端点数 | 鉴权 |
| --- | --- | --- | --- | --- |
| 数据面：公开集合 CRUD/批量/文件 | `server/routes/open_api.py`（782 行） | `/v1` | 11 | `api_key_required` |
| AI 批任务全家桶 | `server/routes/open_api_batches.py`（1257 行） | `/v1/ai-batches` | 28 | `api_key_required`+`require_bound_key` |
| AI 单会话（不拆子任务） | `server/routes/open_api_ai_sessions.py`（121 行） | `/v1/ai-sessions` | 3 | 同上 |
| 编排对外契约 | `server/routes/open_api_orchestrations.py`（128 行） | `/v1/ai-orchestrations` | 5 | 仅 `api_key_required`（观测，见 §7-7） |
| 行操作触发 | `server/routes/open_api_row_actions.py`（106 行） | `/v1/collections` | 1 | `api_key_required`+`require_bound_key` |
| 定时扫描任务触发 | `server/routes/open_api_scan_tasks.py`（90 行） | `/v1/ai-scan-tasks` | 3 | 同上 |
| Prompt 模板 CRUD | `server/routes/open_api_prompt_templates.py`（124 行） | `/v1/prompt-templates` | 5 | 同上 |
| 长期记忆管理 | `server/routes/open_api_memories.py`（77 行） | `/v1/memories` | 3 | 同上 |
| AI 审批（JWT 例外） | `server/routes/ai_approvals.py`（73 行） | `/v1/ai-approvals` | 3 | `login_required`（JWT，归分册 05） |

合计 62 个端点：59 个走 API Key、3 个走 JWT。**不存在 `open_api_approvals.py`**——`/v1/ai-approvals` 家族物理上在 `ai_approvals.py`（双身份体系里它是给人（JWT）裁决用的，不是给密钥调用方），已在分册 05 §5.2 登记端点表，本册 §5.9 仅作家族汇总互指。

## 2. 模块组成

### 2.1 后端

- `server/routes/open_api.py`（782 行）——蓝图 `open_api`，前缀 `/v1`（`:14`），注册 `app.py:99`。数据面 11 端点（§5.1）。安全边界三件套：`check_collection_public :61-69`（`page_configs.api_public`）、`check_collection_writable :95-103`（公开 + `api_writable` 双闸）、`file_in_public_collection :34-44`（文件仅当被某个公开集合的记录引用才可见）。分支模型：`get_request_branch_id :84-92` 校验 `branchId` 参数（默认 `config.OPEN_API_BRANCH`，`config.py:75`，默认 `'main'`；非默认分支须为 `project_versions` 中 `status='active'`，`validate_branch_id :72-81`）。
- `server/routes/open_api_batches.py`（1257 行）——蓝图 `open_api_batches`，前缀 `/v1/ai-batches`（`:39-40`），注册 `app.py:100`。28 端点（§5.2）。蓝图级 `before_request` `_guard_request_body_size :49-81` 按 Content-Length 在 Werkzeug 解析 body 之前卡超大请求；常量 `:43-46`：单文件 20MB、暂存 TTL 24h、`MAX_PROMPT_CHARS=20000`、`TERMINAL_STATUSES=('completed','partial','failed')`。暂存安全：`_validate_staged_path :95-112`（路径必须落在 `batch-staging/<本人userId>/` 下、禁绝对路径与 `..`）、`_sweep_stale_staging :115-144`（上传时顺带清 TTL 过期暂存）、`_validate_files :193-215`（创建/追加/单会话三处共用的形状+归属+存在性校验）。出参白名单 `_batch_out :218-251`（`callbackSecret` 刻意不回显，`:221-222`）。
- `server/routes/open_api_ai_sessions.py`（121 行）——蓝图 `open_api_ai_sessions`，前缀 `/v1/ai-sessions`（`:28-29`），注册 `app.py:106`。3 端点（§5.3）：创建/查状态/cancel。复用批任务的 `MAX_PROMPT_CHARS` 与 `_validate_files`（`import` 于 `:21`，一处实现两处调用）与 `/v1/ai-batches/uploads` 上传端点（文件落在哪个用户的暂存目录下才是校验边界，模块头 `:12-16`）；底层 `server/utils/ai_session_repo.py`（124 行）：`create_session :18-50`（prompt 暂存 `continue_prompt` 列 + `api_key_id` + `input_files` JSONB）、`get_session_for_owner :53-74`（user_id+api_key_id **双重归属校验**）、`cancel_session :95-124`。
- `server/routes/open_api_orchestrations.py`（128 行）——蓝图 `open_api_orchestrations`，前缀 `/v1/ai-orchestrations`（`:18-19`），注册 `app.py:105`。5 端点（§5.4）：definitions 只读两端点 + runs 三端点。`create_run` 以密钥 owner 身份发起（`requested_by_kind='api_key'`，`:82-84`），创建后 `get_worker().notify()` 唤醒批 worker（`:87-91`）；归属隔离：`run.requested_by != 密钥 owner` 一律 404 不泄漏存在性（`:103-104,115-116`）。引擎侧（run 创建冻结/step 展开/推进）归分册 04。
- `server/routes/open_api_row_actions.py`（106 行）——蓝图 `open_api_row_actions`，前缀 `/v1/collections`（`:25-26`），注册 `app.py:101`。1 端点（§5.5）。独立成文件的理由：直接透传 `row_action_engine.RowActionError.message`（中文），与 open-api.md 的全英文 error 惯例冲突（模块头 `:1-13`）。行操作上下文 `_load_row_action_context :35-51` 独立一份而不跨路由文件 import（改生产路由的风险高于复制十来行 SQL，`:36-38`）。
- `server/routes/open_api_scan_tasks.py`（90 行）——蓝图 `open_api_scan_tasks`，前缀 `/v1/ai-scan-tasks`（`:20-21`），注册 `app.py:102`。3 端点（§5.6）。`run-now` 是内部 `admin.ai_scan` JWT 权限门的 API Key 等价物：按 `ai_scan_tasks.owner_user_id` 与密钥 owner 匹配（`:66-90`）；刻意不检查 `task['enabled']`（内部 run-now 也不检查，不引入内部没有的新规则，`:9-10`）。
- `server/routes/open_api_prompt_templates.py`（124 行）——蓝图 `open_api_prompt_templates`，前缀 `/v1/prompt-templates`（`:25-26`），注册 `app.py:103`。5 端点（§5.7），直接复用 `utils/prompt_template.py` 五函数（本就以 user_id 为第一参数、SQL 层按 user_id 过滤）；error 沿用内部路由的英文文案（该家族不在「AI 对外中文」惯例内，模块头 `:1-8`）。
- `server/routes/open_api_memories.py`（77 行）——蓝图 `open_api_memories`，前缀 `/v1/memories`（`:25-26`），注册 `app.py:104`。3 端点（§5.8），复用 `utils/memory.py`。安全关键点：`delete_memory(memory_id)` 不按 user_id 过滤，删除前必须先 `list_memories(owner)` 建「这把密钥能看到的 id 集合」再确认目标在集合内，否则是 IDOR（模块头 `:1-15` 与 `:69-74`）。
- `server/routes/ai_approvals.py`（73 行）——蓝图 `ai_approvals`，前缀 `/v1/ai-approvals`（`:13-14`），注册 `app.py:521`。3 端点（§5.9）：列表按角色过滤、approve/reject 统一 `_decide :41-73`（approve 可带 `edits` 合并 `run_input_snapshot`，`:45-46`）。JWT 门禁与决策幂等归分册 05。

配套：

- `server/auth.py`——鉴权链两装饰器：`api_key_required :172-210`、`require_bound_key :213-225`；`hash_api_key :167-169`（SHA-256）。
- `server/routes/api_keys.py`——密钥管理面（JWT `admin.api_keys` 权限）：创建时明文密钥 `cm_<token_urlsafe(32)>` **仅创建响应返回一次**（`:52`）、`owner_user_id` 绑定为创建者（`:58`）、启停（PUT `:75`、`toggle_api_key :77-103`）、删除（DELETE `:105`、`delete_api_key :107` 起）。
- `server/utils/api_errors.py`——`/v1/*` AI 能力蓝图共用的错误 helper：`err() :19-42` 输出 `{error, code}` 形状（`error` 字符串是既有契约不破坏；`code` 是加法扩展；P1-A1 起可选 `error_detail` 结构化增量 retryable/phase/attempt/evidenceRefs）、`register_error_handlers :44`（各 AI 蓝图注册处如 `open_api_batches.py:41`）。
- `server/utils/upload_limits.py`——请求体上限**唯一真源**（零依赖模块，Flask 与 `proxy.py` 两进程共读）：`MAX_UPLOAD_TOTAL_BYTES=100MB`（`:20`）、uploads 路径再留 1MB multipart 余量（`:21-23`）、其余 JSON 端点 1MB（`:25`）、`body_limit_for_path :44`（不属于 AI 对外接口的路径一律返回 None=不限制，保住备份还原与 `/v1/collections` 大 JSON 通道）。
- `server/utils/operation_log.py`——`log_api_operation :113`（API Key actor 专用，与 JWT 的 `log_operation` 共享 INSERT `:51`）。

### 2.2 前端

本家族**没有专属前端页面**（消费方是外部系统）。相关面只有两处管理入口：密钥管理页（`api_keys.py` 对应的管理 UI，创建/启停/删除）与分册 05 的审批收件箱 `WorkflowInbox.vue`（`/v1/ai-approvals` 的人类消费方）。对外文档面在 `docs/user-guide/integration/`：`open-api.md`（数据面）、`ai-batch-api.md`（批任务）、`ai-session-api.md`（单会话）与两份 OpenAPI 风格参考页（`open-api-reference.html`/`ai-api-reference.html`、`openapi.yaml`）。

## 3. 数据模型

### 3.1 `api_keys`（`db_schema/core.py:235-243` + `init_db.py:1579-1612`）

| 列 | 来源 | 用途 |
| --- | --- | --- |
| `id`/`name`/`key_hash`/`created_at`/`last_used_at`/`is_active` | `core.py:235-243` | 密钥本体；**只存 SHA-256 哈希**（`hash_api_key`），明文仅创建时返回一次 |
| `owner_user_id VARCHAR(100)` FK users **ON DELETE SET NULL** | `init_db.py:1590-1612`（幂等 ALTER + FK 重建） | 密钥↔用户绑定；用户被删后置 NULL，`require_bound_key` 拒绝该密钥（要求重建） |

鉴权链读 `api_keys LEFT JOIN users`（`auth.py:188-193`）——LEFT JOIN 而非 JOIN 是为了 owner 已删/未绑定时密钥行本身还能被查到、由 `require_bound_key` 统一拒绝（`auth.py:184-187` 注释）。

### 3.2 归属与来源列（复用分册 03 的表）

| 表.列 | 证据 | 开放 API 语义 |
| --- | --- | --- |
| `ai_chat_batches.api_key_id` / `scan_task_id` | `batch_repo.py:288-292` 写入 | 来源区分；列表/详情/控制全部按 `(owner_user_id, api_key_id)` 双条件过滤（`batch_repo.list_batches/get_batch_detail` 带 `api_key_id=`，`open_api_batches.py:428-429,439`） |
| `ai_chat_batches.callback_url` / `callback_secret` | `db_schema/ai_batches.py:31-33` | 批终态回调目标与 HMAC 密钥（§4.6） |
| `ai_chat_sessions.api_key_id` / `input_files` / `continue_prompt` | `ai_session_repo.py:43-46`；`continue_prompt` 列 `db_schema/ai_scan.py:45` | 单会话行的归属（user_id+api_key_id 双检）、随附文件、prompt 暂存（首 claim 时被 `_run_one` 的 `batch_id IS NULL` 分支读取并清掉，`ai_session_repo.py:1-8`） |
| `ai_execution_commands.idempotency_key` UNIQUE | `2026_09_23_harness_p1_durable_execution.py:126-127` | 命令幂等（§4.5） |
| `ai_delivery_outbox.signature` / `(event_id,target_url)` UNIQUE | `2026_09_23_harness_p1_durable_execution.py:154-177` | 回调 HMAC 密钥随行保存、投递去重（机制归分册 03 §3.7） |
| `page_configs.api_public` / `api_writable` / `row_actions` / `fields` | `open_api.py:61-69,95-103,106-111`；row_actions 读取 `open_api_row_actions.py:40-44` | 数据面授权模型：页面级开关决定集合可见/可写；`fields` 驱动必填校验、主键唯一性、file/image 字段 apiUrl 补全 |
| `project_versions.status='active'` | `open_api.py:72-81` | 非默认分支须 active 才可经 `branchId` 查写 |

### 3.3 审计与用量

API Key 来源的批子会话在执行审计中 `source_type='open_api'`（由批行 `api_key_id` 判定，`batch_engine.py:1440-1476`，口径归分册 06）；会话管理面的来源 CASE 列把「batch_id + 批 api_key_id」投影为 `api_batch`（`session_admin_repo.py:25-32` 五源，分册 06）。用量经 `_batch_out` 附带 `usage`（`open_api_batches.py:241` → `get_batch_usage`）；api_key scope 预算恒 drain（分册 03 §4.5）。

## 4. 核心流程

### 4.1 鉴权链：X-API-Key → 哈希查表 → 绑定键 → 归属过滤

1. **密钥鉴别**（`api_key_required`，`auth.py:172-210`）：请求头 `X-API-Key` → SHA-256 → `api_keys.key_hash` 查表（LEFT JOIN users 带出 `ownerUserId/ownerUsername/ownerRole`）；无效 401、已吊停 401；顺带 `UPDATE last_used_at`（`:200-203`）。通过后 `g.api_key_info = {id, name, ownerUserId, ownerUsername, ownerRole}`（`:205-208`）——后续所有家族的 `_current_key()`（如 `open_api_batches.py:84-86`）都从这里取。
2. **绑定键闸**（`require_bound_key`，`auth.py:213-225`）：`ownerUserId` 为 NULL 的存量/失主密钥一律 403「请重新创建」——放行它们等于外部调用以某个人的名义跑 AI、烧额度、把任务塞进他账号。
3. **归属过滤**（各视图内）：AI 家族所有读写都以 `key['ownerUserId']` + `key['id']` 双条件落到 repo 层（批任务 `open_api_batches.py:428-429`；单会话 `ai_session_repo.py:70` 的 `user_id AND api_key_id` 双检；编排 `requested_by` 比对 404 `open_api_orchestrations.py:103-104`）。API Key 无角色概念，需要角色判定的场景按**密钥属主本人的角色**解析（行操作 `ownerRole`，`open_api_row_actions.py:80`）——等价于「这把密钥的操作权限=它绑定的用户本人」。
4. **例外**：`/v1/ai-approvals` 是 `/v1` 前缀下的 JWT 家族（`login_required`，`ai_approvals.py:17-38`）——审批裁决是人的动作，不走密钥。

数据面没有第 2/3 步：任何有效密钥可读全部 `api_public` 集合（写还需 `api_writable`），授权粒度是页面级而非用户级。

### 4.2 数据面：公开集合读写（`open_api.py`）

- **读**：`GET /collections`（仅列 `api_public=TRUE`，`:170-191`）→ `GET /collections/<c>`（MongoDB 风格 `q` 过滤经 `remap_labels`+`mongo_translate` 翻译成参数化 SQL 片段，`:243-260`；分页 ≤100）→ `GET /collections/<c>/<id>` → `GET /collections/<c>/schema`。file/image 字段的每个文件对象就地补 `apiUrl` 指回开放下载端点（`enrich_file_urls :47-58`）。
- **写**：`POST /collections/<c>`（必填校验 → ID/业务主键唯一性 → 剥离内部字段 → statusBadge 变化时间戳 → 写入后 `reseed_sequences` 重播种序列计数器防与网页创建重号，`:357-428`）；`POST /collections/<c>/batch`（≤1000 条，**create-only 不做 upsert**：批内重复 id 所有出现都失败、与库内冲突按 per-record 错误，`continueOnError=false` 时任一失败整批 400 不写入；id 唯一性刻意**不按 collection 过滤**——`dynamic_data` 真实主键是 `(id, branch_id)`，跨集合撞 id 在这里以干净的单条错误暴露而不是炸 INSERT 回滚整批，`:431-578`）；`PUT /collections/<c>/<id>`（乐观锁 `_version` 不匹配 409 `VERSION_CONFLICT`，合并式部分更新后再跑必填/主键校验，`:581-678`）。
- **文件**：`POST /files`（multipart 上传，仅允许向公开且可写集合传，选填 `fieldName` 时按页面配置的允许扩展名校验；API Key 无关联用户故 `uploaded_by=NULL`，`:733-782`）→ `GET /files/<id>`（元数据）/ `GET /files/<id>/download`。**可见性边界**：`file_in_public_collection` 要求该文件被某个 `api_public` 集合的记录引用，防止拿密钥拉非公开集合上的文件（`:34-44`）。

### 4.3 AI 批任务面（`/v1/ai-batches`，28 端点）

主链与分册 03 §4.1 的用户面完全同构，差异只在入口契约：

1. **暂存**：`POST /uploads`（`:147-190`）→ `batch-staging/<owner>/<upload_session_id>/`，返回 `{name, path}`；上传时顺带清该 owner 下 TTL>24h 的暂存目录（含统一前的 legacy 树，`:156-161`）。
2. **创建**：`POST ''`（`:358-414`）：name/prompt 必填、prompt ≤20000 字符、0 文件空批壳合法（后续 append 填充）、`callbackUrl` 须 http(s)（`_validate_callback_url :263-270`）、`_validate_files` 路径归属+存在性（超期暂存创建时即 400，不留注定空跑的批次）、`actionChecks` 与内部创建同一道 `agent_ledger.validate_checks` 校验（P0 F4，非法 400 不带病入库，`:391-398`）→ `create_batch(..., api_key_id=key['id'], callback_url, callback_secret)` → `get_worker().notify()`。
3. **观察**：列表/详情/结果 `GET ''|/<id>|/<id>/results`（出参白名单 `_batch_out`：进度计数 + P1 扩展 generation/queueWaitMs/runningMs/lastProgressAt + paused/eventCursor + children 结构化数组，`:218-251`）；事件增量 `GET /<id>/events`（§4.5）；子会话级读四端点（messages 完整对话 / files 实时扫描 / files/download 单文件 `safe_resolve` 防 symlink 越界 / files/download-all ZIP：>50MB 单文件跳过，`:702-850`）。
4. **控制**：cancel / retry-failed（与重新执行同语义，工作区重置失败 500）/ append（刻意不限终态，追加后批次回 running）/ PATCH 配置（**整体替换语义**：省略的 callbackUrl/Secret 会被清空，须整组重传；actionChecks/gateRetry 显式传入才更新，`:918-968`）/ pause / resume / 子会话 cancel|continue|reexecute / 删除（非终态须 `stop=true` 先停止，bounded drain 10s 超时 409 `BATCH_DRAIN_TIMEOUT` 保留任务与工作区，`:531-588`）/ 命令平面 `POST /<id>/commands` + `GET /<id>/commands/<cmd>`（§4.5）。
5. **发现**：`GET /agents|/models|/skills`（`:273-355`）——单会话 API 不重复建，复用这三个端点（`open_api_ai_sessions.py:10`）。
6. **自然语言查询**：`POST /query`（`:971-1022`）——中文/英文问题 → MongoDB 过滤器 JSON（`nl_to_mongo_filter` → `remap_labels` 安全回落 → `mongo_translate` dry-run 校验，语法无效 422 附 `raw_filter`）；**只翻译不执行**，调用方拿 filter 去 `GET /v1/collections/<c>?q=` 取数。

控制类写操作（pause/resume/cancel/retry/force_stop）先落命令账本再同步应用：pause/resume/子会话 cancel 不带显式幂等键（自然键派生），命令平面端点强制 `Idempotency-Key` 头（§4.5）。

### 4.4 单会话面（`/v1/ai-sessions`，batch_id IS NULL 分支）

`create_session`（`ai_session_repo.py:18-50`）插入一行 `status='pending'`、`batch_id=NULL`、`api_key_id=非空` 的 `ai_chat_sessions`，prompt 暂存 `continue_prompt`，files 存 `input_files` JSONB。执行与批任务**共用同一个 worker/dispatcher/并发上限**：claim CTE 的候选条件是 `batch_id IS NOT NULL OR api_key_id IS NOT NULL OR orchestration_run_id IS NOT NULL`（`batch_engine.py:1133-1134`）——单会话（api_key_id）、编排首节点（orchestration_run_id）、批子任务（batch_id）三来源在同一张表里排队；`_run_one` 的 `batch_id is None` 分支首 claim 时读 `continue_prompt` 并立即清掉、把 `uploads/` 下的随附文件拷进会话工作区（`ai_session_repo.py:1-8` 模块注释）。与批任务的契约差异：所有文件进**同一个**会话、不按文件拆子任务；`status` 五态 pending/running/completed/failed/cancelled，`output` 仅 completed 非空、`error` 仅 failed 非空；**没有 SSE，轮询是唯一完成信号**（`open_api_ai_sessions.py:95-97`）。取消语义：pending 时 worker 下一轮直接落 cancelled 不占并发槽；running 时协作式打断（OpenCode abort + 提前结束轮询）；已终态 409（`:105-121`）。归属双检见 §4.1-3。

### 4.5 幂等语义（逐条对码）

| 机制 | 端点 | 语义与证据 |
| --- | --- | --- |
| 命令平面幂等键 | `POST /v1/ai-batches/<id>/commands` | **`Idempotency-Key` 头必填**（缺失 400 `COMMAND_REJECTED`，`open_api_batches.py:1133,1139-1143`）；`execution_commands.submit_command` 同键返回既有命令 `duplicate=True` 不重复执行（`execution_commands.py:29-50`）；重复键 200、新建 202（`:1203-1205`）。应用失败按类收口：状态冲突 → `rejected/COMMAND_REJECTED` 409、工作区重置失败 → `rejected/WORKSPACE_RESET_FAILED` 500（fs 先于库 fail-closed，不留悬挂 pending，`:1188-1195`） |
| 自然键派生 | pause `:1028` / resume `:1057` / 子会话 cancel `:1086` | 不带显式键，`submit_command` 由 `(type, batch, session, requester)` 派生自然键（`execution_commands.py:31-39`） |
| 结果读取幂等 | `POST /<id>/import` | 已导入过的路径返回原文件 id（`:493-499` docstring 与 `session_file_import` 实现） |
| 事件游标幂等 | `GET /<id>/events`、编排 `GET /runs/<id>/events` | `afterSeq` 增量（只返回 `event_seq > afterSeq`），客户端按 `eventId` 幂等合并（`:1226-1228`）；游标过旧（保留期裁剪后 `afterSeq < min_seq-1`）410 `CURSOR_EXPIRED` 携带 `earliestSeq`（`:1241-1250`）。两处同源 `batch_events.read_events`（编排侧 limit 固定 200，`open_api_orchestrations.py:117-128`） |
| 乐观锁 | `PUT /v1/collections/<c>/<id>` | `_version` 不匹配或 UPDATE rowcount=0 → 409 `VERSION_CONFLICT`（`:614-621,655-666`） |
| create-only 批量 | `POST /v1/collections/<c>/batch` | 无 upsert：重复 id 记失败不覆盖（§4.2） |
| 审批裁决幂等 | `POST /v1/ai-approvals/<id>/approve|reject` | CAS + `decision_hash`（机制归分册 05） |

### 4.6 回调交付与 HMAC（衔接分册 03/11）

创建/编辑时可配 `callbackUrl`+`callbackSecret`（`create :369-370`、`PATCH :934-935`、URL 校验 `:263-270`；`callbackSecret` 刻意不在 list/detail 回显，只回 `callbackUrl` 供确认配置，`:221-222`）。批次落终态（`completed/partial/failed`，且每次终态迁移都触发——retry/append/continue 可把终态批次拉回 running 再次落终态）时的投递有两条路，机制主描述在分册 03 §4.7：

1. **outbox 可靠投递**（默认）：终态提交与 `ai_delivery_outbox` 入队**同事务**（`batch_repo.recompute_batch_status_tx :58-81`）；投递线程 `deliver_one` **复用 `webhook_engine._fire_single_webhook` 的 HMAC 与 HTTP**（`delivery_outbox.py:183-215`）；退避 1s/5s/30s/5m/30m ×8 后 `dead_letter` 等人工重放（`BACKOFF_SECONDS :22`/`MAX_ATTEMPTS :23`）。
2. **直发兜底**（`AI_DELIVERY_OUTBOX_ENABLED=0` 回退路径）：`_notify_callback`（`batch_engine.py:509-545`）在 daemon 线程调同一个 `_fire_single_webhook`（同步调用最坏 ~120s 会占满 3 个 worker 槽位，`:514-518` 注释）；outbox 启用时直接 return 防重复投递（`:525-530`）。payload `{'event':'ai_batch_completed', batchId, status, total, done, failed}`、`event_type='ai_batch_completed'`、timeout 30s retries 3（`:537-543`）。

HMAC 口径（`webhook_engine.py`）：`_compute_signature :393-405` 对 `${timestamp}.${payload_json}` 做 HMAC-SHA256；请求头 `X-Webhook-Timestamp`/`X-Webhook-Signature`/`X-Webhook-Event`（`:333-335`）。webhook 规则引擎（管理面 `/webhooks`，归分册 10/11 的 webhook 语境）与批回调共用同一套签名函数——**一套 HMAC 实现、两类消费方**。

### 4.7 请求体上限门（双进程同源）

`utils/upload_limits.py` 是唯一真源，两道执行点：Flask 侧 `open_api_batches.py:49-81` 的蓝图级 `before_request`（批任务家族）+ `app.py:135-160` 的应用级 `before_request`（补齐 `/v1/ai-sessions`、`/v1/memories`、`/v1/prompt-templates`、`/v1/ai-scan-tasks`、行操作 run 五族——此前这些端点只有 proxy 会拦）；代理侧 `proxy.py` 在 `rfile.read()` 之前拦截（它把整个 body 读进代理内存，发生在 Flask 之前）。无 `Content-Length` 的分块传输一律 411。**刻意不设全局 `MAX_CONTENT_LENGTH`**：备份还原 ZIP 大小本质无上界；`/v1/collections` 数据接口刻意不整段限制（可能批量导入大 JSON），只精确匹配行操作 run 端点（`upload_limits.py:14-17,44`）。

## 5. 关键接口

### 5.1 `open_api.py`（前缀 `/v1`，11 端点，全部 `@api_key_required`）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `/collections` | `list_collections` `:170` | 公开集合清单（含 writable 标志） |
| 2 | GET `/branches` | `list_branches` `:194` | main + active 分支清单 |
| 3 | GET `/collections/<c>` | `list_collection_data` `:222` | 分页 ≤100 + `q` Mongo 过滤 + `branchId` |
| 4 | GET `/collections/<c>/<id>` | `get_collection_item` `:289` | 单记录 |
| 5 | GET `/collections/<c>/schema` | `get_collection_schema` `:320` | 字段定义（仅公开） |
| 6 | POST `/collections/<c>` | `create_collection_item` `:357` | 公开+可写；必填/ID/主键校验；序列重播种 |
| 7 | POST `/collections/<c>/batch` | `create_batch_items` `:431` | ≤1000 create-only 批量（§4.2） |
| 8 | PUT `/collections/<c>/<id>` | `update_collection_item` `:581` | `_version` 乐观锁 + 合并式更新 |
| 9 | GET `/files/<id>` | `api_get_file_metadata` `:681` | 元数据（须被公开集合引用） |
| 10 | GET `/files/<id>/download` | `api_download_file` `:707` | 下载（同上边界；磁盘缺失 410） |
| 11 | POST `/files` | `api_upload_file` `:733` | multipart 上传，返回 uid 供写记录引用 |

### 5.2 `open_api_batches.py`（前缀 `/v1/ai-batches`，28 端点，全部 `@api_key_required`+`@require_bound_key`）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | POST `/uploads` | `upload_files` `:147` | 暂存上传（单文件 20MB/单次 100MB/TTL 24h 顺带清扫） |
| 2 | GET `/agents` | `list_agents` `:273` | 主 agent 清单（滤内部 agent）；OpenCode 不可用 502 |
| 3 | GET `/models` | `list_models` `:300` | 已连接 provider 的模型（`<providerID>/<modelID>`） |
| 4 | GET `/skills` | `list_skills` `:341` | enabled 全局技能 |
| 5 | POST `` | `create` `:358` | name/prompt 必填；0 文件空批壳合法；callback/actionChecks 校验；`api_key_id` 落列 |
| 6 | GET `` | `list_` `:417` | 分页 ≤100，按 owner+密钥隔离 |
| 7 | GET `/<batch_id>` | `detail` `:434` | 详情（`_batch_out` 白名单） |
| 8 | GET `/<batch_id>/results` | `results` `:445` | 结果 + opaque childId 双轨（seq 兼容） |
| 9 | GET `/<batch_id>/file-records` | `file_records` `:470` | 子会话产出文件记录（name+seq 定位） |
| 10 | POST `/<batch_id>/import` | `import_files` `:493` | 产出导入 data_files（幂等返回原 id） |
| 11 | DELETE `/<batch_id>` | `remove` `:531` | stop-first：非终态须 stop=true，drain 10s 超时 409 保留 |
| 12 | POST `/<batch_id>/cancel` | `cancel` `:591` | 中断整批（终态 409） |
| 13 | POST `/<batch_id>/retry-failed` | `retry_failed` `:610` | 仅终态；先清工作区再重排 |
| 14 | POST `/<batch_id>/append` | `append` `:635` | 刻意不限终态；追加后回 running |
| 15 | GET `/<id>/sessions/<cid>/messages` | `session_messages` `:702` | 子会话完整对话 |
| 16 | GET `/<id>/sessions/<cid>/files` | `session_files` `:726` | 工作区实时文件列表 |
| 17 | GET `/<id>/sessions/<cid>/files/download` | `session_file_download` `:756` | 单文件下载（`safe_resolve` 防 symlink 越界） |
| 18 | GET `/<id>/sessions/<cid>/files/download-all` | `session_files_download_all` `:786` | 新增+修改打包 ZIP（>50MB 跳过） |
| 19 | POST `/<id>/sessions/<cid>/continue` | `session_continue` `:853` | 终态子会话追加一轮（202） |
| 20 | POST `/<id>/sessions/<cid>/reexecute` | `session_reexecute` `:886` | 单子会话清历史重跑（不限 failed） |
| 21 | PATCH `/<batch_id>` | `update_config` `:918` | 整体替换：agent/model/callback（省略即清空）；actionChecks/gateRetry 显式传入才更新 |
| 22 | POST `/query` | `ai_query` `:971` | NL→Mongo filter 翻译（422 附 raw_filter） |
| 23 | POST `/<batch_id>/pause` | `pause` `:1028` | 命令账本+暂停（自然键） |
| 24 | POST `/<batch_id>/resume` | `resume` `:1057` | 命令账本+恢复（自然键） |
| 25 | POST `/<id>/sessions/<cid>/cancel` | `session_cancel` `:1086` | 单子任务取消（pending 直落/running 协作/paused 落 cancelled） |
| 26 | POST `/<batch_id>/commands` | `submit_command` `:1122` | 命令平面：`Idempotency-Key` 必填；pause/resume/cancel/retry/force_stop |
| 27 | GET `/<id>/commands/<command_id>` | `get_command` `:1208` | 命令状态轮询 |
| 28 | GET `/<batch_id>/events` | `events` `:1223` | afterSeq 增量 + `CURSOR_EXPIRED` 410（§4.5） |

子会话定位 `_resolve_child :680-699` 三级尝试：纯数字→batch_seq、basename、子会话 id（opaque childId）。

### 5.3 `open_api_ai_sessions.py`（前缀 `/v1/ai-sessions`，3 端点，全部绑定键）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | POST `` | `create` `:53` | prompt 必填 ≤20000；files 复用 `_validate_files`；`create_session`（api_key_id 落列）+ notify |
| 2 | GET `/<session_id>` | `detail` `:91` | 状态轮询（user+api_key 双归属）；无 SSE，轮询唯一 |
| 3 | POST `/<session_id>/cancel` | `cancel` `:105` | pending 直落/running 协作打断；终态 409 |

### 5.4 `open_api_orchestrations.py`（前缀 `/v1/ai-orchestrations`，5 端点，仅 `@api_key_required`）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `/definitions` | `list_definitions` `:55` | 已发布定义（白名单出参 `_out_definition :28`） |
| 2 | GET `/definitions/<def_id>` | `get_definition` `:62` | 单定义 |
| 3 | POST `/runs` | `create_run` `:71` | 以密钥 owner 身份发起（`requested_by_kind='api_key'`）；notify 唤醒 worker |
| 4 | GET `/runs/<run_id>` | `get_run` `:97` | 归属隔离 404 不泄漏存在性 |
| 5 | GET `/runs/<run_id>/events` | `run_events` `:108` | afterSeq 增量（事实源 `ai_batch_events`） |

### 5.5 `open_api_row_actions.py`（1 端点，绑定键）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | POST `/collections/<c>/<record_id>/row-actions/<action_id>/run` | `run_row_action` `:57` | 集合公开+可写；role 取属主角色；`operator=api-key:<name>`；`RowActionError` 原样透传（中文）；无发现端点（actionId 线下获取，控制范围） |

### 5.6 `open_api_scan_tasks.py`（前缀 `/v1/ai-scan-tasks`，3 端点，绑定键）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `` | `list_tasks` `:46` | 属主任务清单（出参白名单 `_task_out :29`，不带 prompt 模板等内部配置） |
| 2 | GET `/<task_id>` | `get_task` `:55` | 单任务 |
| 3 | POST `/<task_id>/run-now` | `run_now` `:66` | 同步触发 `ai_scan_engine.run_task`；不检查 enabled（与内部一致）；返回 claimedCount/lastError |

### 5.7 `open_api_prompt_templates.py`（前缀 `/v1/prompt-templates`，5 端点，绑定键）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `` | `list_` `:56` | 属主模板清单（出参不带 user_id） |
| 2 | POST `` | `create` `:64` | name ≤200；重名 409 |
| 3 | GET `/<template_id>` | `get` `:83` | 单模板 |
| 4 | PUT `/<template_id>` | `update` `:94` | 整体替换 name+content |
| 5 | DELETE `/<template_id>` | `delete` `:115` | 204 |

### 5.8 `open_api_memories.py`（前缀 `/v1/memories`，3 端点，绑定键）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `` | `list_my_memories` `:34` | mem0 返回原样透传不裁剪 |
| 2 | POST `` | `add_my_memory` `:42` | ≤2000 字符；`verbatim` 原样保存否则 infer；底层未配置 409 `MEMORY_UNAVAILABLE` |
| 3 | DELETE `/<memory_id>` | `delete_my_memory` `:64` | **先 list 建属主 id 集合再删**（防 IDOR，§2.1） |

### 5.9 `ai_approvals.py`（前缀 `/v1/ai-approvals`，3 端点，`login_required`——JWT 例外，机制归分册 05）

| # | 方法与路径 | 端点（装饰器行） | 关键行为 |
| --- | --- | --- | --- |
| 1 | GET `` | `list_approvals` `:17` | admin 看全部；普通用户只看被点名待决 |
| 2 | POST `/<approval_id>/approve` | `approve` `:29` | `_decide('approved')`：点名权限判定、可带 `edits` 合并 run_input_snapshot、decision_hash 幂等、log_operation 审计 |
| 3 | POST `/<approval_id>/reject` | `reject` `:35` | 同上（rejected） |

## 6. 依赖与协作关系

- **上游依赖**：PostgreSQL（`api_keys`/`page_configs`/`dynamic_data`/`ai_chat_sessions`/`ai_chat_batches`/`ai_execution_commands`/`ai_delivery_outbox` 等）；`utils/batch_repo`+`batch_engine`（批与单会话执行，分册 03）；`utils/orchestration_defs/engine`（编排，分册 04）；`utils/ai_scan_repo/engine`（扫描，分册 07）；`utils/memory`（mem0，分册 09）；`utils/prompt_template`；`utils/row_action_engine`；`utils/opencode_client`（agents/models 发现）；`utils/ai_query`+`mongo_query`（NL 翻译）；`utils/webhook_engine`（HMAC，与 webhook 规则引擎共用）；`utils/workspace`（暂存/工作区/路径遏制）；`utils/agent_ledger`（actionChecks 校验，分册 05）；`utils/session_file_import`/`workspace_changes`/`workspace_outputs`（产出读取与导入）。
- **下游消费方**：外部集成系统（经 `proxy.py` `/api/v1/...`，文档面 `docs/user-guide/integration/`）；批 worker 是单会话/编排 run/批子任务的统一执行体（claim 条件三分支，`batch_engine.py:1133-1134`）；outbox 投递器消费批回调（分册 03 §4.7）；执行审计把 API Key 来源批标为 `open_api`（分册 06）；管理面 outbox 投递状态/重放在分册 03 的 admin 蓝图。
- **协作约束**：对外契约层只吐白名单字段、不复用内部处理器（内部字段外泄即成公开承诺）；`error` 字段保持字符串形状，`code`/`error_detail` 只做加法扩展（`api_errors.py:1-9`）；所有控制类写操作同步落命令账本，形成完整命令历史；error 文案三种惯例并存——数据面 `open-api.md` 英文、AI 能力面（批/单会话/扫描/记忆）中文、prompt-templates 英文（沿用内部路由）、row-actions 中文（透传引擎异常），各自在文件头注明理由。

## 7. 设计决策

1. **单会话 API 刻意只有「两个半」端点**：创建 + 查状态是完整的两个，cancel 只算半个（单会话没有批任务的 pause/resume/retry/append/命令平面——生命周期短，唯一必要的控制面是取消）。没有 list/delete/continue/retry：发现类信息复用批任务的 `/agents`/`/models`/`/skills`，文件上传复用 `/v1/ai-batches/uploads`——「文件落在哪个用户的暂存目录下」才是校验边界，与消费方无关（`open_api_ai_sessions.py:8-16`）。⚠️ 观测：模块 docstring `:8-10` 仍写「刻意只有两个端点——创建 + 查状态」，与实际三个端点（cancel 为后补）不同步，范围承诺以本节口径为准。
2. **对外契约层与内部路由物理分离**（`open_api_batches.py:1-7`）：内部返回体含 `opencode_session_id`/`workspace_path` 等实现细节，一旦对外即成公开承诺，日后为前端改字段会破坏外部集成——故只调 repo/engine 底层、只吐白名单字段（`_batch_out`/`_task_out`/`_template_out`/`_out_run` 各自的白名单函数）。
3. **绑定键是 AI 能力面的硬门槛**：存量未绑定密钥一律 403 要求重建，而非默认归属到某个用户——「以谁的名义跑」必须显式（`auth.py:213-225`）。密钥只存哈希、明文仅创建时返回一次；`owner_user_id` FK ON DELETE SET NULL 保证删用户不级联删密钥，失主密钥由 `require_bound_key` 统一挡下。
4. **幂等三档**：命令平面显式 `Idempotency-Key`（外部重试安全）、批内控制端点自然键派生（同请求者同类操作天然去重）、读侧 afterSeq/eventId 游标合并（保留期外显式 `CURSOR_EXPIRED` 让客户端重新同步而非静默丢帧）——三档都落到 `ai_execution_commands.idempotency_key` 唯一约束或事件表主键上，不靠应用内存。
5. **回调交付复用 webhook 引擎**：一套 `_fire_single_webhook`（HMAC-SHA256 签名头三件套）服务 webhook 规则与批回调两类消费方；outbox 同事务入队 + 退避死信把「回调端点挂了」从批执行路径上解耦（分册 03 §4.7、分册 11 §4.8）。`callbackSecret` 不回显是单向承诺：调用方自留密钥，平台只按它签名。
6. **memories 删除的 IDOR 闸门照抄内部路由**（`open_api_memories.py:1-15,69-74`）：`delete_memory` 不做属主过滤是 mem0 的形状，路由层必须先建属主 id 集合再删——「不能因为是新代码就漏掉」。同理行操作上下文独立复制而不 import 生产路由（改动风险 > 十行 SQL 复制，`open_api_row_actions.py:36-38`）。
7. **观测：`/v1/ai-orchestrations` 未挂 `require_bound_key`**（5 端点仅 `api_key_required`，§5.4）——与其余 AI 能力面（批/单会话/扫描/记忆/模板/行操作全部绑定键）不对称；未绑定密钥（`ownerUserId=NULL`）可创建 `requested_by=NULL` 的 run，且 `get_run` 的 `requested_by != owner` 比对对 NULL==NULL 通过、理论上存在跨失主密钥互见。属现状记录，与本册冲突处以代码为准；收紧属产品决策不在本册范围。
8. **`/v1/ai-approvals` 是前缀下的身份例外**：同在 `/v1` 命名空间但走 JWT——审批裁决是「人」的动作（admin 或被点名者），API Key 无审批资格；契约细节（decision_hash/edits/点名权限）归分册 05，本册仅做家族汇总。
9. **请求体上限必须双进程各判一次**（§4.7）：proxy 在 Flask 之前把 body 读进内存，只在 Flask 设限等于生产入口裸奔——数值收敛到零依赖的 `upload_limits.py` 单一真源；全局 `MAX_CONTENT_LENGTH` 被刻意否决以保住备份还原。

## 8. 代码索引

**路由（注册：`server/app.py:99-106`；`ai_approvals` 注册 `:521`；应用级请求体门 `:135-160`）**

- `server/routes/open_api.py` —— 蓝图 `:14`；`file_in_public_collection :34`；`enrich_file_urls :47`；`check_collection_public :61`；`validate_branch_id :72`；`get_request_branch_id :84`；`check_collection_writable :95`；`get_page_fields :106`；`validate_required_fields :114`；`get_primary_key_fields :131`；`check_primary_key_unique :141`；11 端点见 §5.1
- `server/routes/open_api_batches.py` —— 蓝图 `:39-40`；常量 `:43-46`；`_guard_request_body_size :49-81`；`_validate_staged_path :95`；`_sweep_stale_staging :115`；`_validate_files :193`；`_batch_out :218`；`_validate_callback_url :263`；`_resolve_child :680`；28 端点见 §5.2
- `server/routes/open_api_ai_sessions.py` —— 蓝图 `:28-29`；`_session_out :37`；3 端点见 §5.3
- `server/routes/open_api_orchestrations.py` —— 蓝图 `:18-19`；`_out_definition :28`；`_out_run :40`；5 端点见 §5.4；归属隔离 404 `:103-104,115-116`
- `server/routes/open_api_row_actions.py` —— 蓝图 `:25-26`；`_load_row_action_context :35`；端点见 §5.5
- `server/routes/open_api_scan_tasks.py` —— 蓝图 `:20-21`；`_task_out :29`；3 端点见 §5.6
- `server/routes/open_api_prompt_templates.py` —— 蓝图 `:25-26`；`_template_out :45`；5 端点见 §5.7
- `server/routes/open_api_memories.py` —— 蓝图 `:25-26`；3 端点见 §5.8（IDOR 闸门 `:69-74`）
- `server/routes/ai_approvals.py` —— 蓝图 `:13-14`；3 端点见 §5.9（`_decide :41-73`；契约归分册 05）
- `server/routes/api_keys.py` —— 密钥管理面：创建（明文仅一次 `:52`、owner 绑定 `:58`）、启停 `:75/:77`、删除 `:105/:107`

**鉴权与共享设施**

- `server/auth.py` —— `hash_api_key :167`；`api_key_required :172-210`；`require_bound_key :213-225`
- `server/utils/api_errors.py` —— `err :19`（`{error, code[, error_detail]}`）；`register_error_handlers :44`
- `server/utils/upload_limits.py` —— 上限常量 `:20-25`；前缀表 `:35-41`；`body_limit_for_path :44`
- `server/utils/operation_log.py` —— 共享 INSERT `:51`；`log_api_operation :113`
- `server/utils/ai_session_repo.py` —— `create_session :18`（prompt→continue_prompt、input_files）；`get_session_for_owner :53`（双归属）；`cancel_session :95`

**schema 与迁移**

- `server/db_schema/core.py:235-243` —— `api_keys` DDL
- `server/init_db.py:1579-1612` —— `owner_user_id` 幂等 ALTER + FK ON DELETE SET NULL
- `server/db_schema/ai_batches.py:31-33` —— `callback_url`/`callback_secret` 列；`:44-52` 会话批列
- `server/migrations/2026_09_23_harness_p1_durable_execution.py:108-131,154-177` —— `ai_execution_commands`（idempotency_key 唯一 `:126-127`）/`ai_delivery_outbox`
- `server/config.py:75` —— `OPEN_API_BRANCH`（默认 `main`）

**执行与交付（机制主描述在分册 03/04/07/09）**

- `server/utils/batch_engine.py` —— claim 三来源条件 `:1133-1134`；直发回调兜底 `_notify_callback :509-545`（outbox 让位判断 `:525-530`；payload `:537-543`）；attempt 来源判定 `:1440-1476`（归分册 06）
- `server/utils/delivery_outbox.py` —— `deliver_one :183`（复用 webhook HMAC）；退避/死信 `:22-23`（归分册 03 §4.7）
- `server/utils/webhook_engine.py` —— 签名头 `:326-335`；`_compute_signature :393-405`（HMAC-SHA256）
- `server/utils/execution_commands.py` —— `submit_command :22-65`（幂等键派生 `:31-39`）；`finish_command :68`
- `server/utils/orchestration_engine.py` —— `create_run :30`（归分册 04）
- `server/utils/session_admin_repo.py:25-32` —— 来源五源 CASE（含 api_batch，归分册 06）

**对外文档面（docs/user-guide/integration/）**

- `open-api.md`（数据面契约）/ `ai-batch-api.md`（批任务，含 `/api` 前缀与绑定键说明）/ `ai-session-api.md`（单会话「范围」节=「两个半端点」的对外承诺）/ `openapi.yaml` / `open-api-reference.html` / `ai-api-reference.html`
