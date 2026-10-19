# 设计方案：AI 批任务对外 API — 会话详情 & 继续对话

**日期**：2026-08-15
**状态**：Draft

---

## 1. 背景与目标

外部系统通过 `/api/v1/ai-batches` 已可创建/轮询/取结果/追加/重试批任务，但以下三个能力缺失：

| # | 需求 | 当前状态 |
|---|------|---------|
| R1 | **获取会话结果（完整对话 + 工具调用）** | 仅返回最后一条 assistant 文本拼接，无中间消息、无 tool-call |
| R2 | **获取会话产出文件（列表 + 下载）** | 仅有 `file-records`（变更记录元数据），无实时文件列表/下载 |
| R3 | **在已有会话上继续对话** | 完全不支持，只能 `retry-failed`（全量重跑）或 `reexecute`（删旧消息重跑） |

目标：新增 5 个对外端点，复用已有内部工具层，遵循现有隔离模型（密钥级），不泄漏内部实现细节。

---

## 2. 新增端点概览

| # | 方法 | 路径 | 用途 | 对应需求 |
|---|------|------|------|---------|
| N1 | GET | `/{batchId}/sessions/{name\|seq}/messages` | 取完整对话 | R1 |
| N2 | GET | `/{batchId}/sessions/{name\|seq}/files` | 实时文件列表 | R2 |
| N3 | GET | `/{batchId}/sessions/{name\|seq}/files/download?path=...` | 下载单个文件 | R2 |
| N4 | GET | `/{batchId}/sessions/{name\|seq}/files/download-all` | 打包下载所有新增/修改文件（ZIP） | R2 |
| N5 | POST | `/{batchId}/sessions/{name\|seq}/continue` | 继续对话 | R3 |

子会话定位：URL 中 `{name|seq}` 为子会话标识，与现有 `_resolve_child` 模式一致——通过文件名（`batch_input_file` 的 basename）或序号（`batch_seq`）定位。

---

## 3. 端点详细设计

### 3.1 GET `/{batchId}/sessions/{childId}/messages`

**用途**：获取某个子会话的完整对话历史。

**子会话定位规则**：
- `childId` 为纯数字时按 `batch_seq` 匹配
- 否则按 `batch_input_file` 的 basename 匹配
- 404 找不到时统一返回 404（不区分不存在 vs 无权限）

**响应 — 200**

```json
{
  "batchId": "b1c2d3e4-...",
  "child": { "name": "report1.pdf", "seq": 1, "status": "completed" },
  "messages": [
    {
      "id": "msg-001",
      "role": "user",
      "content": [{ "type": "text", "text": "请总结这份报告..." }],
      "createdAt": "2026-08-10T03:20:00.000Z"
    },
    {
      "id": "msg-002",
      "role": "assistant",
      "content": [
        { "type": "text", "text": "报告核心结论如下..." },
        { "type": "tool_use", "id": "tu-1", "name": "read", "input": { "path": "uploads/report1.pdf" } }
      ],
      "createdAt": "2026-08-10T03:21:00.000Z"
    }
  ],
  "total": 12,
  "truncated": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `child.name` | string | 子会话文件名 |
| `child.seq` | integer | 子会话序号 |
| `child.status` | string | 子会话状态 |
| `messages[].id` | string | 消息 ID |
| `messages[].role` | string | `user` / `assistant` |
| `messages[].content` | array | 内容片段数组，每项含 `type`（`text` / `tool_use`） |
| `messages[].createdAt` | string | ISO 8601 |
| `total` | integer | 消息总数 |
| `truncated` | boolean | 是否被截断（上限 500 条） |

**`content` 数组的 `type` 取值**：

| type | 字段 | 说明 |
|------|------|------|
| `text` | `text` | 文本内容 |
| `tool_use` | `id`, `name`, `input` | 工具调用（`input` 为 JSON 对象） |

> **刻意不返回**：`opencode_session_id`、`workspace_path`、`batch_id`、`user_id` 等内部实现字段。

**错误响应**：404（批任务不存在/不属于本密钥，或子会话定位未命中）。

---

### 3.2 GET `/{batchId}/sessions/{childId}/files`

**用途**：实时扫描子会话工作区，返回当前文件列表（非变更记录）。

**响应 — 200**

```json
{
  "batchId": "b1c2d3e4-...",
  "child": { "name": "report1.pdf", "seq": 1, "status": "completed" },
  "files": [
    {
      "name": "report1.pdf",
      "path": "uploads/report1.pdf",
      "dir": "uploads",
      "size": 102400,
      "modifiedAt": "2026-08-10T03:20:00.000Z"
    },
    {
      "name": "analysis.md",
      "path": "output/analysis.md",
      "dir": "outputs",
      "size": 5120,
      "modifiedAt": "2026-08-10T03:23:00.000Z"
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `files[].name` | string | 文件名 |
| `files[].path` | string | 工作区相对路径（用于 `/files/download` 的 `path` 参数） |
| `files[].dir` | string | 分类：`uploads` / `outputs` / `workspace` |
| `files[].size` | integer | 字节数 |
| `files[].modifiedAt` | string \| null | 最后修改时间 |

**实现**：复用 `workspace_outputs.list_session_files(workspace_path)`，已在内部端点使用。

**错误响应**：404（同上）。

---

### 3.3 GET `/{batchId}/sessions/{childId}/files/download`

**用途**：下载子会话工作区中的单个文件。

**查询参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 工作区相对路径（来自 `/files` 响应的 `files[].path`） |

**响应 — 200**：文件流（`Content-Disposition: attachment`）。

**安全约束**：
- `path` 必须经过路径规范化，禁止 `..` 穿越
- `path` 必须落在该子会话的工作区目录内（通过 `os.path.commonpath` 校验）
- 非法路径返回 400

**错误响应**：400（路径非法）、404（批任务/子会话不存在，或文件不存在）。

---

### 3.4 GET `/{batchId}/sessions/{childId}/files/download-all`

**用途**：将子会话工作区中所有**新增和修改**的文件打包为 ZIP 下载。

**筛选逻辑**：通过 `file-records`（`ai_chat_session_files` 表）获取该子会话被记录的变更文件，仅打包 `status` 为 `added` 或 `modified` 的文件。如果没有任何变更记录，返回空 ZIP。

**响应 — 200**：ZIP 文件流。

```
Content-Type: application/zip
Content-Disposition: attachment; filename="session-report1.zip"
```

ZIP 内部目录结构：
```
session-report1/
  output/
    analysis.md
  data/
    results.csv
```

- 根目录名为 `session-{childName}`（去掉扩展名，截断至 50 字符）
- 保持文件在工作区中的相对路径结构
- 跳过不存在的文件（不报错，日志记录）

**查询参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `include` | string | 否 | `added,modified` | 筛选文件状态，逗号分隔。可选值：`added`、`modified` |

**实现**：
1. 通过 `file_repo.get_session_file_records(session_id)` 获取变更记录
2. 按 `include` 参数筛选
3. 逐个检查文件是否存在于工作区
4. 用 `io.BytesIO` + `zipfile` 在内存中构建 ZIP
5. 单文件过大（>50MB）或 ZIP 总大小预估过大（>200MB）时跳过并记录

**错误响应**：404（批任务/子会话不存在）。

---

### 3.5 POST `/{batchId}/sessions/{childId}/continue`

**用途**：在已完成/失败的子会话上追加一轮对话，保留历史上下文。

**请求体**

```json
{
  "prompt": "请进一步分析风险因素，给出具体的改进建议。",
  "agent": "",
  "model": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 追加的提示词，最长 20000 字符 |
| `agent` | string | 否 | 覆盖 agent（空串/不传 = 使用批任务原有配置） |
| `model` | string | 否 | 覆盖模型（空串/不传 = 使用批任务原有配置） |

**响应 — 202**

```json
{
  "batchId": "b1c2d3e4-...",
  "child": { "name": "report1.pdf", "seq": 1 },
  "status": "running"
}
```

**执行流程**：

1. 校验批任务归属（`api_key_id`）& 子会话定位
2. 校验子会话状态为终态（`completed` / `failed`），非终态返回 409
3. **复用现有 OpenCode 会话**：不清除 `opencode_session_id`，不删除 `ai_chat_messages`
4. 通过 `batch_repo.continue_child()` 将子会话状态重置为 `pending`，调整批任务计数器
5. 返回 202（异步），后台 worker 拾取后检测到 `opencode_session_id` 已存在，跳过 `create_session`，直接调用 `send_prompt_async` 发送新 prompt
6. 新消息追加到已有对话末尾

**与 `retry-failed` / `reexecute` 的区别**：

| 操作 | 历史消息 | OpenCode 会话 | prompt |
|------|---------|--------------|--------|
| `retry-failed` | 保留（仅 failed 子任务） | **新建** | 原始 prompt |
| `reexecute` | **删除** | **新建** | 原始 prompt |
| **`continue`** | **保留** | **复用** | **新 prompt** |

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `prompt` 缺失或超过 20000 字符 |
| 404 | 批任务不存在/不属于本密钥，或子会话未找到 |
| 409 | 子会话不在终态（`pending` / `running`） |

---

## 4. 后端改动点

### 4.1 `server/routes/open_api_batches.py`

新增 4 个路由处理函数，遵循现有模式：
- 通过 `api_key_id` 隔离（复用 `_fetch_batch` helper）
- 子会话定位复用 `_resolve_child` 模式
- 返回体自行构造，不泄漏内部字段

### 4.2 `server/utils/batch_repo.py`

新增 `continue_child(user_id, batch_id, session_id, prompt)` 函数：
- 校验终态（同 `reexecute_child`）
- **不**删除 `ai_chat_messages`
- **不**清除 `opencode_session_id`
- 将 `status` 设为 `pending`，存储 `continue_prompt`（新字段或复用 `batch_input_hint`）
- 调整批任务 `done`/`failed` 计数器

### 4.3 `server/utils/batch_engine.py`

修改 `_run_one` 方法：
- 检测 `opencode_session_id` 是否已存在
- 若已存在：跳过 `create_session`，跳过 `_prepare_workspace`（工作区已有），跳过 `_persist_user_prompt`（由 continue 逻辑预写），直接 `send_prompt_async` + `_await_finished` + `_persist_conversation`
- 若不存在：走现有流程（全新会话）

新增 `_persist_continue_prompt(session_id, prompt)` 函数，写入 user 消息到 `ai_chat_messages`。

### 4.4 数据库

`ai_chat_sessions` 表新增 `continue_prompt` TEXT 列（nullable），用于暂存 continue 请求的 prompt，worker 拾取后读取并发送。

---

## 5. 安全模型

| 维度 | 策略 |
|------|------|
| 批任务归属 | `api_key_id` 匹配（现有机制） |
| 子会话定位 | `name`/`seq` 在已加载的 sessions 列表中线性查找（现有模式） |
| 文件下载 | 路径规范化 + `commonpath` 校验，禁止 `..` 穿越 |
| 消息截断 | 上限 500 条，`truncated: true` 标记 |
| 内部字段隔离 | 不返回 `opencode_session_id`、`workspace_path`、`user_id` 等 |
| 速率限制 | 沿用现有 API 速率限制（如有）；`continue` 受 worker 并发（3）自然限流 |

---

## 6. 外部调用示例

### 6.1 取完整对话

```bash
# 定位子会话 by name
curl -s -H "X-API-Key: $API_KEY" \
  "$BASE_URL/$BATCH_ID/sessions/report1.pdf/messages" | jq

# 定位子会话 by seq
curl -s -H "X-API-Key: $API_KEY" \
  "$BASE_URL/$BATCH_ID/sessions/1/messages" | jq
```

### 6.2 列出并下载产出文件

```bash
# 列出文件
curl -s -H "X-API-Key: $API_KEY" \
  "$BASE_URL/$BATCH_ID/sessions/report1.pdf/files" | jq

# 下载单个文件
curl -s -H "X-API-Key: $API_KEY" \
  "$BASE_URL/$BATCH_ID/sessions/report1.pdf/files/download?path=output/analysis.md" \
  -o analysis.md

# 打包下载所有新增/修改文件
curl -s -H "X-API-Key: $API_KEY" \
  "$BASE_URL/$BATCH_ID/sessions/report1.pdf/files/download-all" \
  -o session-report1.zip
```

### 6.3 继续对话

```bash
curl -s -X POST \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "请进一步分析风险因素，给出具体的改进建议。"}' \
  "$BASE_URL/$BATCH_ID/sessions/report1.pdf/continue" | jq

# 轮询等待完成（复用现有 /results 或新增的 /messages 端点）
```

---

## 7. 关键设计决策

### D1：子会话定位方式（URL 路径 vs 查询参数）

**选择**：URL 路径 `/sessions/{childId}`。

理由：
- 与内部 admin 端点 `/sessions/<session_id>/...` 的 URL 结构一致（虽然标识不同）
- RESTful 语义更清晰
- `childId` 纯数字 → `seq`，否则 → `name`，无歧义

### D2：`continue` 的状态管理

**选择**：`continue` 将子会话状态重置为 `pending`，由 worker 异步执行。

理由：
- 与现有 `retry-failed` / `reexecute` 的执行模型一致
- worker 已有并发控制（3）、超时保护（30 分钟）、错误处理
- 避免在 API 路由中直接调用 `send_prompt_async`（需要自己管理 SSE 监听、状态更新、计数器调整）

### D3：是否暴露 `opencode_session_id`

**选择**：不暴露。

理由：
- 这是内部实现细节，外部系统不应依赖
- 暴露后可能被用于绕过本 API 直接调用 OpenCode
- 子会话通过 `name`/`seq` 定位已足够

### D4：`continue` 是否支持 `pending`/`running` 状态

**选择**：不支持，仅限终态。

理由：
- `running` 状态的会话正在被 worker 处理，此时发 `continue` 会导致与 worker 的 SSE/REST 持久化冲突
- `pending` 状态尚未创建 OpenCode 会话，无法"继续"
- 外部系统可以等终态后再 `continue`

---

## 8. 与现有端点的关系

```
外部系统视角：

上传 → 创建 → 轮询 → 取结果（文本）     ← 现有流程
                  ↓
             终态达成后
                  ↓
         ┌────────┼────────┐
         ↓        ↓        ↓
    取对话      取文件    继续对话      ← 新增能力
   /messages   /files    /continue
                ↓
            /files/download             ← 新增能力
```

新增端点不改变现有端点的行为，是纯粹的增量。

---

## 9. 后续可选扩展（不在本次范围）

| 扩展 | 说明 |
|------|------|
| SSE 推送 | 为 `/continue` 的结果提供实时推送（需在 `proxy.py` 中注册 SSE 路径） |
| 批量 continue | 一次对多个子会话发送相同的 continue prompt |
| 文件预览 | `GET /sessions/{childId}/files/preview?path=...`（返回文本内容而非下载） |
| 会话级 prompt 模板 | 允许为单个子会话配置独立的 system prompt |
