# AI Harness P0 执行安全基线 Spec

**版本**：v1.0
**状态**：待评审
**日期**：2026-09-23
**优先级**：P0
**范围**：AI 批任务子会话（`ai_chat_batches` / `ai_chat_sessions`）、批任务对外 API（`/v1/ai-batches`）、AI 助手交互发送路径、动作账本与到位门禁
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 背景

当前 AI 批任务已经具备完整的“DB 账本 + 进程内 worker”形态：

- 任务定义、状态、重试计数、续跑提示词、取消/暂停标志全部持久化在 `ai_chat_sessions` / `ai_chat_batches`；
- dispatcher 用 `FOR UPDATE SKIP LOCKED` 从库里认领 pending 子任务（`server/utils/batch_engine.py:696-727`）；
- 具备暂停/中断/继续/重试/重执行、看门狗、自动重试、运行中对账、动作门禁与执行审计。

但这些机制解决的是“失败后还能重跑”，并没有解决“**谁拥有这次执行**”。当前存在一条贯穿性的缺口：

> 同一个批任务子会话，可以在多个入口被同时驱动；终态收口不具线性化点；删除不等待执行者退出；门禁异常会被当成通过。

这类缺口在单机、单用户、文件型任务下多数不会暴露；一旦任务产生不可逆外部副作用（写业务数据、调用外部 API、发布产物），或部署出现多进程、人工介入、网络抖动，就会表现为**重复执行、重复计数、状态互相覆盖、门禁形同虚设**。

本 Spec 定义 P0 阶段的最小改造集合：**先把执行权、状态收口、删除语义和门禁可信度封死**，不引入分布式队列、DAG、容器或完整 checkpoint（这些属于 P1/P2，见 `2026-09-23-ai-harness-p1-durable-execution-spec.md` 与 `2026-09-23-ai-harness-p2-orchestration-runtime-spec.md`）。

### 1.1 现状能力（可复用，不要重造）

| 能力 | 现状 | 证据 |
|---|---|---|
| 认领互斥 | `FOR UPDATE SKIP LOCKED` 认领 pending 子任务 | `server/utils/batch_engine.py:696-727` |
| 归属隔离 | 内部按 `user_id`，对外按 `api_key_id`，命中不到返回 404 | `server/utils/batch_repo.py:101-190`、`server/routes/open_api_batches.py:398-406` |
| 暂存路径校验 | 对外创建/追加校验 path 归属与存在性 | `server/routes/open_api_batches.py:94-214` |
| 协作式取消/暂停 | `cancel_requested` / `pause_requested` + worker 轮询消费 | `server/utils/batch_engine.py:729-772`、`:1475-1481`、`server/utils/batch_repo.py:193-288` |
| 终态重试/继续/重执行 | 已有 repo 层函数与内外端点 | `server/utils/batch_repo.py:485-598`、`server/routes/ai_chat_batches.py:395-472` |
| 动作账本与期望 | `agent_tool_calls` / `action_expectations` + 核对 | `server/utils/agent_ledger.py:231-591` |
| 执行审计事实层 | attempts / events / manifests / contracts / step_results / diagnoses | `server/migrations/2026_09_17_execution_audit_tables.py` |
| UI 删除保护雏形 | 非终态 409、`stop=1` 先取消再 bounded drain | `server/routes/ai_chat_batches.py:296-341` |

### 1.2 问题矩阵

| # | 问题 | 现状证据 | 影响 |
|---|---|---|---|
| **P0-1** | 批子会话可被普通聊天入口并发驱动 | `server/routes/ai_chat.py:748-771`（`_load_session_for_user` 返回 `batch_id` 但调用方不做门禁）、`:819-825`（发送入口）、`:951-956`（直接挂监听并 dispatch）；前端 `src/views/ai-chat/AiChatView.vue:1138-1165`、`src/stores/aiChat.ts:494-581` | 同一 OpenCode session 并发两个 turn：消息顺序错乱、完成判定竞态、pause/cancel 失效、门禁核对时机不确定 |
| **P0-2** | 终态写回无 CAS，可被 stale worker 覆盖并重复计数 | `server/utils/batch_engine.py:1791-1810`（`_mark_done` 无条件 UPDATE + `done+1`）、`:1812-1832`、`:1834-1858`、`:1860-1877` | retry/resume/reexecute/cancel 与 worker 收口交叉时重复计数、状态回退、`completed_at` 被重置 |
| **P0-3** | 外部 API 删除无终态保护 | `server/routes/open_api_batches.py:490-509`（直接 cleanup + `delete_batch`，`server/utils/batch_repo.py:171-190` 无终态谓词） | 运行中 OpenCode turn 继续执行、写回命中已删行、外部副作用无法撤销 |
| **P0-4** | 外部 API `actionChecks` 未校验，登记失败被吞，零期望直接 passed | `server/routes/open_api_batches.py:363-372`（原样透传，对比内部 `server/routes/ai_chat_batches.py:93-105`）；`server/utils/batch_engine.py:1100-1121`（异常仅 warning）；`server/utils/agent_ledger.py:492,522-523`（无期望 → passed）；`server/utils/batch_engine.py:1018-1035`（仅 `failed` 阻断） | 调用方以为门禁生效，实际可能“零期望 → passed → completed” |
| **P0-5** | 门禁定向字段在规范化时丢失 | `server/utils/agent_ledger.py:274-280`（normalized 保留 `subagents`，未保留 `apply_to`）；消费点 `:332-355`、`server/utils/batch_engine.py:1100-1114` | `apply_to` 检查被应用到全部子任务，产生误报失败 |
| **P0-6** | 子代理账本先写后建行，FK 失败 | `server/utils/batch_engine.py:1729-1741`（先 `record_messages`）与 `:1749-1753`（后 `_write_subtask`）；FK 定义 `server/migrations/2026_09_20_agent_action_gate.py:18-36` | 首次发现子代理时账本 INSERT 失败、`ledger_ok=False`；快速完成的任务永久漏记，门禁 inconclusive |
| **P0-7** | 控制操作存在 TOCTOU 与遗漏分支 | `server/utils/batch_repo.py:363-382`（`cancel_child` 先查后改，paused 只置 flag，worker 预检仅扫 pending：`server/utils/batch_engine.py:743-766`）、`:429-443`（`MAX(batch_seq)+1` 无唯一约束，`server/init_db.py:706-707`）、`:665-696`（`reset_failed_to_pending` 直接置 pending，未按 paused 重算） | paused 子任务永远无法单独取消；并发 append 序号冲突；批次状态与实际执行不一致 |
| **P0-8** | 重试后旧 attempt 悬挂、审计链断裂 | `server/utils/batch_engine.py:1023-1029`（gate retry）、`:1049-1053`（auto retry）直接 return，未 `finish_latest_running` | `ai_execution_attempts` 永久 `running`，耗时/成本统计与“是否仍在执行”的判断失真 |
| **P0-9** | worker 无跨进程保护 | `server/utils/batch_engine.py:239-246`（模块级单例）、`:585-605`（仅检查本进程线程）、`:774-784`（启动无条件 `running→pending`）、`server/app.py:302-317`；README 又允许 gunicorn（`README.md:239`） | 多 worker 各自启动 3 槽位与对账器，互相当作 orphan 重排，重复通知/回调 |
| **P0-10** | 前端子任务状态文案缺失（**2026-09-23 核查修正**：原判"终态集合与后端不一致"不属实——`src/stores/aiChatBatches.ts` TERMINAL_STATUSES `{completed,failed,partial}` 与后端 `open_api_batches.py` 完全一致，批级状态域本无 `cancelled`，paused 属非终态、持续轮询是正确行为） | `src/views/ai-chat/AiChatView.vue` `batchStatusLabel` 仅映射 pending/running/completed/failed，子任务 `paused`/`cancelled` 退化为英文原文 | UI 文案缺失，非轮询缺陷 |

### 1.3 与既有文档的关系

- `docs/superpowers/specs/2026-09-16-ai-chat-p0-reliability-spec.md` 已提出 `ai_chat_turns`、批任务删除 stop-first、统一错误模型（§7、§10.4、§11）。本 Spec **沿用其模型并收敛到批任务执行链路**，不重复定义通用会话可靠性。
- `docs/superpowers/specs/2026-09-11-batch-session-parity-design.md` 要求“worker 权威 + 人工 continuation 经批通道 + composer 门禁”（§Backend/Execution ownership、§Frontend）。本 Spec 是其实施前置：先有后端权威边界，前端门禁才不是唯一防线。
- `docs/design/ai/AI批任务容灾与恢复机制.md` 描述的重启/对账语义在 P0 保持兼容，仅新增“旧 generation 写回必须 0 行”的约束（§8.1 已承认续跑非强幂等）。

---

## 2. 目标与非目标

### 2.1 目标

1. **单一执行权**：任一时刻，一个批子会话最多存在一个 active turn；普通聊天入口、批 worker、人工 continuation 三者互斥。
2. **线性化终态**：完成/失败/取消/暂停四个终态写入具备 CAS 语义；批次计数只由成功的状态转移驱动。
3. **删除不产生幽灵执行**：非终态批任务在内外两个入口都必须先停止并 bounded drain，超时不得继续删除。
4. **门禁 fail-closed**：非空 `actionChecks` 的登记、账本、核对任一环节不可证实，子任务不得 `completed`。
5. **单实例安全**：多进程部署时只有一个执行器实例运行 batch/scan worker。
6. **确定性问题清零**：§9 表格中的每一行都有回归测试。

### 2.2 非目标

- 不引入分布式队列 / 跨实例 lease 接管（P1）。
- 不做结构化 checkpoint 与 effect ledger（P1）。
- 不做事件 cursor / SSE 回放 / outbox（P1）。
- 不做 Step/DAG、人工审批、Artifact Store、容器 runtime（P2）。
- 不改外部 API 的既有字段语义（只做增量与安全收紧）。
- 不重写 OpenCode 集成方式（仍走 `server/utils/opencode_client.py`）。
- 不解决 `docs/design/ai/AI批任务容灾与恢复机制.md §8.1` 描述的“续跑重复写文件”语义问题——P0 只保证**不会有两个执行者同时写**，重复写的彻底消除依赖 P1 的 checkpoint/effect。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| 批子会话 | `ai_chat_sessions` 中 `batch_id IS NOT NULL` 的行 |
| Turn | 一次 Prompt 派发到收敛（完成/失败/取消/暂停）的完整过程，落 `ai_chat_turns` |
| 执行权（ownership） | 某 turn 由谁驱动：batch worker、人工 continuation、或交互式发送（后者对批子会话禁止） |
| generation | 子会话的执行代数。每次 retry/resume/reexecute/continue 递增；旧 generation 的写回必须失败 |
| CAS 写入 | `UPDATE ... WHERE <期望状态与标志> RETURNING`，以 rowcount 决定后续计数 |
| drain | 停止接收新工作，等待运行中 turn 收敛或超时 |
| stale worker | 持有过期 generation/lease 的执行者，其写回必须被拒绝 |
| fail-closed | 校验环节不可证实时的默认结论是“不通过”，而非“放行” |

---

## 4. 总体设计

### 4.1 执行所有权模型

```text
                     ┌──────────────────────────────┐
                     │  ai_chat_sessions（子会话）    │
                     │  status / execution_generation│
                     │  active_turn_id（唯一）        │
                     └───────┬──────────────┬────────┘
                             │              │
        ┌────────────────────┘              └────────────────────┐
        ▼                                                        ▼
  batch worker（权威）                              人工 / 外部控制面
  claim → turn(accepted→running)                    continue / resume / retry
  写回必须带 generation                              → 只写 command，不直接 dispatch
        │                                                        │
        └──────────────► ai_chat_turns ◄──────────────────────────┘
                     （每 session 最多一个 accepted/running/recovering）
```

规则：

1. **worker 权威**：批子会话的执行只由 `BatchWorker._run_one` 驱动；人工与外部系统只能“入队意图”，不能直接 dispatch。
2. **单一 active turn**：`ai_chat_turns` 上对每个 `session_id` 至多一行处于 `accepted/running/recovering`（用部分唯一索引保证）。
3. **普通发送入口禁止**：`POST /ai/chat/sessions/<sid>/messages` 命中 `batch_id IS NOT NULL` 且子会话非终态时，返回 `409 BATCH_SESSION_CONTROLLED`，不写消息、不 dispatch。
4. **终态子会话的人工输入**：走批任务 continuation 端点（新增内部端点，见 §7.1），由 worker 串行消费。
5. **读路径不受限**：详情、消息、文件、SSE、abort/cancel 仍可用（用户需要观察和干预）。

### 4.2 控制命令优先级

沿用并强化既有约定（`server/utils/batch_engine.py:2012-2032` 已定义 cancel 优先于 pause）：

```text
cancel  >  pause  >  (worker 正常收口)
```

- 两个标志同时置位时按 cancel 处理；
- `resume` 必须同时清 `cancel_requested` 与 `pause_requested`（核查确认现状已满足，保留为回归约束）；
- 已进入终态的行不接受控制命令（返回 409 + `VERSION_CONFLICT`）。

### 4.3 子会话状态机

```text
                    ┌──────────── retry / resume / continue ────────────┐
                    ▼                                                   │
pending ──claim──► running ──┬──► completed                              │
   ▲                │        ├──► failed ──────────────────────────────┤
   │                │        ├──► cancelled                             │
   │                │        └──► paused ────────────────────────────────┘
   │                │
   └────────────────┴──► recovering（P1 引入；P0 仅保留 running→pending 重启语义）
```

约束（P0 生效）：

| 约束 | 说明 |
|---|---|
| 终态只能从 `running` 进入一次 | `completed/failed/cancelled/paused` 的写入必须带 `status='running'` 条件 |
| `paused` 非终态但不可被 claim | claim 只认 `pending`；paused 必须经 resume 回 pending |
| `cancelled` 计入 `failed` 聚合 | 既有约定（`server/utils/batch_repo.py:114-119`、`server/utils/batch_engine.py:1834-1858`），P0 不改 |
| 批次状态仍为派生值 | `status` 由 `done/failed/total` + paused 计数派生（`server/utils/batch_engine.py:320-366`、`server/utils/batch_repo.py:449-482`） |

---

## 5. 数据模型

### 5.1 新表 `ai_chat_turns`

沿用 `2026-09-16-ai-chat-p0-reliability-spec.md §7.1` 的字段设计，P0 落库：

```sql
CREATE TABLE IF NOT EXISTS ai_chat_turns (
  id                VARCHAR(100) PRIMARY KEY,
  session_id        VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  batch_id          VARCHAR(100) REFERENCES ai_chat_batches(id) ON DELETE CASCADE,
  user_id           VARCHAR(100) NOT NULL,
  client_request_id VARCHAR(100) NOT NULL,
  operation         VARCHAR(30)  NOT NULL,  -- send | continue | retry | resume | reexecute | command
  status            VARCHAR(30)  NOT NULL DEFAULT 'accepted',
  -- accepted | running | completed | failed | stopped | cancelled | paused | recovering
  retry_of          VARCHAR(100) REFERENCES ai_chat_turns(id) ON DELETE SET NULL,
  attempt_id        VARCHAR(100),           -- 关联 ai_execution_attempts.id（审计用，非锁）
  expected_generation BIGINT,
  error_code        VARCHAR(100),
  error_message     TEXT,
  started_at        TIMESTAMPTZ,
  last_event_at     TIMESTAMPTZ,
  finished_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_ai_chat_turns_client_req
  ON ai_chat_turns(session_id, client_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_ai_chat_turns_active
  ON ai_chat_turns(session_id)
  WHERE status IN ('accepted','running','recovering');
CREATE INDEX IF NOT EXISTS idx_ai_chat_turns_session_created
  ON ai_chat_turns(session_id, created_at DESC);
```

要点：

- 部分唯一索引 `uniq_ai_chat_turns_active` 是“同一子会话最多一个 active turn”的**数据库级**保证，不依赖应用层判断；
- `(session_id, client_request_id)` 唯一实现发送幂等：重复请求返回已有 turn；
- `attempt_id` 只做审计关联，**不得**作为并发闸门（`ai_execution_attempts` 的 `attempt_no` 分配本身是 `MAX+1`，非原子：`server/utils/execution_audit.py:112-114`）。

### 5.2 `ai_chat_sessions` 新增列

```sql
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS execution_generation BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS active_turn_id VARCHAR(100);
```

- `execution_generation`：每次 retry / resume / reexecute / continue 递增；
- `active_turn_id`：便于详情接口与诊断直接读取“当前谁在执行”，与 `ai_chat_turns` 的 active 行保持同源（由同一事务维护）。

### 5.3 新表 `ai_batch_worker_leases`

```sql
CREATE TABLE IF NOT EXISTS ai_batch_worker_leases (
  lease_key      VARCHAR(50) PRIMARY KEY,        -- 'batch' | 'scan'
  owner_id       VARCHAR(200) NOT NULL,          -- host:pid:instance_uuid
  fencing_token  BIGINT NOT NULL DEFAULT 1,
  acquired_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  lease_until    TIMESTAMPTZ NOT NULL
);
```

P0 语义（最小可用）：

- 启动时 `INSERT ... ON CONFLICT DO UPDATE ... WHERE lease_until < NOW()` 原子抢占；
- 抢不到 → 不启动 dispatcher，仅提供 Web API，并打印可告警日志；
- 运行中周期 heartbeat 续租；
- P0 **不做**跨实例接管（不因 lease 过期抢占别的实例），只解决“同一套部署里多个进程各自跑 worker”。

> 注：P0 保留 `_restart_audit`（`server/utils/batch_engine.py:774-784`）的 `running→pending` 行为，但**仅在持有 lease 时执行**。真正的恢复决策（recovering/orphaned/needs_review）在 P1 引入。

### 5.4 门禁状态列（对外可观测）

```sql
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS gate_status VARCHAR(30),   -- passed | failed | inconclusive | skipped
  ADD COLUMN IF NOT EXISTS gate_error TEXT,
  ADD COLUMN IF NOT EXISTS gate_checked_at TIMESTAMPTZ;
```

`gate_status` 与 `action_expectations.last_status` 并存：期望行是核对依据，本列是**该子任务的门禁结论**（含 inconclusive），供详情/对外 results 与前端展示使用。P0 不改 `action_expectations` 结构。

---

## 6. CAS 契约

### 6.1 终态写入

所有终态写入集中在 repo/engine 的一个内部函数（如 `batch_repo.transition_child`），禁止路由、worker、retry helper 各自直接写状态。统一形态：

```sql
UPDATE ai_chat_sessions
   SET status = %(new_status)s,
       error_message = %(error)s,
       last_message_preview = COALESCE(%(preview)s, last_message_preview),
       active_turn_id = NULL,
       gate_status = %(gate_status)s
 WHERE id = %(sid)s
   AND status = 'running'
   AND execution_generation = %(generation)s
   AND cancel_requested = %(expect_cancel)s
   AND pause_requested = %(expect_pause)s
RETURNING id, batch_id;
```

规则：

| 场景 | 期望条件 | rowcount=0 时 |
|---|---|---|
| 正常完成 | `status='running' AND NOT cancel AND NOT pause` | 重新读取标志，按 cancel/pause 落对应终态 |
| 失败 | 同上 | 同上 |
| 取消（worker 观察到 cancel） | `status='running' AND cancel_requested` | 若已终态则忽略（幂等） |
| 暂停（worker 观察到 pause） | `status='running' AND NOT cancel AND pause_requested` | 同上 |
| 取消 paused 子任务（路由同步落库） | `status='paused'` | 幂等忽略 |

**计数规则**：只有 `rowcount = 1` 时，才在同一事务内 `done+1` / `failed+1`；批次状态重算也必须在同一事务提交前完成，避免出现“计数已加、状态未算”的中间态被其它读路径看到。

### 6.2 控制命令的 CAS

| 操作 | 允许的前置状态 | 副作用 |
|---|---|---|
| `cancel_batch` | 非终态批次 | 对 `pending/running` 置 `cancel_requested`；对 `paused` 同步落 `cancelled` 并计入 failed |
| `pause_batch` | 非终态批次 | 对 `pending/running` 置 `pause_requested`；批次即时显示 paused |
| `resume_batch` | `paused` 或存在 `cancelled` 子任务 | `paused/cancelled → pending`；**同时清 `cancel_requested` 与 `pause_requested`**；`execution_generation + 1`；已开跑过的写 `continue_prompt` |
| `retry_failed` | 终态批次 | 仅 `failed → pending`（`status='failed'` 条件已在 `server/utils/batch_repo.py:679-685`，保留）；`execution_generation + 1`；**重算时必须保留 paused 语义**（§9） |
| `continue_child` | 终态子会话 | 单条 conditional UPDATE（`WHERE id/batch_id/user_id AND status IN ('completed','failed','cancelled')`）+ 同事务写 user 消息 + 递增 generation |
| `reexecute_child` | 终态子会话 | 同上，另清 `opencode_session_id`、删旧消息、回滚计数 |
| `resume_child` | `paused` 子会话 | `paused → pending`，清两个标志，递增 generation |
| `cancel_child` | `pending/running/paused` | pending → 直接 `cancelled`；running → 置 flag 由 worker 收口；**paused → 同步 `cancelled` 并计数** |

### 6.3 generation 语义

- 递增点：retry / resume（批或子）/ reexecute / continue；
- `_run_one` 在 claim 时把当时的 `execution_generation` 记入内存，所有写回（消息落库、终态、门禁、文件记录）都带该值；
- 写回不匹配 → 0 行，记一条 `stale_write` 日志（含 sid、期望/实际 generation），**不得**改状态、计数或消息；
- 消息落库（`_persist_conversation`）同样要校验：子会话已终态或 generation 变化时跳过写入，避免旧 turn 的消息污染新 turn 的对话历史。

---

## 7. API 契约

### 7.1 内部（JWT）

| 路由 | 变更 | 说明 |
|---|---|---|
| `POST /ai/chat/sessions/<sid>/messages` | **收紧** | `batch_id IS NOT NULL` 且 `status IN ('pending','running','paused')` → `409 BATCH_SESSION_CONTROLLED`，不写消息、不 dispatch |
| `POST /ai/chat/batches/<bid>/sessions/<sid>/continue` | **新增** | 人工 continuation：CAS 置 `pending` + 写 user 消息 + 递增 generation，返回 `202`；非终态 409 |
| `POST /ai/chat/batches/<bid>/sessions/<sid>/cancel` | **修正** | paused 子任务同步落 `cancelled`（§9） |
| 其余批端点 | 不变 | cancel/pause/resume/retry/append/reexecute 语义保留，内部改为走 CAS |

前端配合（`src/api/aiChatBatches.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`）：

- `canSend` 对非终态批子会话返回 false（composer 禁用 + 文案“该任务由后台执行器控制”）；
- 终态批子会话的发送改为调用 continue 端点；
- 子任务状态文案补 `paused`/`cancelled`（`AiChatView.vue` batchStatusLabel；TERMINAL_STATUSES 已与后端一致，不改）。

> 前端只是 UX，安全边界在后端。

### 7.2 对外（`/v1/ai-batches`）

| 路由 | 变更 | 说明 |
|---|---|---|
| `DELETE /<batchId>` | **收紧** | 非终态 → `409 BATCH_NOT_TERMINAL`；`?stop=true` → cancel + bounded drain（默认 10s）+ cleanup + delete；drain 超时 → `409 BATCH_DRAIN_TIMEOUT`，**保留任务与工作区** |
| `POST /` | **校验** | `actionChecks` 走 `agent_ledger.validate_checks`，非法 400 |
| `PATCH /<batchId>` | **扩展** | 新增可选 `actionChecks`（显式传入才更新，语义对齐内部 PATCH：`server/routes/ai_chat_batches.py:263-293`） |
| 其余端点 | 不变 | 字段白名单与密钥隔离保持（`server/routes/open_api_batches.py:217-236`） |

删除的完整语义：

```text
terminal                → cleanup workspace → delete
non-terminal, 无 stop   → 409 BATCH_NOT_TERMINAL（不产生任何副作用）
non-terminal, stop=true → cancel_batch → notify worker → 轮询至无 running（≤10s）
                          ├─ 收敛 → cleanup → delete → 200 {"deleted": true}
                          └─ 超时 → 409 BATCH_DRAIN_TIMEOUT（任务/工作区保留）
```

### 7.3 错误码

统一结构化错误（沿用 `2026-09-16-ai-chat-p0-reliability-spec.md §11`）：

```json
{
  "error": {
    "code": "BATCH_SESSION_CONTROLLED",
    "message": "批任务子会话正在由后台执行器控制，请使用批任务继续接口",
    "retryable": false,
    "operation": "send_message",
    "requestId": "req-...",
    "turnId": null
  }
}
```

P0 错误码表：

| code | HTTP | retryable | 触发 |
|---|---|---|---|
| `BATCH_SESSION_CONTROLLED` | 409 | false | 非终态批子会话走普通发送 |
| `TURN_ALREADY_RUNNING` | 409 | false | 同一子会话已有 active turn |
| `BATCH_NOT_TERMINAL` | 409 | false | 非终态批任务被直接删除 |
| `BATCH_DRAIN_TIMEOUT` | 409 | true | stop-and-delete 等待运行中子任务超时 |
| `STALE_EXECUTION_GENERATION` | 409 | false | 控制命令携带过期 generation |
| `ACTION_CHECK_INVALID` | 400 | false | `actionChecks` 校验失败（内外一致） |
| `ACTION_GATE_INCONCLUSIVE` | 200（任务态） | true | 门禁不可证实，子任务进 `needs_review` 语义 |
| `VERSION_CONFLICT` | 409 | true | 状态已被其它请求改变 |
| `OPENCODE_UNAVAILABLE` | 502 | true | OpenCode 不可达 |
| `WORKSPACE_CLEANUP_FAILED` | 500/207 | true | 工作区清理失败（不得伪装成功） |

---

## 8. 门禁 fail-closed

### 8.1 契约

对任一子任务，若其批任务（或模板）存在**生效的** `actionChecks`（即 `check_applies_to_child` 为真的条数 > 0）：

```text
登记期望行数 == 生效检查条数          否则 → gate_status='inconclusive'，不 completed
账本健康（ledger_ok == True）        否则 → inconclusive
核对查询成功                         否则 → inconclusive
逐条满足 min_count/require_state     否则 → failed（带缺失明细）
```

- `inconclusive` 的子任务：`status='failed'`（错误文案含“门禁无法证实，请人工复核”），`gate_status='inconclusive'`，对外 `/results` 的 `output` 保持 `null`（既有契约：`server/utils/batch_repo.py:699-747`）。
- 无生效检查（含未配置）→ `gate_status='skipped'`，正常 completed。
- 登记异常**不得**降级为 warning 后继续（现状 `server/utils/batch_engine.py:1100-1121`）。

### 8.2 必须修复的两处确定性缺陷

1. **`apply_to` 丢失**（P0-5）：`validate_checks` 的 normalized dict 必须保留并校验 `apply_to`（`batch_seq` 数组 / `input_file_glob`），且校验 `batch_seq` 元素为整数、`input_file_glob` 为字符串。**核查补充（2026-09-23）**：丢失发生在内部规范化路径——内部创建/编辑/模板入口先过 `validate_checks` 再入库，`apply_to` 落库前即被剥离，引擎过滤恒真；而外部 Open API 未校验透传路径反而保留原始 JSON。修复点在 `agent_ledger.validate_checks` 一处即可同时收敛两条路径。
2. **子代理账本 FK 顺序**（P0-6）：`_persist_conversation` 必须先 upsert `ai_chat_subtasks` 行，再写子代理的工具账本；或在账本写入失败时于同一轮重试一次。

### 8.3 门禁与执行审计的一致性

`gate_status` 变更同时写一条 `ai_execution_events`（`event_type='gate.evaluated'`，payload 含 check 名称与结论），保证审计时间线可解释；`execution_audit.record_event` 的 seq 分配问题属 P1（见 P1 Spec §5.3），P0 不阻塞。

---

## 9. 确定性缺陷修复清单

| # | 缺陷 | 位置 | 修复要求 | 回归测试 |
|---|---|---|---|---|
| F1 | 子代理账本先写后建行 | `server/utils/batch_engine.py:1729-1753` | 先 upsert subtask 行，再写账本 | 快速完成的多子代理任务账本无缺口 |
| F2 | `apply_to` 规范化丢失 | `server/utils/agent_ledger.py:274-280` | 保留 + 校验 | 创建 → 落库 → 派发全链路断言 |
| F3 | 门禁 inconclusive 被当通过 | `server/utils/batch_engine.py:1018-1035`、`server/utils/agent_ledger.py:492,522-523` | 见 §8.1 | 账本不健康 / 登记异常 / 零期望 三例 |
| F4 | Open API `actionChecks` 未校验 | `server/routes/open_api_batches.py:363-372` | 复用 `validate_checks` | 非法正则/形状 → 400 不落库 |
| F5 | paused 子任务无法单独取消 | `server/utils/batch_repo.py:363-382` | paused → 同步 `cancelled` + 计数 | 单子 cancel on paused |
| F6 | `append` 并发序号冲突 | `server/utils/batch_repo.py:429-443`、`server/init_db.py:706-707` | `UNIQUE(batch_id, batch_seq)` + 分配改为行锁/序列 | 并发 append 无重复序号 |
| F7 | `retry_failed` 覆盖 paused 聚合 | `server/utils/batch_repo.py:665-696` | 重算走统一函数（含 paused 规则） | failed+paused 混合批次状态正确 |
| F8 | 软删后 results/usage 口径不一致 | `server/utils/batch_repo.py:154-167` vs `:699-747`、`:787-808` | 统一 `deleted_at IS NULL`（用户视图） | 软删后 results/usage 不含该子任务 |
| F9 | 重试后 attempt 悬挂 | `server/utils/batch_engine.py:1023-1029`、`:1049-1053` | requeue 前 `finish_latest_running(status='recovering')`，新 attempt 记 `parent_attempt_id` | 终态子任务无 running attempt |
| **F10** | 前端子任务状态文案缺项（**核查修正**：终态集合无需改动，与后端一致；仅文案缺失） | `src/views/ai-chat/AiChatView.vue` `batchStatusLabel` | 补 `paused`/`cancelled` 文案映射（不动 TERMINAL_STATUSES） | vitest |
| ~~F11~~ | **核查后撤销**：`resume_batch`/`resume_child` 已同时清 `cancel_requested` 与 `pause_requested`（`server/utils/batch_repo.py` 332-339、588-594 行），引擎侧 `_maybe_auto_retry`/`_maybe_gate_retry`/`_requeue_lost` 亦然。无需修复；补一条回归断言防回退即可 | `server/utils/batch_repo.py` | 无行为变更 | resume 后两标志均为 false 的断言 |
| F12 | 审计 retention 任务重复注册 | `server/app.py:190-196`（SkillOpt 启动块内自建 scheduler）与 `:335-348`（`_start_audit_retention_job` 再建一个）——`replace_existing` 只在同一 scheduler 内生效 | 只保留一处注册 | 启动后只有一个 03:17 清理作业 |

---

## 10. 迁移与兼容

### 10.1 迁移清单（幂等）

1. `ai_chat_turns` 建表 + 三个索引（§5.1）；
2. `ai_chat_sessions` 增列 `execution_generation` / `active_turn_id` / `gate_status` / `gate_error` / `gate_checked_at`；
3. `ai_batch_worker_leases` 建表；
4. `UNIQUE(batch_id, batch_seq)`：**先回填去重**（历史并发 append 可能已产生重复），再建约束；重复行按 `created_at, id` 保留最早者、其余 +1000 偏移并记录日志；
5. 新迁移必须注册进 `server/app.py` 启动钩子（既有约定，见 `server/app.py:246-280`）。

### 10.2 兼容

| 项 | 策略 |
|---|---|
| 存量 `running` 子任务 | 启动时按现状 `running → pending`（持 lease 时），`execution_generation` 保持 0，首次重跑即走新语义 |
| 存量批次无 `ai_chat_turns` 行 | 不伪造历史 turn；详情/审计对无 turn 的会话显示“历史执行（无 turn 记录）” |
| 外部调用方 | `DELETE` 行为收紧是**破坏性变更**，必须同步更新 `docs/user-guide/integration/ai-batch-api.md` 与 `openapi.yaml`，并在文档标注生效版本 |
| 前端旧缓存 | `cancelled` 终态化只影响轮询停止，无破坏 |

### 10.3 回滚

- 新增列与表全部可保留（不删列）；
- 行为开关：`AI_BATCH_STRICT_OWNERSHIP=1`（默认开）可临时降级为“只告警不 409”，用于灰度排查；
- 门禁 fail-closed 不建议提供降级开关（这正是本 Spec 的核心价值），如需临时放行用批级 `gate_retry`/人工重试。

---

## 11. 测试要求

### 11.1 后端（pytest）

新增 `server/tests/test_batch_execution_safety_p0.py`：

1. **ownership**：本人 pending/running/paused 子会话走普通 send → 409 + 无消息 INSERT + 无 OpenCode 调用；他人/不存在 → 404；终态走内部 continue → 202。
2. **turn 幂等**：相同 `clientRequestId` 两次 → 只有一个 turn、一次 dispatch；并发两次 continuation → 一成功一 409。
3. **CAS**：并发 `_mark_done` 两次 → 计数只 +1；`cancel` 与 `_mark_done` 并发 → 终态唯一（矩阵见 §12）。
4. **generation**：reexecute 后旧 generation 写回 0 行；旧 turn 的消息不落入新 turn。
5. **删除**：Open API 非终态 DELETE → 409 且库/工作区保留；`stop=true` → drain 后删除；模拟 worker 不收敛 → 409 `BATCH_DRAIN_TIMEOUT`。
6. **门禁**：非法 actionChecks 400；登记异常 → 不 completed；零期望 → 不 completed；`apply_to` 全链路；快速完成多子代理账本完整。
7. **lease**：两个 worker 实例同时 `start()` → 只有一个 dispatcher 运行（可用真实 DB + advisory/lease 断言）。
8. **F1–F12 每行至少一例**。

既有套件回归：`test_batch_engine.py`、`test_batch_auto_retry.py`、`test_batch_pause_and_guards.py`、`test_batch_resume.py`、`test_batch_routes.py`、`test_open_api_batches_*.py`、`test_agent_action_gate.py`、`test_execution_audit.py`。

### 11.2 前端（vitest）

- 非终态批子会话 composer 禁用；
- 终态发送走 continue API；
- `cancelled` 停止轮询；
- 状态文案含 paused/cancelled。

### 11.3 E2E（Playwright，真实链路）

在 `e2e/ai-full/` 增加 `ai-harness-p0-safety.spec.ts`：

1. 创建批任务 → 运行中尝试普通发送被拒（UI + API 双断言）；
2. 子任务失败后 continuation 修正并保留历史；
3. Open API `stop-and-delete` 真实链路（含 drain 观察）；
4. cancel/pause 与完成竞态（构造快完成的小任务）。

> 注意既有 E2E 约束：`/ai-chat` 页有常驻 SSE，禁止 `networkidle`；`request.fetch` 传字符串 body 必须显式 `Content-Type`。

### 11.4 生产代理验证

新增/变更的删除与 continue 路由需经 `server/proxy.py`（:8080）实测一次，确认无 SSE 相关回归（本 Spec 不新增 SSE）。

---

## 12. 验收标准

**不变量（必须全部成立）**

- [ ] 任一子会话同时最多一个 active turn（DB 部分唯一索引可验证）；
- [ ] 普通发送入口无法驱动非终态批子会话；
- [ ] 终态写入全部 CAS；批次计数 = 未删除子任务状态聚合；
- [ ] 旧 generation / 旧 turn 的写回不改变任何状态、计数与消息；
- [ ] 非终态批任务在内外两个入口都无法被直接删除；
- [ ] 非空 actionChecks 且不可证实时，子任务不 `completed`，`/results` output 为 null；
- [ ] 多进程部署只有一个 batch/scan worker 实例；
- [ ] 终态子任务不存在 `running` 状态的 attempt。

**竞态矩阵（每格一条测试）**

| 并发对 | 期望 |
|---|---|
| pause ↔ cancel | cancel 优先，落 `cancelled` |
| cancel ↔ worker 完成 | 唯一终态；若 cancel 先成功则 `cancelled`，否则 `completed` 且 cancel 幂等无副作用 |
| cancel ↔ resume | 不得出现 `pending` 且 `cancel_requested=true` 的僵死组合 |
| retry ↔ auto-retry | 只重排一次，`retry_count` 只 +1 |
| retry ↔ worker 失败 | 计数不重复 |
| reexecute/continue ↔ stale worker | 旧写回 0 行 |
| append ↔ delete | 要么 append 后删除、要么删除后 append 失败，无半清理 |

---

## 13. 涉及文件

**新增**

- `server/migrations/2026_09_23_harness_p0_execution_safety.py`（表/列/约束，幂等）
- `server/tests/test_batch_execution_safety_p0.py`
- `e2e/ai-full/ai-harness-p0-safety.spec.ts`
- `src/views/ai-chat/__tests__/AiChatView.test.ts`（或等价 store 测试）

**修改**

- `server/utils/batch_repo.py`（CAS 转移、continue/resume/cancel 修正、append 序号、retry 重算、软删口径）
- `server/utils/batch_engine.py`（generation 校验、终态 CAS、门禁 fail-closed、attempt 收口、lease 启动）
- `server/utils/agent_ledger.py`（`apply_to` 保留与校验）
- `server/routes/ai_chat.py`（批子会话发送门禁）
- `server/routes/ai_chat_batches.py`（新增 continue 端点、cancel_child 修正）
- `server/routes/open_api_batches.py`（删除 stop-first、actionChecks 校验、PATCH 扩展）
- `server/app.py`（lease 获取、迁移注册、retention 去重）
- `server/init_db.py`（新表 DDL 同步，保持“迁移是唯一事实来源”约定）
- `src/api/aiChatBatches.ts`、`src/stores/aiChatBatches.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`
- `docs/user-guide/integration/ai-batch-api.md`、`docs/user-guide/integration/openapi.yaml`

---

## 14. 拆 PR 建议

| PR | 内容 | 依赖 |
|---|---|---|
| **PR-1** | 迁移 + `ai_chat_turns` + generation + CAS 转移函数 + 单测 | 无 |
| **PR-2** | 普通发送门禁 + 内部 continue 端点 + 前端 composer/continue 收敛 | PR-1 |
| **PR-3** | Open API 删除 stop-first + actionChecks 校验 + 文档/openapi 同步 | PR-1 |
| **PR-4** | 门禁 fail-closed（含 F1/F2/F3）+ 门禁回归 | PR-1 |
| **PR-5** | worker lease + F6/F7/F8/F11/F12 修复 + E2E | PR-1 |

每个 PR 必须：`npm run build` 干净、`server/tests` 相关子集全绿、涉及 UI 的附 Playwright 证据。

---

## 15. 依赖与阻断关系

- 本 Spec 是 `2026-09-23-ai-harness-p1-durable-execution-spec.md` 的**前置**：P1 的 lease/heartbeat/checkpoint 必须建立在 P0 的 generation + CAS 之上；P0 未完成前不进入 P1 的恢复改造。
- `2026-09-23-ai-harness-p2-orchestration-runtime-spec.md` 不得绕过 P0 的 ownership/CAS 直接驱动子会话。
- 与 `2026-09-11-batch-session-parity-design.md` 的关系：本 Spec 交付该设计要求的后端权威边界与 composer 门禁。
- 与 `2026-09-16-ai-chat-p0-reliability-spec.md` 的关系：复用其 `ai_chat_turns` 与错误模型定义，本 Spec 负责批任务执行侧的落地。
