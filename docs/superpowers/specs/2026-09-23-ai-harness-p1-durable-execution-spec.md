# AI Harness P1 持久化执行与控制面 Spec

**版本**：v1.0
**状态**：待评审
**日期**：2026-09-23
**优先级**：P1
**前置依赖**：`2026-09-23-ai-harness-p0-execution-safety-spec.md`（ownership / CAS / generation / 删除保护 / 门禁 fail-closed）
**范围**：批任务与 AI 扫描子会话的执行租约、attempt 生命周期、checkpoint、effect 账本、事件流与投递、外部控制命令、资源预算、管理面观测
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 背景

P0 之后，系统具备“同一子会话只有一个执行者、终态只收口一次、删除不产生幽灵执行”的安全基线。但 P0 刻意没有解决的是一组**跨进程、跨重启、跨网络不确定性的可靠性问题**：

| # | 现状 | 证据 | 后果 |
|---|---|---|---|
| P1-1 | worker 是进程内单例 + 内存集合；重启无条件把 `running` 重置为 `pending` | `server/utils/batch_engine.py:239-246`、`:585-605`、`:774-784` | 崩溃后**重放**而非断点恢复：已产生的文件/业务副作用可能被重做 |
| P1-2 | 对账器只能按“是否属于本进程线程表”判断孤儿 | `server/utils/batch_engine.py:1931-2010` | 跨实例无法安全接管；无 lease/heartbeat/fencing 概念 |
| P1-3 | 恢复依赖提示词“已完成的部分不要重做” | `docs/design/ai/AI批任务容灾与恢复机制.md:179-199`、`server/utils/batch_engine.py:576-579` | 文档已自认“续跑不是强幂等”，没有断点续算 |
| P1-4 | 重试/门禁重试直接 return，旧 attempt 悬挂 | `server/utils/batch_engine.py:1023-1029`、`:1049-1053` | attempt 永久 `running`，耗时/成本统计失真 |
| P1-5 | 审计事件序号用 `MAX(event_seq)+1` | `server/utils/execution_audit.py:206-237` | 并发写同 attempt 会唯一冲突并被 `_safe` 吞掉（`:63-73`），事件静默丢失 |
| P1-6 | 批任务无事件流，前端 5s/10s 盲轮询 | `src/stores/aiChatBatches.ts:28-42,66-82`；`docs/design/ai/13-AI批任务实时进度与编排设计.md:11-17` 的 `progress_version` 设计未实现 | 断线只能重拉快照，无 cursor 回放；状态变化最坏延迟 10s |
| P1-7 | 完成回调是 daemon thread 直接 POST | `server/utils/batch_engine.py:369-398` | 进程退出即丢；每次终态转换都发；无 event id、无投递记录、无去重 |
| P1-8 | 外部 API 只有 cancel/retry/append/continue/reexecute，无 pause/resume/child cancel | `server/routes/open_api_batches.py:512-860`；对比内部 `server/routes/ai_chat_batches.py:344-472` | 外部系统发现需要让路时只能 cancel，且无法恢复原批次 |
| P1-9 | 对外进度维度只有 `status/total/done/failed` 四个聚合计数，错误是 ≤500 字纯字符串（**核查修正**：`_batch_out` 白名单另含 name/agent/model/usage 等静态字段，非"只有四个字段"；但进度/错误形态的核心缺口成立） | `server/routes/open_api_batches.py:217-236`、`server/utils/batch_engine.py:1049-1067` | 调用方无法判断可重试性、阶段、重试历史与证据 |
| P1-10 | 并发固定 3，无租户/批次预算 | `server/utils/batch_engine.py:545,588,623`；`server/db.py:13`（每进程 20 连接） | 单用户可占满执行槽；token/cost 只有统计没有硬限制 |
| P1-11 | workspace 与产物不进入备份，长期累积 | `server/utils/backup.py:24-26`（注释明确 AI workspace 是临时数据、故意不备份）；`AI_WORKSPACE_QUOTA_MB` 为死配置零使用（**核查修正**：`AI_SESSION_TTL_HOURS` 有使用但仅用于 MCP 会话令牌过期，与工作区生命周期无关） | DB 恢复后产物可能不存在；磁盘无回收 |

P1 的目标是把 P0 的“安全收口”升级为**可证明的持久化执行协议**：谁在执行、执行到哪、产生了哪些副作用、重启后如何安全继续、外部如何观察与控制。

---

## 2. 目标与非目标

### 2.1 目标

1. **执行租约**：`lease_owner / lease_until / heartbeat_at / fencing_token` 落库；跨实例可安全接管，旧 owner 写回全部被拒。
2. **恢复决策**：重启不再无条件重放；按 lease、OpenCode 会话、checkpoint、effect 状态分流到 `continue / recover_from_checkpoint / needs_review / failed`。
3. **attempt 闭环**：每次 retry/continue/reexecute 关闭旧 attempt 并建 `parent_attempt_id` 链；同一 session 至多一个 running attempt。
4. **checkpoint 与 effect**：回合级/子任务级 checkpoint 可持久化与恢复；副作用有 `idempotency_key` 与 `committed/unknown` 状态，未知状态不得自动重放。
5. **事件流**：批次事件表 + 原子 seq + cursor；内部 SSE 主通道，断线按 `Last-Event-ID` 补发；对外提供事件读取与命令。
6. **可靠投递**：callback/webhook 走 outbox，与状态提交同事务；投递可重试、可去重、可人工重放。
7. **外部控制面**：pause/resume/child cancel/命令查询 + `Idempotency-Key`；对外错误结构化（code/retryable/phase/attempt/evidenceRefs）。
8. **资源预算**：批次/用户/API Key 维度的 wall-clock、token、cost、工具调用、子代理、workspace 字节预算，超限进入明确状态并留事件。
9. **管理面可观测**：attempt timeline、lease/heartbeat、gate 证据、投递状态、预算消耗。

### 2.2 非目标

- 不做 Step/DAG 编排、人工审批状态机、Artifact Store、容器 runtime（P2）。
- 不替换 OpenCode runtime，不改变 `opencode_client` 调用形态。
- 不实现通用消息队列/Redis 依赖；租约与 outbox 均以 PostgreSQL 为唯一事实源（延续项目“不引入额外中间件”的取向，见 `docs/design/ai/13-AI批任务实时进度与编排设计.md:50-62`）。
- 不承诺 LLM 请求 exactly-once；只承诺**业务副作用**按 effect key 幂等（见 §4.3）。
- 不重写既有交互式会话 SSE 链路（`server/utils/chat_persist.py`、`server/routes/ai_chat.py:1179-1261`）。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| Lease | 执行权的时间受限授予；到期前由 owner 续租 |
| Fencing token | 单调递增的执行代数，旧 owner 的写回携带旧 token 时被拒绝 |
| Checkpoint | 执行进度快照（回合级/子任务级）：消息水位、workspace manifest hash、已完成 effect 集合 |
| Effect | 一次对外部世界的可观测副作用（DB 写入、文件导入、外部 HTTP、回调投递） |
| Effect key | 副作用的幂等键，形如 `(scope_id, idempotency_key)` |
| Command | 外部/人工发起的控制意图（pause/resume/cancel/retry/continue/reexecute/force-stop） |
| Outbox | 与业务事务同提交的待投递事件表，由独立投递器消费 |
| Event cursor | 客户端消费事件的位置标识（批次事件 seq 或 opaque cursor） |
| needs_review | 无法自动安全继续的终态，需人工判断（不得自动重放） |

---

## 4. 总体设计

### 4.1 执行租约与接管

```text
Executor A（owner）                    PostgreSQL                       Executor B
  acquire lease ──────────────────────► ai_batch_worker_leases
  claim child（写 generation+fencing）─► ai_chat_sessions
  heartbeat（周期续租）───────────────► lease_until = now()+TTL
  写回（WHERE fencing_token = mine）──► rowcount 1 才生效
                                        ◄──── lease 过期后 acquire
                                               （fencing_token += 1）
  迟到写回（旧 token）────────────────► rowcount 0 → 记 stale_write，丢弃
```

规则：

- **接管条件**：仅当 `lease_until < NOW()` 时才允许 `fencing_token += 1` 并夺取；
- **写回校验**：所有对 child 的状态/计数/消息/文件/effect/checkpoint 写入都必须带 `fencing_token` 与 `execution_generation`；
- **心跳可见**：管理面与对外 detail 暴露 `lastHeartbeatAt`（不含 owner 内部标识）；
- **TTL 取值**：默认 90s，心跳 20s；与既有对账周期（`AI_BATCH_RECONCILE_SEC`，`server/utils/batch_engine.py:631`）解耦，避免互相干扰。

### 4.2 恢复决策（替代“重启即重放”）

进程/实例启动、或对账器发现无主 running 行时，按顺序判定：

| 条件 | 决策 | 说明 |
|---|---|---|
| lease 未过期 | **等待** | 原 owner 仍可能在工作；不重排、不误杀 |
| lease 过期 + OpenCode 会话存在 + checkpoint 完整 | **continue** | 复用原 OpenCode session 与 workspace，注入续跑提示词（沿用 `AUTO_RETRY_CONTINUE_PROMPT`，`server/utils/batch_engine.py:576-579`） |
| lease 过期 + OpenCode 会话存在 + 无 checkpoint | **recover_from_checkpoint = 无 → needs_review** | 无法证明进度时不得自动重发原始 prompt |
| lease 过期 + OpenCode 会话 404/丢失 + checkpoint 存在 | **rebuild** | 新建 OpenCode 会话 + 注入历史摘要（沿用 `_recover_session`，`server/utils/batch_engine.py:991-1006`）+ 从 checkpoint 之后的步骤继续 |
| lease 过期 + 存在 `unknown` 状态的 effect | **needs_review** | 副作用可能已发生但未确认 |
| OpenCode 整体不可达 | **跳过本轮** | 沿用既有语义（`server/utils/batch_engine.py:1975-1976`） |
| 无 checkpoint 且无副作用记录 + 未产生任何消息 | **failed(retryable)** | 明确失败优于静默重放 |

`needs_review` 是终态，但可被人工 command（`retry` / `reexecute`）拉回。

### 4.3 副作用幂等

不能承诺“LLM 请求只发一次”（HTTP 超时可能发生在 OpenCode 已接收之后，见 `server/utils/batch_engine.py:991-1006` 的恢复路径）。因此承诺改为：

```text
Agent turn 可以重试
业务副作用必须按 effect key 幂等
```

| 副作用类型 | effect key 组成 | 幂等实现 |
|---|---|---|
| MCP 数据写入（`data_create/update/delete_record`） | `(session_id, step_key, row_id, payload_hash)` | 内部转发端点按 key 去重（复用既有应用层路由校验：`server/routes/ai_data_internal.py`） |
| 文件导入 `data_files` | `(session_id, source_path, content_hash)` | 既有 `import_recorded_files` 幂等语义（`server/routes/open_api_batches.py:452-487`） |
| 扫描任务回写 | `(scan_task_id, source_record_id, field, value_hash)` | 既有 `jsonb_set` 回写路径 + 记录级原子认领 |
| 外部回调 | `event_id` | outbox + 消费方按 event_id 去重 |
| 产物写盘 | `(session_id, rel_path, content_hash)` | checkpoint 记录已提交产物清单 |

写入时序：

```text
1. 记录 effect(status='planned', idempotency_key=...)
2. 执行副作用
3. 成功 → status='committed'（同事务记录 external_ref/result_hash）
   失败 → status='failed'（可安全重试）
   超时/连接中断等不确定 → status='unknown'（进入 needs_review，禁止自动重放）
```

### 4.4 控制命令平面

所有控制意图（内部 UI、外部 API、调度器）统一落 `ai_execution_commands`，由 worker 消费：

```text
命令入口（内部/外部）
  → 校验归属 + expected_generation + Idempotency-Key
  → INSERT command(status='accepted')
  → notify worker
worker 消费
  → 校验 generation/lease
  → 应用（CAS 状态转移，见 P0 §6）
  → command.status = applied | rejected | expired | failed + result_snapshot
```

同一 `idempotency_key` 重复提交返回同一 command（不重复执行）。

---

## 5. 数据模型

### 5.1 扩展 `ai_execution_attempts`

```sql
ALTER TABLE ai_execution_attempts
  ADD COLUMN IF NOT EXISTS lease_owner      VARCHAR(200),
  ADD COLUMN IF NOT EXISTS lease_until      TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token    BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS queue_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS dispatch_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS recovery_reason  VARCHAR(50),
  ADD COLUMN IF NOT EXISTS checkpoint_id    VARCHAR(100),
  ADD COLUMN IF NOT EXISTS budget_snapshot  JSONB;

-- 状态扩展（原表 CHECK 需幂等替换）
-- accepted | claimed | running | recovering | orphaned | completed | failed | stopped | needs_review
```

不变量（用部分唯一索引强制）：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uniq_attempt_running_per_session
  ON ai_execution_attempts(session_id)
  WHERE status IN ('claimed','running','recovering');
```

### 5.2 扩展 `ai_chat_sessions`（执行租约）

租约的持有者是**子会话行**（claim 单元就是它，见 §6.1）；attempt 上的租约字段是审计视角，不是并发闸门。

```sql
ALTER TABLE ai_chat_sessions
  ADD COLUMN IF NOT EXISTS lease_owner    VARCHAR(200),
  ADD COLUMN IF NOT EXISTS lease_until    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token  BIGINT NOT NULL DEFAULT 0;
```

- `execution_generation`（P0 §5.2）表达“第几代执行”，`fencing_token` 表达“第几次认领”；两者都参与写回校验，任一不匹配即拒绝；
- 存量 `running` 行回填 `lease_owner=NULL` / `fencing_token=0`，使其可被首次接管（§11.1）。

### 5.3 新表 `ai_execution_checkpoints`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_checkpoints (
  id                     VARCHAR(100) PRIMARY KEY,
  session_id             VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  attempt_id             VARCHAR(100) REFERENCES ai_execution_attempts(id) ON DELETE SET NULL,
  execution_generation   BIGINT NOT NULL DEFAULT 0,
  checkpoint_type        VARCHAR(30) NOT NULL,   -- dispatch | progress | turn_complete | recovery | manual
  message_seq            INTEGER,                -- ai_chat_messages.seq 水位
  opencode_session_id    VARCHAR(100),
  workspace_manifest_hash VARCHAR(64),
  completed_effect_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
  artifact_refs          JSONB NOT NULL DEFAULT '[]'::jsonb,
  context_snapshot       JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_latest              BOOLEAN NOT NULL DEFAULT TRUE,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_checkpoint_latest
  ON ai_execution_checkpoints(session_id) WHERE is_latest;
CREATE INDEX IF NOT EXISTS idx_checkpoint_session_created
  ON ai_execution_checkpoints(session_id, created_at DESC);
```

写入时机：派发前（`dispatch`）、每次进度落库（`progress`，节流与 `PROGRESS_PERSIST_SEC` 对齐，`server/utils/batch_engine.py:553`）、回合收敛（`turn_complete`）、恢复动作后（`recovery`）。写入为 `INSERT + 旧行 is_latest=false`，同一事务完成。

**边界（必须在文档与代码注释中写明）**：P1 的 checkpoint 是**子任务/回合级**，不是业务步骤级断点。它解决的是“重启后知道执行到哪、哪些 effect 已提交”，不解决“跨小时业务 DAG 的中间步骤跳过”（P2）。

### 5.4 新表 `ai_execution_effects`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_effects (
  id              VARCHAR(100) PRIMARY KEY,
  session_id      VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  attempt_id      VARCHAR(100),
  batch_id        VARCHAR(100),
  step_key        VARCHAR(100),                 -- P1 为 'turn:<n>'；P2 为 step id
  effect_type     VARCHAR(50) NOT NULL,         -- mcp_write | file_import | scan_writeback | callback | artifact
  idempotency_key VARCHAR(200) NOT NULL,
  request_hash    VARCHAR(64),
  status          VARCHAR(30) NOT NULL DEFAULT 'planned',
  -- planned | started | committed | failed | unknown | compensated
  external_ref    TEXT,
  result_hash     VARCHAR(64),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  committed_at    TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_effect_scope_key
  ON ai_execution_effects(session_id, effect_type, idempotency_key);
```

### 5.5 新表 `ai_execution_commands`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_commands (
  id                  VARCHAR(100) PRIMARY KEY,
  idempotency_key     VARCHAR(200) NOT NULL,
  batch_id            VARCHAR(100),
  session_id          VARCHAR(100),
  command_type        VARCHAR(30) NOT NULL,
  -- pause | resume | cancel | retry | continue | reexecute | force_stop | approve（P2 使用 approve）
  requested_by        VARCHAR(100),
  requested_by_kind   VARCHAR(20) NOT NULL DEFAULT 'user',  -- user | api_key | system
  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
  expected_generation BIGINT,
  status              VARCHAR(30) NOT NULL DEFAULT 'accepted',
  -- accepted | applied | rejected | expired | failed
  result_snapshot     JSONB,
  error_code          VARCHAR(100),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  applied_at          TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_command_idem
  ON ai_execution_commands(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_command_batch_created
  ON ai_execution_commands(batch_id, created_at DESC);
```

### 5.6 新表 `ai_batch_events`

```sql
CREATE TABLE IF NOT EXISTS ai_batch_events (
  batch_id     VARCHAR(100) NOT NULL,
  event_seq    BIGINT NOT NULL,
  event_id     VARCHAR(100) NOT NULL,
  event_type   VARCHAR(50) NOT NULL,
  -- batch.created | batch.status | child.status | child.progress | gate.evaluated
  -- batch.paused | batch.resumed | batch.cancelled | batch.completed | budget.exceeded
  -- command.applied | delivery.sent | delivery.failed
  aggregate_type VARCHAR(30) NOT NULL,   -- batch | child | command | delivery
  aggregate_id   VARCHAR(100),
  execution_generation BIGINT,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (batch_id, event_seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_batch_event_id ON ai_batch_events(event_id);
CREATE INDEX IF NOT EXISTS idx_batch_events_created ON ai_batch_events(batch_id, created_at DESC);
```

**seq 分配**：`SELECT COALESCE(MAX(event_seq),0)+1 ... FOR UPDATE` 加批次行锁，或为批次维护计数列原子自增；**禁止**无锁 `MAX+1`（这是 P1-5 的根因，同样适用于 `ai_execution_events`）。

### 5.7 新表 `ai_delivery_outbox`

```sql
CREATE TABLE IF NOT EXISTS ai_delivery_outbox (
  id              VARCHAR(100) PRIMARY KEY,
  event_id        VARCHAR(100) NOT NULL,
  batch_id        VARCHAR(100) NOT NULL,
  event_type      VARCHAR(50) NOT NULL,
  target_url      TEXT NOT NULL,
  payload         JSONB NOT NULL,
  signature       TEXT,
  idempotency_key VARCHAR(200) NOT NULL,
  status          VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | sending | delivered | failed | dead_letter
  attempt_count   INT NOT NULL DEFAULT 0,
  next_retry_at   TIMESTAMPTZ,
  last_error      TEXT,
  delivered_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_outbox_event
  ON ai_delivery_outbox(event_id, target_url);
CREATE INDEX IF NOT EXISTS idx_outbox_due
  ON ai_delivery_outbox(status, next_retry_at) WHERE status IN ('pending','failed');
```

批次终态提交与 outbox 插入必须在**同一事务**；投递器独立消费（见 §7）。

### 5.8 扩展 `ai_batch_worker_leases`

P0 建立的租约表在 P1 增加接管语义：

```sql
ALTER TABLE ai_batch_worker_leases
  ADD COLUMN IF NOT EXISTS lease_kind VARCHAR(30) NOT NULL DEFAULT 'dispatcher';
  -- dispatcher | delivery | scheduler
```

`delivery` 与 `scheduler` 各自独立 lease key，保证 outbox 投递器与定时扫描也是单实例。

---

## 6. 恢复协议

### 6.1 claim（P0 claim 的扩展）

```sql
WITH picked AS (
  SELECT s.id
    FROM ai_chat_sessions s
   WHERE s.status = 'pending'
     AND s.deleted_at IS NULL
     AND (s.batch_id IS NOT NULL OR s.api_key_id IS NOT NULL)
     AND NOT EXISTS (SELECT 1 FROM ai_chat_batches b
                      WHERE b.id = s.batch_id AND b.paused)
   ORDER BY s.created_at, s.batch_seq
   FOR UPDATE SKIP LOCKED OF s
   LIMIT %s
)
UPDATE ai_chat_sessions s
   SET status = 'running',
       lease_owner = %(owner)s,
       lease_until = NOW() + %(ttl)s,
       fencing_token = s.fencing_token + 1,
       heartbeat_at = NOW()
  FROM picked
 WHERE s.id = picked.id
RETURNING s.*;
```

说明：

- `AND NOT EXISTS (... b.paused)` 同时修复 P0 遗留的“暂停批次追加后新子任务被运行”问题（若 P0 未采用该修法，P1 必须补上）；
- `fencing_token` 每次 claim 递增，天然实现“旧 owner 写回失效”。

### 6.2 heartbeat

worker 对每个运行中 child 每 20s 续租：

```sql
UPDATE ai_chat_sessions
   SET heartbeat_at = NOW(), lease_until = NOW() + %(ttl)s
 WHERE id = %s AND lease_owner = %s AND fencing_token = %s;
```

`rowcount = 0` → 执行权已被接管，当前 worker 必须立刻停止该 child 的工作（abort OpenCode 会话，丢弃后续写回）。

### 6.3 对账与接管

对账器（替代 `_reconcile_stale_running`，`server/utils/batch_engine.py:1931-2010`）每轮：

```text
for each child where status='running':
    if lease_until > NOW():            skip（有人在工作）
    elif opencode 404:                 failed（准确原因，沿用既有文案）
    elif opencode 不可达:               skip
    else:                              按 §4.2 决策表分流
                                       continue → pending + continue_prompt + generation 不变
                                       rebuild  → pending + 保留 oc 绑定由 _recover_session 重建
                                       needs_review → 终态 + 事件 + 通知
```

每次接管写 `ai_batch_events`（`event_type='child.recovered'`）与 attempt 的 `recovery_reason`。

### 6.4 删除与 force-stop 的 drain 语义（P1 加强）

P0 的 stop-and-delete 在 P1 增加 fencing：

1. 写 `command(cancel)`；
2. 等 `lease_until` 过期或 owner 主动释放（`lease_owner=NULL`）；
3. 确认无 `running` attempt 且无 `pending` effect；
4. 才允许 cleanup + delete；超时返回 409（不删除）。

`force_stop` 命令额外允许管理员在 drain 超时后强制：置 `fencing_token += 1`、attempt → `stopped`、child → `cancelled`，并记录操作审计（`log_operation`）。

---

## 7. 事件流与可靠投递

### 7.1 事件写入

任何批/子任务状态变化在同一事务内：

1. 更新 `ai_chat_sessions` / `ai_chat_batches`；
2. 追加 `ai_batch_events`（原子 seq）；
3. 若为终态且配置了 callback → 插入 `ai_delivery_outbox`。

前端与外部读的是事件表，worker 不再“顺带”承担推送职责。

### 7.2 内部 SSE

新增（对齐 `docs/design/ai/13-AI批任务实时进度与编排设计.md:128-177` 的协议设计，但事实源改为事件表）：

```text
GET /ai/chat/batches/events?ids=<id1>,<id2>&access_token=<jwt>
```

- 帧类型：`batch_event`（带 `eventId`/`eventSeq`）、`batch_done`、`: ping`（15s）；
- 断线重连：前端携带 `Last-Event-ID`（或 `?afterSeq=`），服务端从该位置补发；
- 轮询保留为降级路径（5s 详情 / 10s 列表）；
- 服务端 30 分钟硬上限关流，防泄漏；
- 归属校验沿用 `login_required_sse` + 逐 id 归属过滤（非归属静默剔除）。

### 7.3 对外事件读取

```text
GET /v1/ai-batches/<batchId>/events?afterSeq=<n>&limit=<n>
→ 200 {
    "batchId": "...",
    "events": [{"eventId":"...","eventSeq":12,"type":"child.status","at":"...","data":{...}}],
    "nextAfterSeq": 12,
    "hasMore": false
  }
```

- 只暴露白名单字段（不泄漏 `opencode_session_id`/`workspace_path`/内部 user id）；
- `afterSeq` 语义：返回 `> afterSeq` 的事件；客户端按 `eventId` 幂等合并；
- 事件保留：与 `ai_execution_events` 的保留策略一致（默认行 180 天，见 `docs/design/ai/AI执行审计能力说明.md:203-209`），过期后 `afterSeq` 过旧返回 `CURSOR_EXPIRED`。

### 7.4 投递器（outbox consumer）

- 独立线程/进程，持 `lease_kind='delivery'` 租约；
- 取 `status IN ('pending','failed') AND next_retry_at <= NOW()` 的行，`FOR UPDATE SKIP LOCKED`；
- 复用既有 HMAC 签名与重试逻辑（`server/utils/webhook_engine.py:320-405`）；
- 退避：1s/5s/30s/5m/30m，最多 8 次，之后 `dead_letter`；
- 每次投递结果写 `ai_batch_events`（`delivery.sent` / `delivery.failed`）供管理面查看；
- 支持人工重放（管理端点把 `dead_letter` 重置为 `pending`）。

---

## 8. API 契约

### 8.1 内部（JWT）

| 路由 | 变更 |
|---|---|
| `GET /ai/chat/batches/events` | **新增**（§7.2） |
| `GET /ai/chat/batches/<bid>/attempts` | **新增**：attempt 链 + lease/heartbeat + recovery_reason |
| `GET /ai/chat/batches/<bid>/events` | **新增**：事件分页（管理/排障用） |
| `POST /ai/chat/batches/<bid>/commands` | **新增**：统一命令入口（幂等） |
| `GET /ai/chat/batches/<bid>/commands/<cid>` | **新增**：命令状态查询 |
| `GET /ai/chat/admin/batches/<bid>/deliveries` | **新增**：outbox 投递状态 + 重放 |
| 既有 pause/resume/cancel/retry/append/reexecute | 保留路由，内部改走 command + CAS |

### 8.2 对外（`/v1/ai-batches`）

| 路由 | 变更 |
|---|---|
| `POST /<batchId>/pause` | **新增**（复用内部语义，密钥隔离） |
| `POST /<batchId>/resume` | **新增** |
| `POST /<batchId>/sessions/<childId>/cancel` | **新增** |
| `POST /<batchId>/commands` | **新增**：通用命令（含 `forceStop`），要求 `Idempotency-Key` |
| `GET /<batchId>/commands/<commandId>` | **新增** |
| `GET /<batchId>/events` | **新增**（§7.3） |

`childId` 寻址：P1 保持“`seq` 或文件名 basename”兼容，同时新增 opaque `childId`（`ai_chat_sessions.id` 的对外别名，避免同名文件歧义——现状 `_resolve_child` 按 basename 取首个匹配：`server/routes/open_api_batches.py:595-609`）。`/results`、`/file-records`、`/sessions/*` 响应新增 `childId` 字段（增量，不破坏既有字段）。

### 8.3 对外状态与错误字段（增量）

`GET /<batchId>` 与 `/results` 增加：

```json
{
  "batchId": "...",
  "status": "running",
  "generation": 3,
  "queueWaitMs": 4200,
  "runningMs": 183000,
  "lastProgressAt": "2026-09-23T10:12:00Z",
  "eventCursor": 128,
  "paused": false,
  "children": [
    {
      "childId": "c-1",
      "name": "report1.pdf",
      "status": "running",
      "phase": "tool",
      "attempt": 2,
      "retryCount": 1,
      "retryable": true,
      "lastProgressAt": "...",
      "error": {"code": null, "message": null, "retryable": null, "evidenceRefs": []}
    }
  ]
}
```

错误结构统一为：

```json
{"error": {"code": "UPSTREAM_TIMEOUT", "message": "...", "retryable": true,
           "phase": "dispatch", "attempt": 2, "evidenceRefs": ["event:evt_..."]}}
```

新增错误码（在 P0 表基础上）：

| code | 含义 |
|---|---|
| `COMMAND_DUPLICATE` | 相同 `Idempotency-Key` 已存在（返回既有 command，HTTP 200） |
| `COMMAND_REJECTED` | 前置状态不允许（如终态批次 resume） |
| `CURSOR_EXPIRED` | `afterSeq` 超出保留窗口 |
| `BUDGET_EXCEEDED` | 超出预算 |
| `NEEDS_REVIEW` | 执行进入人工复核终态 |
| `LEASE_LOST` | 调用方持有的执行权已被接管 |

---

## 9. 资源预算

### 9.1 模型

```sql
CREATE TABLE IF NOT EXISTS ai_execution_budgets (
  id            VARCHAR(100) PRIMARY KEY,
  scope_type    VARCHAR(20) NOT NULL,   -- batch | user | api_key | tenant
  scope_id      VARCHAR(100) NOT NULL,
  max_wall_clock_ms BIGINT,
  max_tokens        BIGINT,
  max_cost          NUMERIC(12,4),
  max_tool_calls    INT,
  max_subagents     INT,
  max_workspace_bytes BIGINT,
  max_concurrency   INT,
  on_exceed     VARCHAR(20) NOT NULL DEFAULT 'drain',  -- warn | drain | abort
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_budget_scope ON ai_execution_budgets(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS ai_execution_usage (
  session_id     VARCHAR(100) PRIMARY KEY REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  batch_id       VARCHAR(100),
  tokens_input   BIGINT NOT NULL DEFAULT 0,
  tokens_output  BIGINT NOT NULL DEFAULT 0,
  cost           NUMERIC(12,4) NOT NULL DEFAULT 0,
  wall_clock_ms  BIGINT NOT NULL DEFAULT 0,
  tool_calls     INT NOT NULL DEFAULT 0,
  subagents      INT NOT NULL DEFAULT 0,
  workspace_bytes BIGINT NOT NULL DEFAULT 0,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

数据来源：既有 per-message meta（`server/utils/ai_message_meta.py:11-60`，由 `_persist_conversation` 落库）与工具账本（`agent_tool_calls`）、子代理表（`ai_chat_subtasks`）、workspace 扫描。**从“观测字段”升级为“判定依据”。**

### 9.2 判定点与动作

| 时机 | 动作 |
|---|---|
| claim 前 | `max_concurrency` 与 scope 并发检查，超限不 claim（留 pending） |
| 每次进度落库 | 累计 usage；超过 soft 阈值 → `budget.warning` 事件；超过 hard → 按 `on_exceed` |
| 回合收敛 | 写 `budget.exceeded` 事件 + outbox（外部可感知） |
| 超限动作 | `warn`：仅事件；`drain`：不再 claim 新子任务、运行中自然收敛；`abort`：abort OpenCode + child → `failed`（`error_code='BUDGET_EXCEEDED'`） |

既有常量 `MAX_AUTO_RETRY` / `SESSION_TIMEOUT_SEC` / `STALL_TIMEOUT_SEC` / `TOOL_STALL_TIMEOUT_SEC`（`server/utils/batch_engine.py:550-583`）在 P1 保留为**默认值来源**，并可被批次级预算覆盖。

### 9.3 workspace 生命周期

- 批次终态后默认保留 N 天（可配 `AI_WORKSPACE_RETENTION_DAYS`，默认 30），由 `lease_kind='scheduler'` 的清理任务回收；
- 清理前把 `ai_chat_session_files` 记录与（P2 之后的）artifact manifest 一并核对，避免 DB 记录悬挂；
- 落实 `server/config.py:89-90` 中定义但未实现的 `AI_WORKSPACE_QUOTA_MB`：超限拒绝新 workspace 创建并告警。

---

## 10. 管理面

`src/views/admin/AiBatchAdmin.vue` 与详情抽屉新增（数据源为 §8.1 端点）：

| 区块 | 内容 |
|---|---|
| Lease / 心跳 | owner 是否在线（布尔）、`heartbeatAt`、`leaseUntil`、`fencingToken` |
| Attempt 链 | `attempt_no`、`parent_attempt_id`、operation、状态、起止、耗时、recovery_reason |
| 事件时间线 | `ai_batch_events` 分页，按 `eventSeq` 排序，支持跳转 |
| 门禁证据 | 每条 expectation 的 `last_status` / `last_evidence` / 失败明细（含 inconclusive 原因） |
| 活跃工具/子代理 | 当前 running 的 tool/subagent（来自最近一次进度落库） |
| 预算消耗 | usage 汇总 + 超限事件 |
| 投递状态 | outbox 行、重试次数、`dead_letter` 与重放按钮 |
| 命令历史 | command 列表（谁、何时、结果） |

权限沿用 `admin.ai_chat_admin`；Prompt 明文仍受 `admin.ai_execution_prompt_read` 约束（不得从批消息接口绕过，见 P2 附注与 `server/routes/ai_session_admin.py:550-591`）。

---

## 11. 迁移与兼容

### 11.1 迁移清单（幂等，注册进 `server/app.py` 启动钩子）

1. `ai_chat_sessions` 增列 `lease_owner` / `lease_until` / `heartbeat_at` / `fencing_token`（§5.2）；`ai_execution_attempts` 增列 + 状态 CHECK 替换 + 部分唯一索引（§5.1）；
2. `ai_execution_checkpoints`、`ai_execution_effects`、`ai_execution_commands`、`ai_batch_events`、`ai_delivery_outbox`、`ai_execution_budgets`、`ai_execution_usage` 建表与索引；
3. `ai_batch_worker_leases` 增 `lease_kind`；
4. `ai_execution_events` 的 seq 分配改为原子（代码变更，无 DDL）；
5. 存量 `running` 行回填 `fencing_token=0`、`lease_owner=NULL`，使其可被首次接管。

### 11.2 兼容

| 项 | 策略 |
|---|---|
| 存量 callback 配置 | 迁移后首次终态起走 outbox；历史投递不回填 |
| 既有 SSE/轮询前端 | 事件流上线后前端先接 SSE、保留轮询降级；旧前端仍可用轮询 |
| 对外字段 | 全部为**增量**；`children` 数组为新增字段，不替换既有 `results` 结构 |
| 事件保留 | 新表默认 180 天；`CURSOR_EXPIRED` 是明确契约，不是错误 |

### 11.3 回滚

- 行为开关：`AI_HARNESS_LEASE_ENFORCE=0` 可退回“仅记录不强制”（仅用于故障排查，不建议长期）；
- outbox 开关：`AI_DELIVERY_OUTBOX_ENABLED=0` 回退为直接 POST（保留旧路径一个版本）；
- 新表只增不删，回滚不需要数据迁移。

---

## 12. 测试与故障注入

### 12.1 后端（pytest）

新增 `server/tests/test_harness_p1_durable.py`：

1. **lease**：两实例同时 acquire → 一个成功；heartbeat 中断后 TTL 到期可接管；旧 owner 写回 0 行。
2. **恢复决策**：§4.2 表中每一行至少一例（含 `needs_review`）。
3. **attempt 链**：retry/continue/reexecute 关闭旧 attempt 且 `parent_attempt_id` 正确；终态 session 无 running attempt。
4. **checkpoint**：写入幂等、`is_latest` 唯一、恢复后不重复已提交 effect。
5. **effect**：`unknown` 不自动重放；重复 effect key 只提交一次；`committed` 后重试直接复用。
6. **事件**：并发写 seq 无冲突无丢失；`afterSeq` 语义；`CURSOR_EXPIRED`。
7. **outbox**：事务回滚不留 outbox 行；投递失败重试到 `dead_letter`；人工重放；同一 event 不重复投递。
8. **命令**：幂等键、`expected_generation` 冲突、终态拒绝。
9. **预算**：并发不超卖；hard limit 动作正确；usage 缺失与 0 可区分。

### 12.2 故障注入（关键）

- 在 `create_session` 前 / `send_message` 后 / 工具执行中 / `_mark_done` 前分别 kill executor；
- 模拟“请求已送达但客户端超时”（`send_message` 抛 `RequestException` 但 OpenCode 已开始执行）；
- OpenCode serve 重启（会话落盘仍在）与 serve 数据被清理（404）两种情形；
- 投递中 kill 进程，重启后确认最终投递。

### 12.3 E2E

`e2e/ai-full/ai-harness-p1-durable.spec.ts`：

- 批任务运行中断开 SSE → 重连按 `Last-Event-ID` 补发，无漏帧；
- 暂停 → 恢复 → 完成，事件序列与 UI 一致；
- callback 端点在测试中先拒后收，验证重试与最终投递；
- 超预算小任务（token 上限调低）触发 `BUDGET_EXCEEDED` 并可在 UI 看到。

---

## 13. 验收标准

- [ ] 任一 child 同时只有一个有效 lease；旧 fencing token 的写回不改变任何状态；
- [ ] executor 崩溃后，运行中任务在 `lease TTL + 对账周期` 内被正确分流（continue / rebuild / needs_review / failed），无永久 `running`；
- [ ] 恢复不重复提交已 `committed` 的 effect；`unknown` 一律进 `needs_review`；
- [ ] 终态 session 不存在 running attempt；attempt 链可完整回溯；
- [ ] 事件按 seq 单调、可 `afterSeq` 增量拉取、断线可补发、过期返回 `CURSOR_EXPIRED`；
- [ ] callback 由 outbox 投递：与状态同事务、失败可重试、可去重、可人工重放；
- [ ] 外部 API 支持 pause/resume/child cancel/commands/events，全部要求 `Idempotency-Key`；
- [ ] 对外错误含 `code/retryable/phase/attempt/evidenceRefs`；
- [ ] 预算可按批次/用户/API Key 生效，超限行为可观测且可恢复；
- [ ] 管理面可看到 lease/attempt/事件/门禁证据/投递/预算，且与对外状态口径一致。

**量化指标（建议纳入回归）**

| 指标 | 目标 |
|---|---|
| 孤儿执行识别时间 | ≤ `lease TTL` + 1 个对账周期 |
| 事件写入丢失率 | 0（并发压测下） |
| 回调最终投递成功率 | 目标端点恢复后 100%（8 次退避内） |
| 重复 effect 提交 | 0 |
| 状态可见延迟（SSE 正常） | 子任务状态变化 ≤ 2s |

---

## 14. 涉及文件

**新增**

- `server/migrations/2026_09_23_harness_p1_durable_execution.py`
- `server/utils/execution_lease.py`（租约/心跳/fencing 原语）
- `server/utils/execution_checkpoint.py`
- `server/utils/execution_effect.py`
- `server/utils/execution_commands.py`
- `server/utils/batch_events.py`（原子 seq 写入 + 读取）
- `server/utils/delivery_outbox.py`（投递器 + 重放）
- `server/utils/execution_budget.py`
- `server/tests/test_harness_p1_durable.py`
- `e2e/ai-full/ai-harness-p1-durable.spec.ts`
- `src/api/batchEvents.ts`（SSE + cursor 客户端）
- `src/components/admin/BatchAttemptTimeline.vue`、`BatchDeliveryPanel.vue`（或并入既有抽屉）

**修改**

- `server/utils/batch_engine.py`（claim/heartbeat/写回带 fencing；恢复决策；checkpoint 写入点；预算判定）
- `server/utils/batch_repo.py`（CAS 写回带 token；命令化）
- `server/utils/execution_audit.py`（seq 原子分配、attempt 状态机扩展）
- `server/routes/ai_chat_batches.py`（events/attempts/commands 端点）
- `server/routes/open_api_batches.py`（pause/resume/child cancel/commands/events；状态字段扩展）
- `server/routes/ai_batch_admin.py`（deliveries 重放）
- `server/app.py`（delivery/scheduler 租约、迁移注册）
- `src/stores/aiChatBatches.ts`（SSE 主通道 + 轮询降级 + cursor）
- `src/views/admin/AiBatchAdmin.vue`、`src/api/aiBatchAdmin.ts`
- `docs/user-guide/integration/ai-batch-api.md`、`docs/user-guide/integration/openapi.yaml`
- `docs/design/ai/AI批任务容灾与恢复机制.md`（更新为 lease/checkpoint 语义）

---

## 15. 拆 PR 建议

| PR | 内容 | 依赖 |
|---|---|---|
| **PR-1** | 租约/心跳/fencing 原语 + claim/写回接入 + 多实例测试 | P0 |
| **PR-2** | 恢复决策表 + attempt 闭环 + `needs_review` 终态 | PR-1 |
| **PR-3** | checkpoint + effect 账本 + 幂等写入 | PR-1 |
| **PR-4** | 事件表 + 原子 seq + 内部 SSE + 前端 cursor | PR-1 |
| **PR-5** | outbox 投递器 + 管理面投递面板 + 人工重放 | PR-4 |
| **PR-6** | 外部 pause/resume/child cancel/commands + 状态字段扩展 + 文档/openapi | PR-1 |
| **PR-7** | 资源预算 + workspace 生命周期清理 | PR-3 |

---

## 16. 依赖与阻断关系

- **依赖 P0**：租约的写回校验建立在 P0 的 `execution_generation` 与 CAS 之上；P0 未完成不得开工。
- **阻断 P2**：P2 的 Step/DAG、审批、Artifact Store、Runtime Adapter 必须复用本 Spec 的 lease / checkpoint / effect / events / outbox / commands，不得另建一套。
- 与 `docs/design/ai/13-AI批任务实时进度与编排设计.md` 的关系：本 Spec 采用其“DB 版本 + 条件推送”的取向，但把事实源从 `progress_version` 升级为事件表（同时覆盖其未落地的动态并发与优先级，见该文档 §5.3/§5.4）。
- 与 `docs/design/ai/AI批任务容灾与恢复机制.md` 的关系：三层防线保留，但“重启重放”升级为“租约 + checkpoint 决策”，并修正该文档 §8.1 承认的非强幂等边界。
