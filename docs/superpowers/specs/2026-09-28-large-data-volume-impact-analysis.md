# AI 功能大数据量影响面分析

> 日期：2026-09-28 ｜ 方法：以 dev 库行数为增长基线，逐条审读热路径 SQL/前端渲染/文件系统操作，EXPLAIN 验证关键查询
> 范围：AI 会话、批任务、编排、搜索、备份、审计。仅分析不改代码。

## 0. 数据增长基线（dev 库实测）

| 表 | 行数 | 增长来源 | 保留机制 |
|---|---|---|---|
| operation_logs | 28,342 | 每个写操作一行 | **无** |
| ai_execution_events | 9,114 | 每次工具/回合事件 | ✅ 30/180 天 |
| ai_chat_sessions | 1,200 | 每次新建 | 软删保留 |
| ai_batch_events | 1,135 | 每次状态变化 | ✅ 180 天（本周新增） |
| ai_chat_messages | 3,089 | 每条消息（含工具大输出） | **无** |
| agent_tool_calls | 1,021 | 每次工具调用 | **无** |
| ai_chat_subtask_messages | 328 | 子代理消息 | **无**（读取有 500 上限） |
| webhook_logs | 321 | 每次回调投递 | **无** |

生产环境为 dev 的数十倍（长期运行 + 多用户）。**行数本身不致命，致命的是"行数 × 每行成本"叠加在无界查询上。**

## 1. 高危：数据量大时会实际影响使用的功能点

### 1.1 交互会话消息全量加载 + 全量渲染（会话页主路径）
- `get_messages`（ai_chat.py:1087）无 `since` 时 `SELECT ... ORDER BY created_at ASC` **无 LIMIT**——一个跑了几个月的长会话（批子会话有 500 条上限，交互会话没有）会把全部消息连 JSON content（含工具大输出）一次性拉给前端；
- 前端 `v-for="(m, mi) in messages"`（AiChatView:1479）全量渲染 DOM，无虚拟滚动——几千条消息 × 每条若干工具气泡 = 页面卡死；
- `mergeReasoningParts`/`splitArtifacts` 等对每条消息在每次渲染时重算，轮询刷新一次全量重跑。
- **触发量级**：单会话 > 2k 条消息即可感知，> 10k 条基本不可用。

### 1.2 会话"搜内容"（侧栏搜索勾选内容命中时）
- `searchSessions`（ai_chat.py:610-640）对每条消息做 `jsonb_array_elements(m.content)` 展开后逐 text 片段 `ILIKE`——**关联子查询 + 无任何索引可用**（ILIKE 对 jsonb 数组元素无法走 trgm 索引；messages 表现有索引只有 `(session_id, created_at)`）;
- 批内搜索（`/ai/chat/batches/<id>` 搜索域）同款；
- **触发量级**：用户消息总量 > 10 万条时搜索从秒级劣化到分钟级。

### 1.3 备份全表载入内存（管理面备份功能）
- `create_backup` 对 44 张表逐表 `fetchall()` 全量载入内存后 `json.dumps` 整表——`ai_chat_messages`（每行含完整 content JSONB）/`operation_logs`/`agent_tool_calls` 达百万行时**进程 OOM**；
- 恢复同样整表载入。

### 1.4 动作门禁核对在复用会话上随历史线性变慢
- 核对 SQL 对 `agent_tool_calls` 按 `tool/state + args_text ~ 正则` 过滤后逐条正则匹配；会话**复用**场景下同一 `oc_session_id` 累积全部历史工具调用——复用第 N 次委派时核对要扫前 N-1 次的全部账目（EXPLAIN 确认 `args_text ~` 无索引且走 Seq Scan 分支）；
- **触发量级**：复用会话累计 > 5k 工具调用时每次终态核对秒级起。

## 2. 中危：功能可用但会逐渐劣化 / 产生维护负担

| # | 功能点 | 问题 | 触发量级 |
|---|---|---|---|
| 2.1 | 侧栏会话列表无分页 | `list_sessions` 全量返回 active/closed 会话，前端全量渲染（每项含徽标/预览/操作按钮）；千级会话 DOM 数千节点 | > 2k 会话 |
| 2.2 | `operation_logs` 无保留期 | 无限增长（dev 已 28k）；admin 页有分页可查，主要是表与索引膨胀拖慢写入 | > 100 万行 |
| 2.3 | `agent_tool_calls` 无保留期 | 同上，且直接放大 1.4 的门禁核对成本 | > 50 万行 |
| 2.4 | `webhook_logs` 无保留期 | outbox 每次投递一行，批任务密集时快速增长 | > 50 万行 |
| 2.5 | `_record_workspace_files` 每回合 rglob 工作区 | 在 worker 后台线程（可接受），但单会话文件数大（如全量 clone 仓库）时长收尾拖慢下一轮 claim | 单会话 > 1 万文件 |
| 2.6 | 配额快查依赖 `workspace_bytes` 快照 | 单会话树极大时 worker 收口遍历耗时长（后台线程）；快照有"回合级"陈旧度 | 单会话 > 5 万文件 |
| 2.7 | 子任务气泡渲染 | MAX_SUBTASK_MESSAGES=500 有上限 ✓，但 500 条 × 每条多 part 仍是重 DOM；复用会话任务段边界随段数增加 | 复用 > 50 段 |

## 3. 已有防护（验证过，大数据量下不受影响）

- 动态数据搜索：`search_text` 折叠列 + pg_trgm GIN（千万级已专项优化）✓
- `ai_execution_events`：30/180 天双层保留 ✓
- `ai_batch_events`：180 天保留 + `CURSOR_EXPIRED` ✓
- artifact 保留期清理 + 备份 ✓
- 批子会话消息 / 子代理消息读取 500 条上限 ✓
- outbox：退避 + dead_letter + 重放，行数有界 ✓
- 工作区配额：单会话口径，usage 快查（本周改）✓
- 消息查询走 `(session_id, created_at)` 索引 ✓（EXPLAIN Bitmap Index Scan）

## 4. 建议补齐顺序

| 优先级 | 项 | 方案要点 |
|---|---|---|
| 高 | 交互会话消息分页 | `get_messages` 强制 LIMIT + 前端滚动加载（since 增量通道已有，补"向前翻页"） |
| 高 | 备份流式化 | 改 `COPY (SELECT ...) TO STDOUT` 按块写 zip，或按时间分片多文件 |
| 高 | 会话内容搜索物化 | 把 text 片段物化进 search 表（复用 search_text 模式）+ trgm 索引；或限制搜索仅标题/预览 |
| 中 | 门禁核对加下界 | 核对查询限定 `occurred_at > 上次核对时间`（增量核对），复用会话不再全量重扫 |
| 中 | operation_logs / agent_tool_calls / webhook_logs 保留任务 | 挂进既有每日 retention 作业 |
| 低 | 侧栏分页/虚拟滚动 | 会话 > 500 时启用虚拟列表或按月折叠 |

## 5. 与既有登记的关系

本分析不改变审计文档（implementation-audit-spec）§9 的功能缺口口径，是**横向的容量维度**补充：即使功能全部落地，上述无界查询/渲染在数据量增长后仍会劣化。建议将"高"优先级 3 项纳入下一开发批次。
