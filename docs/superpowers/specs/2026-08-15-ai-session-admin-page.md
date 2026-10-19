# AI 会话管理页 — 设计方案

**日期**：2026-08-15
**状态**：Draft

---

## 1. 背景与目标

当前 AI 会话管理分散在两处：

| 页面 | 覆盖范围 | 问题 |
|------|---------|------|
| AI 助手侧边栏 | 当前用户的交互式会话 | 只能看到自己的，无法看别人的 |
| AI 批任务管理 (`/admin/ai-batches`) | 批任务维度 | 按批任务聚合，无法按会话维度筛选/搜索 |

**目标**：新建一个管理页 `/admin/ai-sessions`，以**会话**为维度统一管理所有类型的 AI 会话，支持：

| 能力 | 说明 |
|------|------|
| 统一列表 | 所有会话（交互式 / 批任务子会话 / API 批任务子会话 / 定时扫描 / 客服） |
| 类型筛选 | 按来源类型（regular / batch / api_batch / scan / kefu）过滤 |
| 会话 ID 可见 | 每行展示 session ID，方便与 API 日志/数据库对齐 |
| 执行轨迹 | 查看完整对话（含工具调用、子代理委派） |
| 输入/输出文件 | 查看会话工作区的文件列表、预览、下载 |
| 基本操作 | 归档、重执行（批任务子会话） |

---

## 2. 新增端点

### 2.1 后端 API

| # | 方法 | 路径 | 用途 |
|---|------|------|------|
| E1 | GET | `/ai/chat/admin/sessions` | 统一会话列表（分页 + 筛选） |
| E2 | GET | `/ai/chat/admin/sessions/<sid>` | 单个会话详情 |
| E3 | GET | `/ai/chat/admin/sessions/<sid>/messages` | 会话对话历史 |
| E4 | GET | `/ai/chat/admin/sessions/<sid>/files` | 会话工作区文件列表 |
| E5 | GET | `/ai/chat/admin/sessions/<sid>/files/download` | 下载文件 |
| E6 | POST | `/ai/chat/admin/sessions/<sid>/archive` | 归档（已有，复用） |

> **权限**：全部要求 `admin.ai_chat_admin`。
>
> **与现有端点的关系**：E1 替换现有的 `GET /ai/chat/admin/sessions`（仅返回非批任务会话、无分页、无筛选）。E3 对批任务子会话和普通会话统一处理——现有 `GET /admin/batches/<bid>/sessions/<sid>/messages` 保留不变（按 batch 路径访问仍可用），新增的 E3 直接用 session ID 访问，不依赖 batch ID。

### 2.2 E1 统一会话列表 — 请求/响应

**请求参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | int | 1 | 页码 |
| `pageSize` | int | 20 | 每页条数，最大 100 |
| `status` | string | — | 按状态筛选：`active` / `closed` / `deleted` / `archived` / `pending` / `running` / `completed` / `failed` |
| `sourceType` | string | — | 按来源类型：`regular` / `batch` / `api_batch` / `scan` / `kefu` |
| `owner` | string | — | 用户名模糊搜索 |
| `keyword` | string | — | 会话标题 / 输入文件名 / 最后消息模糊搜索 |
| `batchId` | string | — | 按批任务 ID 筛选（查看某批任务下所有子会话） |

**响应**

```json
{
  "items": [
    {
      "id": "sess-abc123",
      "userId": "user-42",
      "username": "admin",
      "title": "帮我分析这份报告",
      "status": "active",
      "sourceType": "regular",
      "batchId": null,
      "batchName": null,
      "scanTaskId": null,
      "scanTaskName": null,
      "inputFile": null,
      "lastMessagePreview": "报告核心结论如下...",
      "errorMessage": null,
      "createdAt": "2026-08-10T03:20:00.000Z",
      "lastActiveAt": "2026-08-10T03:25:00.000Z",
      "opencodeSessionId": "oc-xxx"
    },
    {
      "id": "sess-def456",
      "userId": "user-42",
      "username": "admin",
      "title": null,
      "status": "completed",
      "sourceType": "api_batch",
      "batchId": "b-1",
      "batchName": "季度报告分析",
      "scanTaskId": null,
      "scanTaskName": null,
      "inputFile": "batch-staging/u-42/up/report.pdf",
      "lastMessagePreview": "分析完成...",
      "errorMessage": null,
      "createdAt": "2026-08-10T03:20:00.000Z",
      "lastActiveAt": "2026-08-10T03:24:00.000Z",
      "opencodeSessionId": "oc-yyy"
    }
  ],
  "total": 1234
}
```

### 2.3 E2 单会话详情

返回单个会话的完整信息，包含 `workspace_path`、`opencode_session_id` 等内部字段（admin 上下文，可以暴露）。

### 2.4 E3 会话对话历史

复用现有 `admin_get_child_messages` 的逻辑，但去掉 batch_id 校验——直接按 session_id 查询。

```json
{
  "messages": [
    {
      "id": "msg-001",
      "role": "user",
      "content": [{ "type": "text", "text": "请总结这份报告" }],
      "createdAt": "2026-08-10T03:20:00.000Z",
      "meta": null
    },
    {
      "id": "msg-002",
      "role": "assistant",
      "content": [
        { "type": "text", "text": "报告核心结论如下..." },
        { "type": "tool_use", "id": "tu-1", "name": "read", "input": { "path": "uploads/report.pdf" } }
      ],
      "createdAt": "2026-08-10T03:21:00.000Z",
      "meta": { "model": "mimo-v2.5-pro", "tokens": 1234, "duration": 5200 }
    }
  ],
  "total": 12,
  "truncated": false
}
```

### 2.5 E4 会话文件列表

复用 `workspace_outputs.list_session_files` + `augment_with_data_file_id`。

```json
{
  "files": [
    { "name": "report.pdf", "path": "uploads/report.pdf", "dir": "uploads", "size": 102400 },
    { "name": "result.md", "path": "output/result.md", "dir": "outputs", "size": 5120, "dataFileId": "df-xxx" }
  ]
}
```

---

## 3. 数据库

### 3.1 新增索引

统一分页查询需要按 `source_type` + `status` + `created_at` 排序/筛选，当前索引不覆盖。新增一个复合索引：

```sql
CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_admin_list
  ON ai_chat_sessions(status, created_at DESC, batch_id, scan_task_id, kefu_instance_id);
```

此索引支撑 `WHERE status = ? ORDER BY created_at DESC` 以及 source_type 的 FK 判空筛选。

### 3.2 SQL：统一分页查询

```sql
SELECT s.id, s.user_id, s.title, s.status, s.created_at, s.last_active_at,
       s.batch_id, s.batch_input_file, s.error_message, s.last_message_preview,
       s.scan_task_id, s.opencode_session_id,
       u.username,
       b.name AS batch_name,
       CASE
         WHEN s.kefu_instance_id IS NOT NULL THEN 'kefu'
         WHEN s.scan_task_id IS NOT NULL THEN 'scan'
         WHEN s.batch_id IS NOT NULL AND b.api_key_id IS NOT NULL THEN 'api_batch'
         WHEN s.batch_id IS NOT NULL THEN 'batch'
         ELSE 'regular'
       END AS source_type
FROM ai_chat_sessions s
LEFT JOIN users u ON u.id = s.user_id
LEFT JOIN ai_chat_batches b ON b.id = s.batch_id
WHERE 1=1
  AND s.status = $status           -- 可选
  AND source_type = $sourceType    -- 可选（用 CASE WHEN 表达式过滤）
  AND u.username ILIKE $owner      -- 可选
  AND (s.title ILIKE $kw OR s.batch_input_file ILIKE $kw
       OR s.last_message_preview ILIKE $kw)  -- 可选
  AND s.batch_id = $batchId        -- 可选
ORDER BY s.created_at DESC
LIMIT $pageSize OFFSET $offset;
```

---

## 4. 前端

### 4.1 页面结构

```
/admin/ai-sessions
┌─────────────────────────────────────────────────────────────────┐
│ 筛选栏                                                           │
│ [来源类型 ▾] [状态 ▾] [用户名] [关键词] [批任务ID] [查询] [刷新] │
├─────────────────────────────────────────────────────────────────┤
│ 会话列表（el-table）                                              │
│ ┌─────┬────────┬────────┬──────┬──────┬────────┬───────┬─────┐ │
│ │ ID  │ 用户   │ 来源   │ 状态 │ 标题 │ 批任务 │ 时间  │ 操作│ │
│ ├─────┼────────┼────────┼──────┼──────┼────────┼───────┼─────┤ │
│ │ ab… │ admin  │ 交互式 │ 活跃 │ 帮… │ —      │ 08-10 │ 📄📂│ │
│ │ de… │ admin  │ API批  │ 完成 │ —   │ 季度…  │ 08-10 │ 📄📂│ │
│ └─────┴────────┴────────┴──────┴──────┴────────┴───────┴─────┘ │
│ [分页]                                                           │
├─────────────────────────────────────────────────────────────────┤
│ 会话详情（el-drawer，点击行打开）                                  │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ [基本信息] [对话历史] [文件]  ← tabs                        │   │
│ │                                                           │   │
│ │ 基本信息：session ID / 用户 / 来源 / 状态 / 批任务 / 时间   │   │
│ │ 对话历史：BatchConversationView（含 ToolCallBubble）        │   │
│ │ 文件：AdminBatchFiles（列表 / 预览 / 下载）                 │   │
│ └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 文件清单

| 文件 | 说明 |
|------|------|
| `src/views/admin/AiSessionAdmin.vue` | 页面主组件 |
| `src/api/aiSessionAdmin.ts` | API 函数 |
| `src/stores/aiSessionAdmin.ts` | Pinia store |

### 4.3 settingsCatalog 注册

```typescript
{
  id: 'ai-sessions',
  label: 'AI 会话管理',
  perm: 'admin.ai_chat_admin',
  component: () => import('@/views/admin/AiSessionAdmin.vue'),
}
```

### 4.4 复用组件

| 组件 | 复用场景 |
|------|---------|
| `BatchConversationView.vue` | 对话历史 tab（含工具调用、子代理气泡） |
| `AdminBatchFiles.vue` | 文件 tab（列表、预览、下载、导入） |
| `ToolCallBubble.vue` | 工具调用渲染（由 BatchConversationView 内部使用） |
| `SubtaskBubble.vue` | 子代理委派渲染 |
| `MarkdownView.vue` | Markdown 内容渲染 |

---

## 5. 关键设计决策

### D1：为什么不扩展现有的 `GET /ai/chat/admin/sessions`

现有端点：
- 无分页（硬上限 500）
- 无筛选（不能按来源类型、状态、用户过滤）
- 只返回非批任务会话（`WHERE batch_id IS NULL`）
- 返回字段不足（没有 `opencode_session_id`、`batch_id` 等）

替换它会破坏现有的调用方（如果有）。新增 E1 端点，保留旧端点不删。

### D2：为什么不复用 `BatchConversationView` 直接嵌入

`BatchConversationView` 本身已经可复用——它接收 `messages` 数组和 `fetchSubtaskFn` prop，不依赖 batch 上下文。所以直接在 drawer 中使用它，只是需要通过 E3 端点（按 session_id）获取消息，而不是现有的 batch 路径端点。

### D3：session ID 展示格式

展示完整 UUID（如 `sess-a1b2c3d4-...`），不截断。原因：
- 管理员需要与 API 日志、数据库记录、OpenCode 会话 ID 对齐
- 截断后无法直接搜索/复制
- 表格列宽用 `fixed width` + `ellipsis` + `copy on click`

### D4：权限复用

使用现有的 `admin.ai_chat_admin` 权限，不新增权限键。此权限已用于批任务管理、会话归档等，语义一致。

---

## 6. 端点与现有端点的关系

```
现有:
  GET /ai/chat/admin/sessions          → 保留（不删，向后兼容）
  GET /ai/chat/admin/batches/*         → 保留（批任务维度管理不变）
  POST /ai/chat/sessions/<sid>/archive → 保留

新增:
  GET /ai/chat/admin/sessions/v2       → 统一分页列表（替代旧接口的能力）
  GET /ai/chat/admin/sessions/v2/<sid> → 单会话详情
  GET /ai/chat/admin/sessions/v2/<sid>/messages → 对话历史
  GET /ai/chat/admin/sessions/v2/<sid>/files    → 文件列表
  GET /ai/chat/admin/sessions/v2/<sid>/files/download → 文件下载
```

> 使用 `/v2` 路径而非覆盖旧端点，避免破坏现有调用。

---

## 7. 实施步骤

| 步 | 内容 | 文件 |
|----|------|------|
| S1 | 后端：`session_admin_repo.py` — 统一查询 SQL + 分页 | `server/utils/session_admin_repo.py` |
| S2 | 后端：`routes/ai_session_admin.py` — 5 个端点 | `server/routes/ai_session_admin.py` |
| S3 | 后端：`app.py` 注册蓝图 | `server/app.py` |
| S4 | 后端：索引迁移 | `server/init_db.py` |
| S5 | 前端：API 函数 | `src/api/aiSessionAdmin.ts` |
| S6 | 前端：Pinia store | `src/stores/aiSessionAdmin.ts` |
| S7 | 前端：页面组件 | `src/views/admin/AiSessionAdmin.vue` |
| S8 | 前端：注册到 settingsCatalog | `src/views/admin/hub/settingsCatalog.ts` |
| S9 | 测试 | `server/tests/test_ai_session_admin.py` |
| S10 | 文档 | `docs/user-guide/admin/ai-sessions.md` |
