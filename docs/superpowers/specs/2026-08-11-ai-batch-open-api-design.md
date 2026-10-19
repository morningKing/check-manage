# AI 批任务对外 API 设计

日期：2026-08-11
状态：已确认，待编写实施计划

## 1. 背景与目标

平台已有「AI 批任务」能力（`docs/superpowers/specs/2026-05-31-ai-chat-batch-tasks-design.md`）：选 N 个文件 + 1 个 prompt → N 个隔离的 AI 会话，限流 3 并发，结果落在各自的子会话里。目前只能通过前端「AI 助手」里的界面使用，走 JWT 鉴权。

本设计把这个能力**对外暴露为 API**，让外部系统能以编程方式提交批量 AI 处理任务并取回结果，并配套调用文档。

### 目标

1. 外部系统凭 API Key 提交一批文件 + 一个 prompt，得到 N 个 AI 处理结果。
2. 对外契约与内部实现解耦 —— 内部重构不破坏外部集成。
3. 密钥之间、以及密钥与 UI 任务之间严格隔离。

### 非目标

- 不做完成回调 webhook，不做 SSE 推送（只轮询）。
- 不返回完整对话（工具调用等内部实现细节不外泄）。
- 不支持从平台数据页记录构造输入（那是 AI 扫描任务的领域）。
- 不改批任务引擎本身的执行逻辑。

## 2. 方案选型

**采纳方案 A：独立蓝图 `/api/v1/ai-batches/*`，只复用底层函数。**

新建 `server/routes/open_api_batches.py`，与现有 `open_api_bp`（`/api/v1`，collections 语义）并列但分开。内部直接调 `batch_repo` / `batch_engine` 的函数，**不复用 `ai_chat_batches.py` 的路由处理器**。

已否决：

- **方案 B（加进现有 `open_api.py`）**：该文件已 400+ 行、全是 collections/branches/schema 语义与专用辅助函数，塞进 AI 批任务会让它同时承担两个领域。
- **方案 C（给 `ai_chat_batches.py` 的路由加双鉴权）**：最省代码但最危险。(1) 内部返回体（含 `opencode_session_id`、`workspace_path` 等实现细节）会变成公开契约，以后为前端改字段就会破坏外部集成；(2) 鉴权与归属校验分支会散落在 9 个处理器里，任何一个漏判就是越权口子 —— 与「严格按密钥隔离」的要求正面冲突。

选 A 的核心理由是**契约隔离**：对外 API 一旦发布就是承诺，必须能独立于内部实现演进。这层薄翻译层读 `batch_repo` 的数据、只吐出约定的字段，内部加字段/改名/换存储都影响不到它。附带收益：`api_key_id` 归属校验集中在一处不会漏判，「404 而非 403」的约定也统一实现。

## 3. 数据模型、鉴权与隔离

### 3.1 DDL 变更（两处，幂等迁移）

按 `server/init_db.py` 现有写法（查 `information_schema.columns`，缺列才 `ALTER TABLE`）：

1. **`api_keys` 加 `owner_user_id VARCHAR(100) REFERENCES users(id)`**（可空）。创建密钥时写入当前登录用户；存量密钥为 NULL。
2. **`ai_chat_batches` 加 `api_key_id VARCHAR(100) REFERENCES api_keys(id)`**（可空）。UI 建的批任务为 NULL；API 建的记下密钥 id。
3. 配套索引：`CREATE INDEX IF NOT EXISTS idx_ai_chat_batches_api_key ON ai_chat_batches(api_key_id, created_at DESC)`，支撑按密钥的列表查询。

### 3.2 鉴权扩展

`server/auth.py` 的 `api_key_required` 目前 SELECT `id, name, is_active`，设置 `g.api_key_info = {'id', 'name'}`。

改为一并取 `owner_user_id`，`g.api_key_info` 变为 `{'id', 'name', 'ownerUserId'}`。

**新增守卫**：`owner_user_id` 为 NULL 的密钥**不能**调用 AI 批任务接口，返回 403 +「该密钥未绑定用户，请在密钥管理中重新创建」。

这条的意义：存量密钥天然被挡在外面，**不会意外以某个人的名义跑 AI、烧他的额度、把任务塞进他的侧边栏**。该限制只作用于新的 AI 批任务接口，**现有 collections 接口完全不受影响**，老集成不会因此挂掉。

### 3.3 隔离

- 所有查询无条件带 `api_key_id = <当前密钥>` 条件。
- 命中不到一律 **404 而非 403** —— 不泄漏「这个 id 存在但不属于你」。
- UI 建的批任务 `api_key_id IS NULL`，永远不会被 API 命中。

### 3.4 密钥管理页的配合改动

`server/routes/api_keys.py` 的 `create_api_key` 在 INSERT 时写入 `owner_user_id = g.current_user['userId']`。密钥列表可展示归属用户名（可选，不强制）。

## 4. 端点契约

统一前缀 `/api/v1/ai-batches`，鉴权 `X-API-Key`，请求/响应皆 JSON（上传除外）。

### 4.1 `POST /uploads` — 上传输入文件

`multipart/form-data`，字段名 `files`，**一次可传多个**（内部 `staging_upload` 是一次一个，外部这样对集成方更友好）。服务端自行生成 upload session id，调用方无需知道。

```json
→ 201 {
  "files": [
    { "name": "订单A.pdf", "path": "batch-staging/user-x/8f3a2c1e/订单A.pdf" }
  ]
}
```

`path` 对调用方是**不透明串**，原样回传给创建接口即可。

文件名经 `workspace.safe_filename` 处理（保留中文）。暂存目录为 `batch-staging/<ownerUserId>/<uploadSessionId>/`（`workspace.batch_staging_dir`，本身已做路径穿越防护）。

### 4.2 `POST /` — 创建批任务

```json
{
  "name": "批量审核",
  "prompt": "阅读文档并给出审核意见",
  "files": [ { "name": "订单A.pdf", "path": "batch-staging/..." } ],
  "agent": "build",
  "model": "anthropic/claude-sonnet-4"
}
→ 201 { "batchId": "...", "status": "pending", "total": 3 }
```

`name`、`prompt`、`files` 必填；`agent`、`model` 可选，空则用全局默认。创建后调 `batch_engine.get_worker().notify()` 让 worker 立即取活。

### 4.3 `GET /` — 列出本密钥创建的批任务

查询参数 `page`（默认 1）、`pageSize`（默认 20，上限 100）。

```json
→ 200 {
  "items": [ { "batchId": "...", "name": "...", "status": "completed",
               "total": 3, "done": 3, "failed": 0,
               "createdAt": "...", "completedAt": "..." } ],
  "total": 12
}
```

### 4.4 `GET /<batchId>` — 状态与进度

返回单条，形状同列表项，额外含 `agent`、`model`。

`status` 取值：`pending | running | completed | partial | failed`（沿用 `ai_chat_batches.status` 的 CHECK 约束）。

### 4.5 `GET /<batchId>/results` — 取结果

```json
→ 200 {
  "batchId": "...",
  "status": "completed",
  "results": [
    { "name": "订单A.pdf", "status": "completed", "output": "……完整回复文本……", "error": null },
    { "name": "订单B.pdf", "status": "failed", "output": null, "error": "……" }
  ]
}
```

**`output` 必须从 `ai_chat_messages` 取最后一条助手消息的文本**，不能读 `ai_chat_sessions.last_message_preview` —— 后者存的是 `batch_engine._preview_from(m)` 取的**第一行**，读它会让调用方拿到被截断的输出。

未跑完的子任务 `output` 与 `error` 均为 `null`，`status` 反映其真实状态。

`name` 取子会话 `batch_input_file` 的**basename**。注意 `batch_input_file` 存的是 `create_batch` 收到的 `f['path']`，即**工作区相对路径**（如 `batch-staging/user-x/8f3a/订单A.pdf`）而非文件名 —— 直接返回它会泄漏内部 userId 与目录结构，所以必须只取最后一段。

### 4.6 `DELETE /<batchId>` — 删除批任务

复用 `batch_repo` 的删除逻辑（含子会话与工作区清理）。不可逆。

### 4.7 `POST /<batchId>/retry-failed` — 重试失败的子任务

```json
→ 200 { "retried": 2 }
```

把该批下 `failed` 的子会话重置为 `pending` 交给 worker。

**终态定义**：`completed` / `partial` / `failed` 三者为终态，可重试；`pending` / `running` 为非终态，返回 409「该批任务仍在执行中」。

批任务处于终态但 `failed == 0`（没有可重试的子任务）时返回 200 + `{"retried": 0}`，不报错。

## 5. 安全：文件路径归属校验

**这是本功能对外后唯一新增的横向越权面，必须处理。**

内部创建接口**不校验** `files[].path` 的归属 —— 它在 JWT 网关之后，路径由前端从上传响应原样带回，天然限定在该用户目录下。

对外不成立：恶意调用方可直接构造 `path`，例如 `batch-staging/<别人的userId>/.../机密.pdf`，或用 `../` 往上跳，从而把别人工作区的文件塞进自己的批任务让 AI 读出来。

**要求**：外部创建接口对每个 `files[].path` 做校验 —— 规范化（解析 `..`、统一分隔符）后必须落在 `batch-staging/<本密钥的 ownerUserId>/` 之下，否则返回 400「文件路径无效」（不解释具体原因，避免被用来探测目录结构）。

## 6. 限制

| 项 | 值 | 说明 |
|---|---|---|
| 单批文件数 | 50 | 沿用既有 `batch_repo.MAX_FILES_PER_BATCH` |
| 单次上传总大小 | 100 MB | **新增** —— 目前全项目无 `MAX_CONTENT_LENGTH`，上传无界，对外必须封顶 |
| 单个文件大小 | 20 MB | 新增 |
| `prompt` 长度 | 20000 字符 | 新增，防止超长上下文被塞进每个子任务 |
| `pageSize` | ≤ 100 | 与内部列表接口一致 |

超限一律返回 400，错误信息中**带上具体上限值**（便于集成方自查）。

**各限制的强制位置**（避免实现时理解不一）：

| 限制 | 在哪强制 |
|---|---|
| 单个文件 20 MB、单次上传总计 100 MB | **上传接口**（`POST /uploads`），超限直接拒绝、不落盘 |
| 单批文件数 50 | **创建接口**（`POST /`），因为一次批任务的文件可以分多次上传后一起提交 |
| `prompt` 20000 字符 | 创建接口 |
| `pageSize` ≤ 100 | 列表接口（超出则截断为 100，不报错，与内部列表接口行为一致） |

## 7. 错误处理

| 情况 | 响应 |
|---|---|
| 缺 / 无效 / 已吊销的 API Key | 401（沿用 `api_key_required` 现有行为） |
| 密钥未绑定用户（`owner_user_id` 为 NULL） | 403「该密钥未绑定用户，请在密钥管理中重新创建」 |
| `files[].path` 不在本密钥属主的暂存目录下 | 400「文件路径无效」 |
| 文件数 / 大小 / prompt 超限 | 400 + 具体上限值 |
| `name` / `prompt` / `files` 缺失 | 400 + 中文说明 |
| `batchId` 不存在**或不属于本密钥** | 404（不用 403，不泄漏存在性） |
| 对非终态批任务调 `retry-failed` | 409「该批任务仍在执行中」 |

## 8. 实现边界

**新文件** `server/routes/open_api_batches.py`，蓝图 `open_api_batches_bp`（`url_prefix='/api/v1/ai-batches'`），在 `server/app.py` 注册 —— **必须在 `dynamic_bp` 之前**（那个 catch-all 永远最后注册）。

**复用的底层**：`batch_repo.create_batch / list_batches / get_batch / delete_batch / retry_failed`、`workspace.batch_staging_dir` / `safe_filename`、`batch_engine.get_worker().notify()`。

**唯一新增的底层函数**：`batch_repo.get_batch_results(batch_id)` —— 联表取每个子会话的 `batch_input_file` / `status` / `error_message`，外加从 `ai_chat_messages` 取最后一条助手消息的文本。放在 `batch_repo` 而非路由层，因为它是数据访问逻辑、将来可能有其他消费方。

**`create_batch` 的签名扩展**：现有签名是 `create_batch(user_id, *, name, prompt, template_id, files, scan_task_id=None, agent=None, model=None, provision_repo=None, provision_ref=None)`。新增一个**带默认值的关键字参数** `api_key_id: str | None = None`，写入新列。默认 `None` 保证所有现有调用点（UI 创建、AI 扫描任务、行操作按钮）行为不变、无需改动。

### 8.1 暂存文件的孤儿清理

上传后如果调用方**从不创建批任务**，暂存目录会永久堆积（内部流程也有这个问题，但 UI 用户量可控；对外后是任何持 key 的人都能无限制刷）。

处理方式：`POST /uploads` 每次调用时，顺带清理**同一属主**（`batch-staging/<ownerUserId>/` 下）**超过 24 小时未被使用**的暂存目录。选择「顺带清理」而不是加一个后台调度器，是因为它零新增组件、且触发频率与滥用程度天然正相关 —— 刷得越狠、清理得越勤。

已被 `create_batch` 消费掉的暂存目录由既有的批任务流程负责迁移/清理，本机制不碰它们（判据是目录 mtime，已迁移的目录本就不在了）。

**不改**：批任务引擎的执行逻辑、内部 `ai_chat_batches.py` 的任何路由。

## 9. 测试与验证

### 9.1 后端测试 `server/tests/test_open_api_batches.py`

- **鉴权三态**：无 key → 401；无效/已吊销 key → 401；`owner_user_id` 为 NULL 的 key → 403。
- **路径越权**：构造指向别的 userId 目录的 path → 400；含 `..` 的 path → 400；合法 path → 通过。
- **隔离**：A 密钥查不到 B 密钥的批任务 → 404；查不到 UI 建的（`api_key_id IS NULL`）→ 404；删除、retry-failed 同样受限。
- **限制边界**：文件数 50/51；prompt 20000/20001 字符；文件大小与总大小。
- **`get_batch_results` 取的是完整文本而非 preview 首行** —— 造一条多行回复，断言返回的 `output` 含第二行。
- **retry-failed 的状态门**：非终态 → 409。

### 9.2 `proxy.py` 验证（强制）

这是**新增的 `/api` 路由形状**，按 CLAUDE.md 必须过一遍 `:8080`。额外风险点：**multipart 上传经过 `proxy.py` 转发** —— `_proxy_to_backend` 按 `Content-Length` 转发 body，需实测确认大文件 multipart 不被截断、不缓冲爆内存。

`X-API-Key` 已在 `proxy.py` 的转发头白名单与 CORS 白名单内，**不需要改**。本功能**没有 SSE**，`_is_sse()` 不用动。

### 9.3 不需要 Playwright

纯后端 API，无 UI。但密钥管理页因为要写 `owner_user_id`，创建密钥的流程需实跑确认一次（可用 Playwright 或直接调接口 + 查库）。

## 10. 文档

- **新增 `docs/user-guide/integration/ai-batch-api.md`**，按现有 `open-api.md` 的风格：完整 curl 示例、一个从上传到取结果的端到端流程、错误码表、限制表、以及「密钥必须绑定用户」的说明。
- 从 `docs/user-guide/README.md` 的 `integration/` 分组索引。
- `docs/user-guide/integration/open-api.md` 补一句指向新文档（说明 `/api/v1` 下除 collections 外还有 AI 批任务）。
- `CLAUDE.md` 补一节描述该对外接口与隔离模型。

## 11. 涉及文件清单

**新增**
- `server/routes/open_api_batches.py`
- `server/tests/test_open_api_batches.py`
- `docs/user-guide/integration/ai-batch-api.md`

**修改**
- `server/init_db.py`（两处列迁移 + 一个索引）
- `server/auth.py`（`api_key_required` 取 `owner_user_id` 并放进 `g.api_key_info`）
- `server/routes/api_keys.py`（创建密钥时写 `owner_user_id`）
- `server/utils/batch_repo.py`（新增 `get_batch_results`；`create_batch` 接受并写入 `api_key_id`）
- `server/app.py`（注册蓝图）
- `docs/user-guide/README.md`、`docs/user-guide/integration/open-api.md`、`CLAUDE.md`

## 12. 实施注意

当前 `main` 已包含行操作与设置扁平化两个功能（`5d2c051`）。本改造从 `main` 另开分支。
