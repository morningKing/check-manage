# 指定批任务下会话搜索功能 Spec

**版本**：v1.0  
**状态**：待评审  
**日期**：2026-09-15  
**范围**：普通用户 AI 助手侧边栏中的批任务子会话搜索  
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 现状问题

当前普通用户侧边栏搜索接口：

```text
GET /ai/chat/sessions/search
```

只搜索：

- 普通会话标题。
- 普通会话消息文本。

同时限制：

```sql
status IN ('active', 'closed')
```

批任务子会话通常处于以下状态：

```text
pending / running / completed / failed
```

因此，批任务下的子会话不会进入普通搜索结果。

管理员会话页面虽然支持 `batchId` 筛选，但关键词目前只搜索：

- 会话标题。
- 输入文件路径。
- `last_message_preview`。

不会搜索 `ai_chat_messages.content` 中的完整历史消息。

## 2. 功能目标

用户可以针对指定批任务搜索其下的子会话，并能够：

- 按子会话标题搜索。
- 按输入文件名搜索。
- 按完整会话消息搜索。
- 搜索运行中、已完成、失败和待运行的子会话。
- 点击搜索结果后直接打开对应子会话。
- 自动展开并选中所属批任务。
- 不影响现有普通会话搜索行为。
- 严格限制在当前用户自己的批任务范围内。

## 3. 范围

### 3.1 本期范围

- 在现有 AI 助手侧边栏增加“搜索本批任务”入口。
- 扩展普通用户会话搜索接口，支持批任务范围。
- 搜索批任务子会话标题、输入文件名和完整文本消息。
- 支持批任务子会话的全部有效运行状态。
- 复用现有批任务详情和子会话打开逻辑。
- 增加用户权限和跨批任务隔离测试。
- 增加前端浏览器验证。

### 3.2 非本期范围

- 默认将所有批任务子会话加入“全部会话”搜索。
- 搜索工具调用参数、内部 reasoning 或原始 OpenCode 事件。
- 搜索工作区文件内容。
- 引入独立的全文搜索服务。
- 改造批任务列表分页模型。

## 4. 交互设计

### 4.1 搜索入口

在现有 `BatchGroup` 批任务标题区域增加操作按钮：

```text
[批任务名称] [状态] [进度] [搜索本批任务]
```

点击“搜索本批任务”后：

1. 设置当前搜索范围为该批任务。
2. 聚焦顶部会话搜索框。
3. 搜索框显示范围标签。
4. 输入关键词后，仅搜索该批任务下的子会话。

### 4.2 搜索范围状态

顶部搜索框增加范围状态：

| 状态 | 说明 |
|---|---|
| 全部普通会话 | 保持当前搜索行为 |
| 指定批任务 | 仅搜索当前批任务下的子会话 |

选中批任务后，搜索框占位提示改为：

```text
在“批任务名称”中搜索会话标题、文件名或内容
```

提供清除范围按钮，清除后恢复普通会话搜索。

### 4.3 搜索结果

搜索结果需要显示：

- 子会话标题。
- 所属批任务名称。
- 输入文件名。
- 子会话状态。
- 批任务序号。
- 命中字段。
- 内容摘要。

示例：

```text
季度报告分析
批任务：2026 年度报告处理
文件：report-001.pdf
状态：处理中
命中：消息内容
……请提取报告中的关键风险和结论……
```

### 4.4 点击结果

点击搜索结果后：

1. 加载对应批任务详情。
2. 展开批任务分组。
3. 选中对应子会话。
4. 打开现有会话详情页面。
5. 保留当前搜索条件，允许返回搜索结果继续查看。

复用现有逻辑：

- `batches.selectBatch(batchId)`。
- `selectBatchChild(sessionId)`。
- `aiChat.openSession(sessionId)`。

## 5. 搜索范围

### 5.1 指定批任务模式

搜索以下字段：

- `ai_chat_sessions.title`。
- `ai_chat_sessions.batch_input_file`。
- `ai_chat_sessions.last_message_preview`。
- `ai_chat_messages.content` 中 `type = 'text'` 的文本内容。

输入文件搜索按完整路径和文件名均可命中。

批任务名称不作为每个子会话的匹配字段，只作为搜索范围和结果上下文展示。

### 5.2 普通会话模式

保持现有行为：

- 只搜索普通会话。
- 不默认引入全部批任务子会话。
- 继续使用现有 `active/closed` 状态过滤。

这样可以避免批任务子会话数量较多时污染默认搜索结果。

## 6. API 设计

扩展现有接口：

```text
GET /ai/chat/sessions/search
```

新增可选参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `q` | string | 搜索关键词 |
| `batchId` | string | 指定批任务 ID，可选 |
| `limit` | integer | 返回数量，默认 50，最大 50 |

示例：

```text
GET /ai/chat/sessions/search?q=风险&batchId=batch-123
```

### 6.1 权限规则

当传入 `batchId` 时，必须同时满足：

```text
ai_chat_batches.id = batchId
ai_chat_batches.user_id = 当前用户
ai_chat_sessions.user_id = 当前用户
ai_chat_sessions.batch_id = batchId
```

不属于当前用户的批任务统一返回空结果，不返回“批任务不存在”之类的信息，避免资源枚举。

### 6.2 状态规则

指定批任务时允许搜索：

```text
pending
running
completed
failed
active
closed
```

排除：

```text
deleted
```

具体状态白名单应集中定义，避免使用宽泛的 `NOT IN`。

### 6.3 响应结构

扩展现有 `AiSessionSearchHit`：

```json
{
  "sessions": [
    {
      "id": "session-001",
      "title": "report-001.pdf",
      "status": "completed",
      "lastActiveAt": "2026-09-15T10:20:00Z",
      "matchField": "content",
      "snippet": "……报告中存在供应链风险……",
      "batchId": "batch-123",
      "batchName": "季度报告分析",
      "batchSeq": 3,
      "inputFileName": "report-001.pdf",
      "isBatchChild": true
    }
  ]
}
```

`matchField` 取值：

```text
title
file
preview
content
```

## 7. 后端查询设计

### 7.1 查询原则

指定 `batchId` 时，必须先通过批任务范围缩小数据集，再搜索消息内容。

查询条件应包含：

```sql
s.user_id = 当前用户
AND s.batch_id = 指定批任务
AND s.status IN (...)
AND (
    s.title ILIKE ...
    OR s.batch_input_file ILIKE ...
    OR s.last_message_preview ILIKE ...
    OR EXISTS (
        SELECT 1
        FROM ai_chat_messages m
        CROSS JOIN LATERAL jsonb_array_elements(m.content) p
        WHERE m.session_id = s.id
          AND p->>'type' = 'text'
          AND p->>'text' ILIKE ...
    )
)
```

### 7.2 排序

建议排序顺序：

1. 标题命中。
2. 文件名命中。
3. 消息内容命中。
4. 最近活跃时间倒序。
5. `batch_seq` 正序。

### 7.3 摘要

复用现有摘要生成逻辑：

- 从命中的文本消息中截取上下文。
- 命中关键词前后各保留约 40 个字符。
- 换行转为空格。
- 前端继续使用现有高亮逻辑。
- 标题或文件名命中时可以不显示消息摘要。

## 8. 前端实现范围

建议修改：

- `src/views/ai-chat/AiChatView.vue`
- `src/components/ai-chat/BatchGroup.vue`
- `src/api/aiChat.ts`
- `src/types/aiChat.ts`，如类型集中维护

### 8.1 搜索状态

新增：

```text
sessionSearchBatchId
sessionSearchBatchName
```

搜索请求由：

```text
searchSessions(query)
```

扩展为：

```text
searchSessions(query, { batchId })
```

仍保留现有：

- 300ms 防抖。
- 旧请求结果丢弃。
- 搜索关键词高亮。
- 搜索清空逻辑。

### 8.2 批任务加载边界

搜索结果对应的批任务可能不在当前批任务列表分页中。

点击结果时不能依赖批任务已经存在于当前列表，应允许：

1. 通过批任务 ID 加载详情。
2. 将详情写入当前批任务状态。
3. 直接打开指定子会话。

## 9. 管理员页面同步设计

管理员页面已有：

```text
batchId + keyword
```

建议同步修正其搜索语义：

- 保留批任务 ID 精确过滤。
- 关键词增加 `ai_chat_messages.content` 全文搜索。
- 运行中和失败会话也必须可以命中历史消息。
- 不再仅依赖 `last_message_preview`。

普通用户和管理员的“消息搜索”语义应保持一致。

管理员接口不需要新增路由，只需扩展现有查询条件。

## 10. 性能方案

### 10.1 MVP 阶段

不新增搜索表，直接复用：

```text
ai_chat_sessions(batch_id, batch_seq)
```

现有索引。

先通过 `batch_id` 缩小范围，再解析消息 JSONB。由于单个批任务的子会话数量有限，MVP 性能风险可接受。

### 10.2 禁止的实现

不允许在没有 `batchId` 的情况下默认对全部会话执行：

```text
jsonb_array_elements(ai_chat_messages.content)
ILIKE '%keyword%'
```

这会导致大量消息表扫描。

### 10.3 后续优化

当批任务或消息量增长后，再考虑：

- `ai_chat_messages.search_text`。
- `pg_trgm` GIN 索引。
- 独立消息搜索表。
- 消息写入时同步维护搜索文本。

当前阶段不提前引入，以控制改动范围。

## 11. 测试设计

### 11.1 后端测试

覆盖：

- 指定批任务内按标题命中。
- 指定批任务内按文件名命中。
- 指定批任务内按历史消息命中。
- `last_message_preview` 为空时仍可命中。
- `pending`、`running`、`completed`、`failed` 均可搜索。
- 普通会话搜索行为不变。
- 不同批任务之间不会串结果。
- 不同用户之间不会串结果。
- `%`、`_`、反斜杠按字面量处理。
- 返回数量不超过 50。
- 非法或非本人 `batchId` 返回空结果。

### 11.2 前端测试

覆盖：

- 点击“搜索本批任务”正确设置搜索范围。
- 搜索请求包含 `batchId`。
- 清除范围后恢复普通搜索。
- 搜索结果显示批任务和文件上下文。
- 点击结果正确加载批任务和子会话。
- 批任务不在当前分页时仍能打开。
- 空结果显示正确提示。
- 搜索请求不会被旧响应覆盖。

### 11.3 Playwright 验证

需要实际验证：

1. 创建或选择一个包含多个子任务的批任务。
2. 确认子任务处于运行中、完成或失败状态。
3. 在批任务上点击“搜索本批任务”。
4. 输入只存在于某个历史消息中的关键词。
5. 确认搜索结果只属于当前批任务。
6. 点击结果，确认打开正确子会话。
7. 切换到另一个批任务，确认结果不会残留。

## 12. 实施阶段

### P0：普通用户批任务搜索

- 扩展普通用户搜索接口的 `batchId` 参数。
- 增加批任务范围查询。
- 放开批任务子会话状态搜索。
- 增加完整消息内容搜索。
- 增加 `BatchGroup` 搜索入口。
- 完成点击结果打开子会话流程。

### P1：管理员搜索一致性

- 管理员关键词搜索完整消息。
- 修正“标题/文件名/消息”与实际行为不一致的问题。
- 增加跨批任务隔离测试。

### P2：搜索性能优化

- 基于真实数据执行 `EXPLAIN ANALYZE`。
- 必要时新增消息搜索文本列和 trigram 索引。
- 增加批量消息搜索性能监控。

## 13. 验收标准

1. 用户可以从批任务分组进入“搜索本批任务”模式。
2. 指定批任务下的子会话可以按标题、文件名和消息内容搜索。
3. 运行中、已完成、失败和待运行子会话都可以被搜索。
4. 搜索结果不会包含其他批任务或其他用户的数据。
5. 点击结果后可以直接打开正确的批任务子会话。
6. 默认普通会话搜索行为不发生回归。
7. 消息搜索不依赖 `last_message_preview`，可以命中历史消息。
8. 批任务不在当前列表分页时，搜索结果仍然可以打开。
9. 管理员搜索与普通用户搜索的“消息”语义保持一致。
10. 在真实浏览器中完成完整搜索和打开流程验证。

## 14. 相关文件

### 后端

- `server/routes/ai_chat.py`
- `server/utils/session_admin_repo.py`
- `server/utils/batch_repo.py`
- `server/routes/ai_session_admin.py`

### 前端

- `src/views/ai-chat/AiChatView.vue`
- `src/components/ai-chat/BatchGroup.vue`
- `src/api/aiChat.ts`
- `src/types/aiChat.ts`

### 测试

- `server/tests/test_ai_chat_search.py`
- `server/tests/test_ai_session_admin.py`
- `e2e/ai-chat-batch.spec.ts`
