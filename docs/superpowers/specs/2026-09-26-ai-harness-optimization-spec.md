# AI Harness 优化路线图 Spec

**版本**：v1.0
**状态**：待评审
**日期**：2026-09-26
**前置**：P0 执行安全基线 + P1 持久化执行 + P2 编排骨架 + 六轮审计修复（06–14 号文档）均已合入
**范围**：P0/P1/P2 三份 spec 交付后的能力空洞填补、架构短板治理、新机会落地
**本阶段**：定义开发规格，不实施代码修改

---

## 1. 背景

六轮审计与修复后，系统在执行安全（ownership/CAS/fencing）、持久化执行（租约/heartbeat/checkpoint/effect）、事件流（原子 seq/SSE/outbox）、编排骨架（DAG/审批/artifact/runtime 接口）层面已达生产级。本 spec 覆盖下一阶段的 20 项优化，按 P1（可见性+安全+体验）→ P2（编排可靠性+并发治理）→ P3（平台成熟度）三阶段推进。

### 1.1 当前基线

| 层 | 状态 | 依据 |
|---|---|---|
| 状态机 | generation 必填 + fencing 必填 + 单值 CAS | cb8827e |
| 并发控制 | SKIP LOCKED + 租约 + heartbeat + 重试接管 | 11c2949 |
| 门禁 | 三类 + expected 三信号 fail-closed + PG 正则口径 | 25d0eef |
| effect 账本 | callback/mcp_write/file_import/scan_writeback 四类同事务登记 + settle 全流转 | 83309ed |
| outbox | 读返回值 + responseStatus 分类 + 退避 + dead_letter + 心跳 | 83309ed |
| 事件流 | 原子 seq + SSE `id:` 行 + cursor + login_required_sse | 11c2949 |
| 恢复 | 租约/checkpoint/needs_review/决策表（重启不重放） | d5a0347 |
| 编排 | 线性/条件/并行 DAG + 审批门禁 + artifact store + runtime 接口 | c7fdcad |
| 对外 API | pause/resume/commands/events + childId + generation/queueWaitMs | 11c2949 |

---

## 2. 阶段划分与依赖

```text
P1（可见性 + 数据安全 + 外部体验）
├── A1  对外状态/错误结构化
├── A2b _import_child_outputs_to_record effect 补漏
├── A9  备份表清单扩充
├── B3  /metrics 监控端点
└── A4  SSE 前端消费者

P2（编排可靠性 + 并发治理）
├── A5  Step 超时兜底
├── A6  条件分支 + join 汇聚
├── A3  M10 编排定义字段透传 + version 递增
├── B1  并发可配 + provider 限流
├── B2  统一租约框架
├── B4  workspace TTL/配额
├── B6  扫描子路径 effect 补漏（与 A2b 同一入口，P2 收口）
└── C5  token/cost 实时累计

P3（平台成熟度）
├── A7  编排管理面前端
├── A8  Runtime Adapter 生产接线
├── C1  PG LISTEN/NOTIFY 替代轮询
├── C2  实时仪表
├── C3  action gate 过程拦截
├── C4  effect 自动补偿
└── B5  子代理独立取消
```

依赖：B4 ← B2（scheduler 租约）；C5 ← B1（预算框架）；P3-C2 ← P1-A1（结构化 error）。

---

## 3. P1 详细设计

### 3.1 A1 — 对外状态/错误结构化

**现状**：`_batch_out` 已返回 `generation/queueWaitMs/runningMs/lastProgressAt` + `childId` 双轨。缺 `children` 数组、`paused`、`eventCursor`、结构化 `error_detail`。

**改动**：

#### 3.1.1 `_batch_out` 增量字段

```python
# 新增字段（增量，不删既有）
'paused': b.get('paused', False),       # 从 get_batch_detail 的 paused 子查询透出
'eventCursor': ext.get('eventCursor'),   # ai_batch_events 中该批次最大 event_seq
```

#### 3.1.2 新增 `_children_out(batch_id)`

返回每个子会话的结构化状态：

```json
[{
  "childId": "c-1",
  "name": "report1.pdf",
  "status": "running",
  "phase": "tool",
  "attempt": 2,
  "retryCount": 1,
  "retryable": true,
  "lastProgressAt": "…",
  "error": { "code": null, "message": null, "retryable": null }
}]
```

数据源：`ai_chat_sessions` 的 `execution_generation`（→ attempt）、`retry_count`、`error_message`、`gate_status`；`phase` 从最近一次 progress checkpoint 的 `context_snapshot.active_tool` 推导。

#### 3.1.3 新增 `error_detail` 对象

对外 API 错误响应从 `{'error': msg, 'code': code}` 升级为：

```json
{
  "error": "人类可读消息",
  "code": "BATCH_SESSION_CONTROLLED",
  "error_detail": {
    "retryable": false,
    "phase": "dispatch",
    "attempt": 2,
    "evidenceRefs": ["event:bevt_..."]
  }
}
```

增量：既有 `error` 字符串和 `code` 字段保留不动。`api_errors.py::err()` 增加可选 `**kwargs` 透传到 `error_detail`。

#### 3.1.4 涉及文件

| 文件 | 改动 |
|---|---|
| `server/routes/open_api_batches.py` | `_batch_out` +`paused/eventCursor`；新增 `_children_out`；错误响应加 `error_detail` |
| `server/utils/batch_repo.py` | 新增 `get_children_progress(batch_id)`；`get_batch_progress_ext` 补 `eventCursor` |
| `server/utils/api_errors.py` | `err()` 增加可选 `**kwargs` |

#### 3.1.5 测试

- pytest：断言 `_batch_out` 返回含 `paused/eventCursor`；`_children_out` 每个子项含 `attempt/retryable/error`；`err()` 返回含 `error_detail` 对象
- base（当前 HEAD）上 `paused/eventCursor/children/error_detail.retryable` 不存在 → 新测试必失败

---

### 3.2 A2b — `_import_child_outputs_to_record` effect 补漏

**现状**：`ai_scan_engine.py:195` 直接 INSERT `data_files`，不经过 `import_recorded_files`，无 effect 登记。

**改动**：在 `:228-233` INSERT 后加 `record_effect(session_id, 'file_import', f'{session_id}:{path}:{sha256[:16]}', batch_id=…)` + `settle_effect(…, 'committed')`。

**涉及文件**：`server/utils/ai_scan_engine.py`（~5 行）

**测试**：pytest 触发扫描 → 断言 effect 行出现

---

### 3.3 A9 — 备份表清单扩充

**现状**：`BACKUP_TABLES` 不含 14 张 AI Harness 新表。

**改动**：在 `server/utils/backup.py` 的 `BACKUP_TABLES` 列表追加：

```python
('ai_orchestration_definitions', [...], {…}, 'AI 编排定义'),
('ai_orchestration_runs', [...], {…}, 'AI 编排运行'),
('ai_orchestration_steps', [...], {…}, 'AI 编排步骤'),
('artifacts', [...], {…}, 'AI 产物'),
('artifact_refs', [...], set(), 'AI 产物引用'),
('ai_runtime_manifests', [...], {…}, 'AI 运行时清单'),
('ai_execution_attempts', [...], {…}, 'AI 执行尝试'),
('ai_execution_events', [...], {…}, 'AI 执行事件'),
('ai_execution_checkpoints', [...], {…}, 'AI 执行检查点'),
('ai_execution_effects', [...], {…}, 'AI 副作用账本'),
('ai_execution_commands', [...], {…}, 'AI 执行命令'),
('ai_batch_events', [...], set(), 'AI 批次事件'),
('ai_delivery_outbox', [...], {…}, 'AI 投递队列'),
('ai_execution_budgets', [...], {…}, 'AI 执行预算'),
('ai_execution_usage', [...], {…}, 'AI 资源消耗'),
('ai_chat_turns', [...], {…}, 'AI 执行回合'),
('ai_batch_worker_leases', [...], {…}, 'AI 执行器租约'),
('agent_tool_calls', [...], {…}, 'AI 工具调用账本'),
('action_expectations', [...], {…}, 'AI 动作门禁期望'),
```

列清单需对照 `init_db.py` 的 DDL 逐表填写；JSONB 列用集合标记。

**涉及文件**：`server/utils/backup.py`（~30 行）

**测试**：pytest 备份→还原→断言 `ai_batch_events` 行数一致

---

### 3.4 B3 — `/metrics` 监控端点

**现状**：队列深度、执行时长、失败率、审批等待时长全在 DB 里，但没有可消费的指标端点。

**改动**：新增路由 `GET /metrics`（admin token 或内网直连），输出 Prometheus 文本格式：

```text
# HELP ai_batch_queue_depth Pending batch children waiting for claim
# TYPE ai_batch_queue_depth gauge
ai_batch_queue_depth 5
# HELP ai_batch_running Currently running batch children
# TYPE ai_batch_running gauge
ai_batch_running 3
# HELP ai_batch_failed_last_hour Failed in last hour
# TYPE ai_batch_failed_last_hour counter
ai_batch_failed_last_hour 2
# HELP ai_orchestration_runs_active Active orchestration runs
# TYPE ai_orchestration_runs_active gauge
ai_orchestration_runs_active 1
# HELP ai_outbox_pending Undelivered outbox rows
# TYPE ai_outbox_pending gauge
ai_outbox_pending 0
# HELP ai_worker_lease_heartbeat_seconds Seconds since last worker heartbeat
# TYPE ai_worker_lease_heartbeat_seconds gauge
ai_worker_lease_heartbeat_seconds{kind="batch"} 5
```

指标来源全部为单条 SQL 聚合（COUNT/AVG/MAX），零新依赖。

**涉及文件**：`server/routes/metrics.py`（新增，~60 行）；`server/app.py`（注册蓝图）

**测试**：pytest 断言 `/metrics` 返回 200 + 含 `ai_batch_queue_depth` 行

---

### 3.5 A4 — SSE 前端消费者

**现状**：后端 `GET /ai/chat/batches/events?ids=…` 已就绪（`login_required_sse` + `id:` 行 + cursor），前端无消费者，仍用 5s/10s 轮询。

**改动**：

新建 `src/api/batchEvents.ts`：

```typescript
export function createBatchEventStream(
  batchIds: string[],
  handlers: {
    onEvent: (batchId: string, event: any) => void;
    onDone: (batchId: string) => void;
    onError: () => void;
  },
) {
  const params = batchIds.map(id => `ids=${id}`).join('&');
  const token = localStorage.getItem('check-manage:token');
  const es = new EventSource(`/api/ai/chat/batches/events?${params}&access_token=${token}`);
  // EventSource 原生带 Last-Event-ID 重连
  es.addEventListener('batch_event', e => { … });
  es.addEventListener('batch_done', e => { … });
  es.onerror = () => { /* 自动重连，降级轮询 */ };
  return es;
}
```

`src/stores/aiChatBatches.ts` 改造：`selectBatch` 时优先开 SSE，`onerror` 时降级为现有轮询（不删除轮询代码）；收到 `batch_event` 时调 `applyDetail` 增量更新。

**涉及文件**：`src/api/batchEvents.ts`（新增 ~40 行）；`src/stores/aiChatBatches.ts`（~20 行改动）

**测试**：vitest mock EventSource，断言 `batch_event` 触发 `applyDetail`、`onerror` 触发降级轮询

---

## 4. P2 详细设计

### 4.1 A5 — Step 超时兜底

**现状**：编排 step 状态 `running` 后无超时检查——子会话卡住（如 tool_stall 未触发）则 run 永久 running。

**改动**：definition 节点支持 `timeout_sec` 字段；编排调度器 `_tick` 中扫描 `running` 超过 `timeout_sec` 的 step → 标 `failed`（带 `error_message='step timeout'`），写 `ai_batch_events('step.timeout')`。definition 级默认 `timeout_policy.default_sec = 900`（15 分钟）。

**涉及文件**：`orchestration_engine.py`（~30 行）；`orchestration_defs.py`（校验 timeout_sec 为正整数）

**测试**：pytest 设 `timeout_sec=1` → 等待 → 断言 step=failed、run=partial/failed

---

### 4.2 A6 — 条件分支后 join 汇聚

**现状**：菱形 DAG（split→条件分支→join）中，一侧被 skip 后 join 收到 `dep_dead` 也被 skip → run completed 但 join 实际未汇聚。12 号确认 join 对 skipped 依赖的处理是"全 skip"而非"至少一个 succeeded"。

**改动**：join 节点新增 `join_policy` 字段（默认 `'all_success'`），支持 `'any_success'`（至少一个入边 succeeded 即通过）。`_advance_run` 的 join 判定改读该字段。

```python
if s['kind'] == 'join':
    deps_ok = _join_satisfied(by_node, s.get('depends_on'), s.get('join_policy', 'all_success'))
```

`_join_satisfied`：
- `all_success`：全部依赖 succeeded（现有行为）
- `any_success`：至少一个 succeeded（其余 skipped/failed 不阻塞）

**涉及文件**：`orchestration_engine.py`（~15 行）；`orchestration_defs.py`（校验 join_policy 枚举）

**测试**：pytest 菱形 DAG + `any_success` → 一侧 skip → join 通过 → run completed

---

### 4.3 A3 — M10 编排定义字段透传 + version 递增

**现状**：`orchestration_defs.py:33-41` 归一化白名单只保留 10 个键，`skills/input_refs/runtime/budget/priority` 被静默丢弃；`:116-127` 每次发布生成新随机 id + `version=1`，无递增路径。

**改动**：

1. 归一化白名单扩充：保留全部 `skills/input_refs/runtime/budget/priority/timeout_sec/join_policy`
2. 发布改为"同 id 递增 version"：`SELECT max(version)+1 FROM … WHERE id=%s`，首次发布 INSERT，后续 UPSERT
3. `list_definitions` 返回每个 id 的最新版本

**涉及文件**：`orchestration_defs.py`（~40 行）

**测试**：pytest 发布两次同 id → version 从 1 递增到 2；旧 version 行保留；`skills` 字段不被丢弃

---

### 4.4 B1 — 并发可配 + provider 限流

**现状**：`MAX_CONCURRENT = 3` 硬编码。

**改动**：

1. `batch_engine.BatchWorker.__init__` 改读优先级链：`ai_settings` 表 `batch_max_concurrent` 列 > env `AI_BATCH_CONCURRENCY` > 默认 3
2. claim 数量计算 `free = self.MAX_CONCURRENT - len(self._running_session_ids)` 改用动态读取
3. provider 限流：在 `_run_one` dispatch 前，检查同 provider 的在跑子会话数是否超过 `provider_max_concurrent`（同表配置）；超限则该子会话本 tick 跳过

**涉及文件**：`batch_engine.py`（~20 行）；`ai_settings` DDL 加列（`init_db.py` 幂等迁移）

**测试**：pytest 设 `batch_max_concurrent=1` → 断言同时只跑 1 个

---

### 4.5 B2 — 统一租约框架

**现状**：只有 batch worker / delivery / scheduler 三个有租约。scan scheduler（`ai_scan_scheduler.py`）、backup scheduler、audit retention 调度器**没有租约**，多实例下会重复执行。

**改动**：统一用 `execution_lease.acquire(key, kind)` 包裹以下调度器的 `start()`：

| 调度器 | lease_key | lease_kind |
|---|---|---|
| scan scheduler | `scan` | `scheduler` |
| backup scheduler | `backup` | `scheduler` |
| audit retention | `audit_retention` | `scheduler` |
| status badge timeout | `status_badge` | `scheduler` |
| field index build | `field_index` | `scheduler` |
| ETL scheduler | `etl` | `scheduler` |

每个调度器加同款 `_acquire_retry` 模式（N2 同款：抢不到不放弃，每 5s 重试）。

**涉及文件**：`ai_scan_scheduler.py`（~15 行）；`backup.py`（~15 行）；`skillopt.py` retention 部分（~10 行）；`status_badge_timeout_scheduler.py`（~10 行）；`field_index_scheduler.py`（~10 行）；`etl_scheduler.py`（~10 行）

**测试**：pytest 两个进程同时 acquire → 只有一个成功

---

### 4.6 B4 — workspace TTL / 配额

**现状**：`config.py:89-90` 的 `AI_WORKSPACE_QUOTA_MB` 和 `AI_SESSION_TTL_HOURS` 是死配置，无消费方。

**改动**：

1. TTL：在编排调度器 tick（`lease_kind='scheduler'`）中加一步：扫 `ai_chat_sessions WHERE status IN ('completed','failed','needs_review') AND finished_at < NOW() - TTL`，删除 workspace 目录（`shutil.rmtree`）+ 清 `workspace_path` 列（保留 DB 行以维持审计引用）
2. 配额：`_prepare_workspace` 创建前，累加该用户全部子会话的 workspace 目录大小，超 `AI_WORKSPACE_QUOTA_MB` 则拒绝并返回 `WORKSPACE_QUOTA_EXCEEDED`

**涉及文件**：`orchestration_engine.py` tick（~30 行）；`workspace.py`（~15 行）；`batch_engine.py::_prepare_workspace`（~10 行）

**测试**：pytest 设 TTL=0 → 断言终态子会话 workspace 目录被删；设小配额 → 断言新创建被拒

---

### 4.7 B6 — 扫描子路径 effect 补漏

**现状**：`ai_scan_engine.py:195 _import_child_outputs_to_record` 直接 INSERT `data_files`，不经过 `import_recorded_files`，无 effect 登记。

**改动**：在 INSERT 后加 `record_effect(session_row['id'], 'file_import', f'{session_id}:{path}:{sha[:16]}', batch_id=…)` + `settle_effect(…, 'committed')`。同 A2b 模式。

**涉及文件**：`ai_scan_engine.py`（~5 行）

**测试**：pytest 触发扫描含文件 → 断言 effect 行出现

---

### 4.8 C5 — token/cost 实时累计

**现状**：usage 在回合收敛时才写 `ai_execution_usage`。

**改动**：在 `_persist_conversation` 的进度落库中，每条 assistant 消息的 meta（含 tokensInput/tokensOutput/cost）落库后同步 UPSERT `ai_execution_usage`。预算判定从"终态才准"变为"每次进度落库即准"。

**涉及文件**：`batch_engine.py::_persist_conversation`（~10 行）

**测试**：pytest 进度落库后断言 `ai_execution_usage` 行存在且 tokens > 0

---

## 5. P3 详细设计（概要）

| # | 项 | 概要 |
|---|---|---|
| A7 | 编排管理面前端 | Vue 组件：run graph 可视化（cytoscape，项目已有依赖）、审批收件箱集成（复用 `/workflow/inbox`）、step 状态实时推送 |
| A8 | Runtime Adapter 接线 | batch_engine `_run_one` 的 `opencode_client` 调用全部替换为 `get_runtime().dispatch/abort/…`；`OpenCodeLocalRuntime` 包装现有客户端逻辑 |
| C1 | PG LISTEN/NOTIFY | 触发器 `AFTER INSERT ON ai_chat_sessions` → `pg_notify('batch_claim_ready')`；BatchWorker dispatcher 加 `psycopg2.extras.select` + `LISTEN batch_claim_ready`，收到通知即 `notify()` 唤醒 claim，消灭 2s 空转轮询 |
| C2 | 实时仪表 | 管理页 SSE 通道复用 `ai_chat_batches/events` 基建，推 `attempt.*` 事件（attempt.started/tool.started/tool.completed）；前端画 attempt timeline |
| C3 | PreToolUse 拦截 | 扩展 `baize-trace.js` 插件：工具调用**前** POST `/ai/gate/pre-check` → Flask 查 `action_expectations`（`mode='pre'` 的行）→ 返回 allow/deny → 插件阻断工具执行 |
| C4 | effect 自动补偿 | `ai_execution_effects` 加 `compensation_handler` 列（JSON，存逆操作参数）；编排 step 失败时按 `compensation_policy` 自动调用 |
| B5 | 子代理独立取消 | OpenCode 插件暴露子代理列表 → Flask 定期轮询 → 管理页可单独 abort 某个子代理 |

---

## 6. 涉及文件总清单

| 阶段 | 新增文件 | 修改文件 |
|---|---|---|
| P1 | `server/routes/metrics.py`、`src/api/batchEvents.ts` | `open_api_batches.py`、`batch_repo.py`、`api_errors.py`、`ai_scan_engine.py`、`backup.py`、`app.py`、`aiChatBatches.ts` |
| P2 | （无新增） | `orchestration_engine.py`、`orchestration_defs.py`、`batch_engine.py`、`batch_repo.py`、`ai_scan_scheduler.py`、`backup.py`、`skillopt.py`、`status_badge_timeout_scheduler.py`、`field_index_scheduler.py`、`etl_scheduler.py`、`ai_scan_engine.py`、`workspace.py`、`config.py`、`init_db.py` |
| P3 | `src/views/admin/AiOrchestrationManager.vue`、`src/api/orchestration.ts`、`src/components/admin/RunGraphView.vue` | `batch_engine.py`、`orchestration_engine.py`、`delivery_outbox.py`、`mcp-server/tools/baize-trace.js` |

---

## 7. 测试策略

- 每项都有 **"base 上必失败"** 的判别性测试（延续 11 号确立的标准）
- P1 的 5 项走 pytest（后端）+ vitest（前端 SSE）
- P2 的编排项（A5/A6/A3）走 pytest 真库测试
- P3 前端走 vitest + Playwright；后端走 pytest
- 泄漏验证：全量套件前后孤儿 `ai_batch_events` 计数差 ≤ 1（conftest 兜底生效）

---

## 8. 非目标

- 不引入 Redis / RabbitMQ / Kafka（PG 已够用）
- 不做多实例水平扩展（单实例 + 租约足够当前规模）
- 不替换 OpenCode runtime
- 不实现会签 / SLA / 动态表单 / 任意循环 / 跨组织审批

---

## 9. 依赖与阻断

- 本 spec 依赖 `cb8827e` + `83309ed` + `d6e960a` 全部合入（已完成）
- P2 的 B4 依赖 B2；P2 的 C5 依赖 B1；P3 的 C2 依赖 P1 的 A1
- P2/P3 不阻断 P1 独立交付

---

## 10. 非目标与风险

| 风险 | 缓解 |
|---|---|
| A1 结构化 error 是增量，旧调用方可能忽略 `error_detail` | 文档 + examples 引导迁移 |
| B2 统一租约改变 6 个调度器行为 | 每个独立 PR，单测覆盖 |
| B4 workspace 删除不可逆 | 只删终态 + TTL 已过；加 dry-run 模式 |
| C3 PreToolUse 依赖 OpenCode 插件 | 与 baize-trace 同机制，已有先例 |
