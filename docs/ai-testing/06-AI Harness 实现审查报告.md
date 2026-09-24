# AI Harness P0/P1/P2 实现审查报告

> 审查日期：2026-09-24 ｜ 审查对象：分支 `feat/ai-harness-p0-p1-p2`（9 commits）
> 审查基准：`docs/superpowers/specs/2026-09-23-ai-harness-p0-execution-safety-spec.md`、`...-p1-durable-execution-spec.md`、`...-p2-orchestration-runtime-spec.md`
> 审查方式：三份 spec 逐条核对 + 新增测试实跑 + 既有回归实跑 + 后端全量套件 + 前端构建 + 生产库只读查询 + 运行中后端状态取证
> **结论先行：分支交付的是「P0 主体可用 + P1 骨架齐备但关键承诺未接线 + P2 可演示骨架」；不建议按「全部完成」合入。存在 8 项严重缺陷（其中 3 项为我实测发现并复核），14 项中等缺陷。**

---

## 1. 审查范围与方法

### 1.1 改动规模

| 项 | 数值 |
|---|---|
| 相对 main 的提交数 | 9 |
| `git diff --shortstat` 显示 | 50 files, 18503 insertions, 12110 deletions |
| **真实改动（`-w` / `--ignore-space-at-eol`）** | **50 files, ≈6611 insertions, ≈218 deletions** |

> 差异原因：分支把多个文件从 CRLF 改写为 LF（例如 `server/utils/batch_engine.py` 在 main 有 2121 行含 CR，分支为 0），使 `git diff` 膨胀约 3 倍。**审查与后续 review 请用 `git diff -w main..HEAD -- <path>`**，否则几乎无法阅读。

新增交付物：3 个迁移（`2026_09_23_harness_p0/p1/p2_*.py`）、16 个新后端模块（`execution_lease/checkpoint/effect/commands/budget`、`batch_events`、`delivery_outbox`、`orchestration_defs/engine`、`approval_repo`、`artifact_store`、`runtime/`）、4 个新路由（`ai_approvals`、`ai_orchestrations`、`artifacts` 等）、3 个新测试文件、1 个新 E2E spec、3 份文档更新。

### 1.2 证据来源与结果

| 手段 | 命令 / 对象 | 结果 |
|---|---|---|
| P0 新增测试 | `pytest tests/test_batch_execution_safety_p0.py` | **32 passed**（36.6s） |
| P1/P2 新增测试 | `pytest tests/test_harness_p1_durable.py tests/test_orchestration_p2.py` | **29 passed**（24.7s） |
| 受影响既有回归 | 7 个批任务/门禁/审计测试文件 | **146 passed**（64s），其中出现 1 条线程异常告警（见 H1） |
| 后端全量套件 | `pytest tests/`（串行，544s） | **19 failed, 2226 passed, 3 skipped** —— 两组失败单独重跑**均通过**（见 §7.3） |
| 前端构建/类型检查 | `npm run build` | **✓ built in 5m 4s**（vue-tsc 干净） |
| 生产库只读查询 | `ai_batch_worker_leases` / `ai_execution_effects` / `ai_execution_checkpoints` / `ai_delivery_outbox` | 见 H1、H7 |
| 运行中后端取证 | `server/ai-chat.log` + 进程 195556（正在运行本分支代码） | 见 H1 |

---

## 2. 结论总览

| 阶段 | 达成度 | 严重缺陷 | 判断 |
|---|---|---|---|
| **P0 执行安全基线** | 主体达成（10 项验收中 6 项 ✅、3 项 ⚠️、1 项 ❌） | 3（H1/H2/H3） | 架构与实现方向正确，CAS/fencing/门禁 fail-closed 都有真实实现与真实断言；但有 1 处实测缺陷与 1 处相对 main 的行为回退 |
| **P1 持久化执行与控制面** | 数据模型与模块齐备，**关键承诺未接线**（12 项中 4 项 ✅、5 项 ⚠️、3 项 ❌） | 3（H7/H8 + 恢复决策表） | 「表都在、原语都在、生产不调用」——effect 账本零写入、checkpoint 不参与恢复、重启仍无条件重放 |
| **P2 编排与 Runtime** | 可演示骨架（11 项中 3 项 ✅、4 项 ⚠️、4 项 ❌） | 2（H4/H5/H6） | 线性 DAG 与审批门禁能跑通；但并发双派发、条件分支与数据流不可用、skipped 死锁、Runtime Adapter 完全未接线 |

**一句话**：P0 可以带着 H1–H3 修复后合入；P1/P2 建议明确标注为「骨架/部分完成」，不宜计入「已完成」。

---

## 3. 严重缺陷

### H1. batch worker 租约心跳线程启动即崩，单实例保护在 90 秒后失效

**现象**：`server/utils/batch_engine.py:648` 在 `_lease_loop` 内先引用、后导入：

```python
while not self._stop.is_set():
    if self._stop.wait(execution_lease.DEFAULT_HEARTBEAT_SEC):   # 648 —— UnboundLocalError
        break
    from utils import execution_lease                            # 650 —— 使该名在整函数内成为局部变量
```

函数体内存在 `from utils import execution_lease`，Python 遂把该名视为局部变量；第 648 行的求值先于赋值，线程第一条语句即抛 `UnboundLocalError` 而死。对照 `server/utils/delivery_outbox.py:245`（同款循环、import 在函数首行）工作正常。

**证据**：

1. pytest 运行输出告警：`Exception in thread batch-worker-lease ... UnboundLocalError: cannot access local variable 'execution_lease'`（`test_batch_engine.py::test_concurrency_cap_3`）。
2. 运行中后端（PID 195556，正在执行本分支代码）日志 `server/ai-chat.log:25378`：`2026-09-24 02:42:52 batch dispatcher started`，此后**无** dispatcher 退出、无 lease 告警。
3. `ai_batch_worker_leases` 现状：`delivery` / `scheduler` 两行心跳为 1–4 秒前（存活），**`batch` 行不存在**（已被后来者抢占/释放）。租约 TTL 为 90s（`execution_lease.py:22`），即后端自 02:44 起一直处于「自以为持锁、实际锁已过期」。

**影响**：

- P0 目标「多进程部署只有一个执行器」只在启动瞬间成立。第二个实例在 TTL 后可成功 `acquire`，随后执行 `_restart_audit()`（`batch_engine.py:883-894`，仍为无条件 `running→pending`）→ 把第一个实例正在跑的 running 子会话重排并执行 → **同一子会话双执行**；第一个实例的写回会被子会话级 fencing 拒绝（结果丢失），但它已产生的外部副作用（MCP 写入、文件、外部 HTTP）不会回滚。
- `batch_engine.py:653-658` 的「连续 3 次心跳失败就让位」安全网因线程已死而永不触发。

**建议**：将 `from utils import execution_lease` 提到 `_lease_loop` 首行；补测试断言 `start()` 后心跳线程存活、且 `heartbeat_at` 随时间推进（现有 `test_lease_single_owner` 只测 `execution_lease` 原语，覆盖不到该路径）。

---

### H2. 内部 DELETE 缺少 drain 超时保护，超时仍继续删除

**现象**：`server/routes/ai_chat_batches.py:329-341` 的 `while` 等待 10 秒后**没有 else 分支**，超时照样执行 `cleanup_batch_workspaces` + `delete_batch`。对外路由已按 spec 正确实现（`server/routes/open_api_batches.py:548-553`：超时返回 409 且不删）。

**影响**：正是 spec P0-3 要消灭的「幽灵执行」——worker 写回命中已删行、工作区已被拆。内外两个入口语义不一致，spec §7.2 明确要求一致。

**建议**：内部路由与对外对齐，超时返回 409 并保留任务与工作区。

---

### H3. 门禁 fail-open 回归：入口 D 补挂的期望「核对了但不拦」

**现象**：`server/utils/batch_engine.py:1164-1177` 只有当批级 `action_checks` 过滤后 `applicable > 0` 时才使用核对结果，否则直接 `gate_status='skipped'` → completed。而管理员补挂入口 `POST /ai-chat/batches/<bid>/action-checks/attach`（`server/routes/ai_chat_batches.py:230-261`）只写 per-session 期望、不写批级列。

**对照 main**：`git show main:server/utils/batch_engine.py:1022-1029` 是「任何 `failed` 都阻断」。因此这是**相对 main 的行为回退**：补挂的期望现在会被静默忽略（`gate` 变量已被 `_check_action_gate(sid)` 计算并更新 `action_expectations`，但结果被丢弃）。

**建议**：把「该会话实际登记的期望数 > 0」纳入判定条件，而非只看批级配置；补入口 D 的端到端测试。

---

### H4. P2 step 状态转移无 CAS → 并发推进重复派发同一 step

**现象**：`server/utils/orchestration_engine.py` 的全部 step UPDATE 只有 `WHERE id=%s`，无状态谓词（`:223-225, 242-244, 318-320, 340-342, 375-376, 442-443, 461-463`）。其中 `:340-342` 是 `SET status='running', session_id=%s, attempt_count=1` 的无条件写。`_advance_run` 被 API 线程（`routes/ai_orchestrations.py:63`）、调度线程（`:533-548`）、批 worker（`batch_engine.py:1261-1268`）三处并发调用且无锁。

**证据**：审查线用 6 线程并发探针实测——同一单节点 run 创建了 **6 个 `ai_chat_sessions`**（step 行只记 1 个 session_id、`attempt_count=1`）。

**影响**：6 份重复 Agent 执行与重复 token 成本；迟到子会话的终态无条件覆盖 step 状态。违反 spec §5.3「全部 CAS，禁止无条件覆盖」。

**建议**：所有 step 转移改为 `UPDATE ... WHERE id=%s AND status=%(expected)s RETURNING`，以 rowcount 决定后续；`_advance_run` 增加批次/run 级行锁或 advisory lock。

---

### H5. P2 条件分支与数据流在生产路径不可用

**现象**：`ai_orchestration_steps.output` 在**全仓生产代码中没有任何写入点**（仅 `orchestration_engine.py:127,162` 两处读；写入只存在于 `server/tests/test_orchestration_p2.py:194,225,234,269,304,326`）。

**影响**：

- 条件边判定 `_edge_matches`（`orchestration_engine.py:121-148`）恒为假 → 条件分支永不命中；
- `{{steps.x}}` 提示词渲染（`:160-167`）恒为空。E2E 中 `summarize` 的 prompt「上游抽取结果：{{steps.extract}}」实际渲染为「上游抽取结果：」，而 E2E 只断言状态不断言内容（`e2e/ai-full/ai-harness-safety.spec.ts:283-291`）故未暴露。

**建议**：在子会话终态收口处把结构化结果/产物摘要写入 `steps.output`；补「命中条件分支」与「上游数据传入下游 prompt」的真实链路测试。

---

### H6. P2 skipped 依赖不传播 → run 永久卡在 running

**现象**：`dep_failed` 只认 `failed/needs_review`（`orchestration_engine.py:218-226`），skipped 依赖使下游永远停在 `blocked`；run 状态派生把 `blocked` 视为 `running`（`:263-264`），且无 step 超时兜底。

**证据**：审查线复现菱形 DAG（split→big/small→merge）：big 成功后 merge 恒为 `blocked`、run 恒为 `running`。

**建议**：定义 skipped 的传播语义（skipped 依赖 → 下游 skip 或按 join 策略放行），并补 step 超时。

---

### H7. effect 账本零写入 → P1 的幂等承诺空转

**现象**：`execution_effect.record_effect / settle_effect` 在**生产代码中无任何调用方**（grep 仅命中 `server/tests/test_harness_p1_durable.py` 与模块自身）。

**证据**：生产库只读查询 `ai_execution_effects` = **0 行**；`has_unknown_effects()`（`batch_engine.py:2339-2346`）在生产恒为 False，needs_review 分支恒不触发。

**影响**：spec P1 §4.3 承诺的「MCP 写入 / 文件导入 / 扫描回写 / 回调 / 产物」五类副作用幂等键全部未接入；「at-least-once 执行 + exactly-once effect」只剩前半句。P1 的核心价值未交付。

---

### H8. 重启仍是无条件重放，恢复决策表在真实重启路径被绕过

**现象**：`_restart_audit`（`batch_engine.py:883-894`）在 P1 提交中**未被改动**（`git diff -w dfd594f..7bcfd7a` 中 `restart_audit` 命中数 = 0），仍在 dispatcher 之前被调用（`:635`）。进程重启后遗留 running 行立即变 pending → 被 claim 时 `continue_prompt` 为 NULL → 重建 OpenCode 会话、重发原始 prompt。

**影响**：spec P1 §13 的验收项「崩溃后按 continue / rebuild / needs_review / failed 分流」在**真实重启路径上不成立**；决策表只在「进程存活但线程死亡」时生效。配合 H7（无 effect 记录）与 checkpoint 不参与恢复（见 §5），重启后无法判断哪些副作用已发生。

---

## 4. 中等缺陷

| # | 缺陷 | 证据 |
|---|---|---|
| M1 | `continue_child` / `reexecute_child` / `resume_child` 为 SELECT 后**无状态谓词**的 UPDATE（非 spec §6.2 要求的单条 conditional UPDATE）；并发双执行会重复回滚计数、`execution_generation` 跳 2 | `batch_repo.py:691-728, 731-771, 774-808` |
| M2 | **续跑的用户消息丢失**：`_persist_user_prompt` 用固定 id `f'{session_id}:user'` + `ON CONFLICT DO NOTHING`，而 `ai_chat_messages.id` 是主键 → 第二轮及以后的人工续跑消息永不落库（模型收到、UI 与历史看不到） | `batch_engine.py:1637-1652`、`init_db.py:450-451` |
| M3 | `POST /ai/chat/sessions/<sid>/command` 对批子会话无 `batch_id`/状态门禁，可绕过 P0-1 的发送门禁再开一个 turn（前端不可达、HTTP 可达） | `ai_chat.py:1540-1556` |
| M4 | outbox 与旧直发并存：`_notify_callback` 未检查 `outbox_enabled()`，outbox 开启时同一终态既入队又立即 POST → 回调重复投递（配合既有 A4「回调可多次触发」语义放大） | `batch_engine.py:375-404`、调用点 `:2127,2151,2289` |
| M5 | `needs_review` 是死胡同：`batch_repo.py` 全文无该状态，retry/reexecute/continue/resume 均不含它 → 含 needs_review 子任务的批次永久卡死；spec §4.2「可被人工 command 拉回」未实现 | grep 无命中 |
| M6 | fencing 写回校验只覆盖 `transition_child` 一处且为**可选参数**（`batch_repo.py:122`）；`_mark_needs_review` 未传（`batch_engine.py:2280-2284`）、`_persist_conversation` 只校验 generation、checkpoint/effect/文件/重排写回无校验；**无任何测试**覆盖「旧 owner 写回 0 行」 | — |
| M7 | 对外状态/错误字段扩展完全未实现：`_batch_out` 与 main 相同，无 `generation / queueWaitMs / phase / attempt / retryable / error.evidenceRefs / eventCursor / children`；`CURSOR_EXPIRED`、`BUDGET_EXCEEDED`、`NEEDS_REVIEW`、`LEASE_LOST` 错误码全仓无命中 | `open_api_batches.py:217-236` |
| M8 | **测试会删除共享开发库里无关的 pending 子会话**：两个新测试文件都含 `DELETE FROM ai_chat_sessions WHERE status='pending' AND batch_id <> %s`，作用于 `config.DB_CONFIG` 指向的 dev 库（详见 §10.1） | `test_batch_execution_safety_p0.py:49-56`、`test_harness_p1_durable.py:59-64` |
| M9 | Artifact 鉴权缺陷：`ingest_session_outputs` 未传 `owner_user_id` → 产物 owner 为 NULL，创建者本人下载 403（仅 admin 可下）；`(sha256,name)` 全局去重还会让第二个用户拿到他人 owner 的行 | `orchestration_engine.py:469-471`、`artifact_store.py:53-57`、`routes/artifacts.py:22-23` |
| M10 | 定义字段静默丢弃（`skills / input_refs / runtime / budget / priority` 归一化时被丢且不报错）；五个 policy 字段只存不消费；无 version 递增路径（恒 `version=1`，`enabled` 从不读） | `orchestration_defs.py:33-41, 116-127, 169` |
| M11 | 内部 SSE 用 `login_required` 而非 `login_required_sse`（浏览器 EventSource 无法携带 JWT），且不输出 SSE `id:` 行 → 浏览器 Last-Event-ID 自动补发不可用；ping 3s ≠ spec 的 15s | `ai_chat_batches.py:548-615` vs `auth.py:63-82` |
| M12 | outbox 投递置 `sending` 后进程被 kill → 该行永久卡住（`idx_outbox_due` 只含 pending/failed，重放只接受 failed/dead_letter） | `delivery_outbox.py:90-92, 170`、迁移 `:173-175` |
| M13 | `lease_kind` 从不写入（`acquire` 的 INSERT 不含该列）→ DB 中三行 lease 的 `lease_kind` 全是 `dispatcher`，spec §5.8 名存实亡（实际隔离靠 `lease_key`） | `execution_lease.py:44-58`（DB 实测） |
| M14 | `append_event` / `enqueue` 传 conn 时吞异常，但 PG 事务已进入 failed 状态 → 后续语句报 `InFailedSqlTransaction`，最终 commit 等价 rollback，**终态写入与计数静默丢失** | `batch_events.py:50-56`、`delivery_outbox.py:51-53` |

---

## 5. 低优先与一致性清单

- `gate_status` / `gate_error` / `gate_checked_at` 只写不读（无 API/UI 出口，spec §5.4「对外可观测」未兑现）；
- spec §8.3 的 `gate.evaluated` 审计事件缺失（仅有 `child.status` 事件带 `gateStatus` 作近似）；
- `_maybe_auto_retry` / `_maybe_gate_retry` 的重排 UPDATE 缺 generation 与状态谓词（对照 `_requeue_lost:2400` 有 `AND status='running'`）；
- `scan` 租约未实现（`app.py:363-364` 无条件启动 scan scheduler），spec §5.3 的 `lease_key='scan'` 未落地；
- `parent_attempt_id` 依赖进程内 dict（`batch_engine.py:612,1067,1370-1379`），跨重启/跨实例断链；DB 实测 319 个 attempt 中 `parent_attempt_id` 非空 = 0；
- checkpoint 写入点只有 2/4（缺 progress / recovery），`latest_checkpoint()` 无生产调用方，`message_seq / workspace_manifest_hash / completed_effect_ids` 从不填充；
- attempt 租约列（§5.1）从不写入（DB 实测 319 行全空）；
- 命令面未闭环：内部无 commands 路由/调用，`expected_generation` 只存不比，`force_stop` 无实现；
- 预算 7 个维度中 4 个只存不判（`max_concurrency / max_tool_calls / max_subagents / max_workspace_bytes`）；usage 仅终态累计；无 `budget.warning`；abort 落 cancelled 而非 failed + `BUDGET_EXCEEDED`；
- workspace quota / retention 未实现（`config.py:90` 仍为死配置）；
- 事件保留与 `CURSOR_EXPIRED` 未实现；
- 编排/审批/产物的 openapi.yaml 与用户文档未补；P2 管理面前端（`AiOrchestrationManager.vue`、`RunGraphView.vue`、`api/orchestration.ts`）与 P1 前端（`api/batchEvents.ts`、attempt/delivery 面板、store 的 SSE/cursor 主通道）均不存在；
- `artifacts` / `artifact_refs` / `ai_runtime_manifests` / `ai_orchestration_*` 未纳入 `backup.py` 的 `BACKUP_TABLES`；
- claim CTE 仍无暂停批次守卫（spec P1 §6.1 要求补的「暂停批次追加后新子任务被运行」未修）；
- `ai_chat_batches` 状态枚举不含 `cancelled`（与前端一致，见 §6）。

---

## 6. 规格校准（spec 过度指定，实现方无需改）

**F10「前端终态集合补 cancelled」无需修改**：`ai_chat_batches.status` 的 CHECK 约束为 `('pending','running','paused','completed','partial','failed')`，不含 `cancelled`（cancelled 子任务计入 `failed` 聚合），因此 `TERMINAL_STATUSES` 不含它不会造成轮询泄漏。实现方保留原样是正确的——是我的 spec 过度指定，应修正 spec。文案部分（paused / cancelled / partial）已按 spec 补齐（`src/views/ai-chat/AiChatView.vue:845-849`）。

**spec P1 §4.2 第 4 行与 §6.3 存在自相矛盾**：前者要求「OpenCode 会话 404 + 有 checkpoint → rebuild」，后者要求「404 → failed」。实现方选择了 failed（`batch_engine.py:2356-2362`），与 §6.3 一致。建议后续修订 spec 消除冲突。

---

## 7. 测试评估

### 7.1 做得好的部分

- CAS / generation / fencing / 删除保护 / `apply_to` / 门禁登记异常都有**真实断言**（已逐条读断言而非测试名）；
- 事件并发 seq（24 并发）与审计事件并发（12 并发）使用真库真线程，断言无丢失；
- F9（attempt 收口）断言最强：验证 attempt 状态分布 + `parent_attempt_id`；
- P0 新增 32 例、P1 17 例、P2 12 例全部通过；前端构建与类型检查干净。

### 7.2 主要缺口

1. **真并发几乎零覆盖**：spec §12 的 7 格竞态矩阵无一格是真并发；`test_mark_done_counted_exactly_once` 是顺序双调用；`test_f6_append_allocates_sequential_seqs` 是单线程顺序 append + 索引存在性检查。
2. **测试绕过生产路径**：P2 的条件分支 / join / 审批用例都用直接 `UPDATE ai_orchestration_steps SET status/output` 预置，验证的是调度器算术而非「能力可用」，恰好掩盖了 H5 / H6。
3. **E2E 断言过弱**：`expect([200, 409]).toContain(...)`（`ai-harness-safety.spec.ts:165-169`）两种结果都接受，等于没有断言；UI 门禁断言是条件分支，跑太快会 `console.warn` 跳过（`:100-105`）。
4. **关键不变量无测试**：「旧 owner 写回 0 行」零覆盖（M6）；恢复决策表 7 行只测 3 行；**无任何测试调用 `_restart_audit`**（H8 完全不受约束）；F1（子代理账本 FK 顺序）无回归用例；F11 的断言未检查 `pause_requested`。
5. **测试隔离风险**：见 M8 与 §10.1。

### 7.3 全量套件的 19 个失败是顺序相关 flaky，不是确定性破坏

| 失败组 | 数量 | 单独重跑结果 |
|---|---|---|
| `test_workspace_changes.py`（`subprocess...`） | 18 | **36 passed**（17.3s） |
| `test_batch_auto_retry.py::test_provider_auth_never_retries` | 1 | **1 passed**（10.1s） |

两组失败在全量顺序下出现、单独运行通过，说明存在顺序/状态依赖（可能是前序用例遗留的 git 工作区状态与 worker 认领状态）。**不能按「绿了」处理**，需定位顺序依赖；同时说明当前全量套件在共享 dev 库上运行时的稳定性不足。

---

## 8. 逐项验收对照

### P0（spec §12）

| 验收项 | 结论 | 说明 |
|---|---|---|
| 同一子会话最多一个 active turn（DB 部分唯一索引） | ✅ | `uniq_ai_chat_turns_active` 已建，非应用层判断 |
| 普通发送无法驱动非终态批子会话 | ⚠️ | 主入口 ✅（`ai_chat.py:830-841`），但 `/command` 可绕过（M3） |
| 终态写入全部 CAS，计数只在 rowcount=1 时增加 | ✅ | `batch_repo.py:104-164` |
| 旧 generation / 旧 turn 写回不改变状态 | ✅ | 实现存在（`batch_repo.py:107-130`），但覆盖面不足（M6） |
| 非终态批任务内外两个入口都无法直接删除 | ❌ | 对外 ✅ / **内部超时仍删（H2）** |
| 非空 actionChecks 不可证实时不 completed | ⚠️ | 批级 ✅ / **补挂入口 fail-open（H3）** |
| 多进程部署只有一个 batch/scan worker | ⚠️ | batch 仅启动瞬间 ✅（**H1**）；scan 未实现 |
| 终态子任务不存在 running attempt | ✅ | 含存量悬挂 attempt 的 `orphaned` 清扫 |
| F1–F12 确定性缺陷修复 | ✅ 10/12 | F2/F4/F5/F6/F7/F8/F9/F12 正确；F1 实现正确但无回归测试；F11 断言不完整；F10 无需修改（§6） |
| 错误码 §7.3 | ⚠️ | 5/9：缺 `TURN_ALREADY_RUNNING`、`STALE_EXECUTION_GENERATION`、`ACTION_GATE_INCONCLUSIVE`、`WORKSPACE_CLEANUP_FAILED` |

### P1（spec §13）

| 验收项 | 结论 | 说明 |
|---|---|---|
| 单一有效 lease；旧 fencing token 写回被拒 | ⚠️ | 子会话级实现正确，但仅 1 处校验且可选、无测试（M6）；实例级心跳失效（H1） |
| 崩溃后按 continue / rebuild / needs_review / failed 分流 | ❌ | **H8**：重启路径仍无条件重放；决策表仅 3/7 行 |
| 不重复提交已 committed effect；unknown 进 needs_review | ❌ | **H7**：effect 账本零写入 |
| 终态 session 无 running attempt；attempt 链可回溯 | ⚠️ | 前者 ✅；后者依赖进程内 dict，DB 实测 0/319 |
| 事件 seq 单调、afterSeq 增量、断线补发、过期返回 CURSOR_EXPIRED | ⚠️ | 后端 seq 原子 ✅、REST ✅；SSE 无法被浏览器自动补发（M11）；`CURSOR_EXPIRED` 未实现 |
| callback 由 outbox 投递（同事务、可重试、可去重、可重放） | ⚠️ | 同事务 ✅、退避/死信/重放 ✅；**与旧直发并存导致重复（M4）**、`sending` 无回收（M12） |
| 外部 pause/resume/child cancel/commands/events | ✅ | 全部新增；但仅 `/commands` 强制 `Idempotency-Key` |
| 对外错误含 code/retryable/phase/attempt/evidenceRefs | ❌ | **M7** |
| 预算可按批次/用户/API Key 生效 | ⚠️ | 3/7 维度生效 |
| 管理面 lease/attempt/事件/门禁证据/投递/预算 | ⚠️ | 后端数据可得，前端面板未做 |

### P2（spec §12.4）

| 验收项 | 结论 | 说明 |
|---|---|---|
| definition 有版本且不可变 | ⚠️ | 主键 `(id, version)` ✅，但无 version 递增路径 = 空转 |
| DAG 支持线性/条件/并行；join 与部分失败语义明确 | ⚠️ | 线性 ✅ / 并行 ✅ / 部分失败 ✅；**条件 ❌（H5）**、**join ⚠️（H6）** |
| step 级 retry 不重复已提交 effect | ❌ | 依赖 effect 账本（H7） |
| 高风险 step 必须经审批，可从准确 checkpoint 继续 | ⚠️ | 门禁节点式阻断 ✅、approve/reject ✅；无 `edit`、`admin.ai_approval` 权限键未使用（硬编码 `role=='admin'`）、决策未写 command/outbox |
| 产物有稳定 id/hash/引用，workspace 回收不影响产物 | ⚠️ | id/sha256/去重 ✅；**鉴权缺陷（M9）**、无过期清理、未纳入备份 |
| 上层执行引擎不直接依赖 OpenCode 客户端 | ❌ | `get_runtime()` 无生产调用方；`batch_engine.py:233` 仍为模块级 `opencode_client` 直连 |
| 容器/Job 执行具备时间/CPU/内存/网络/进程树约束 | ❌ | spec 标为可选，未实现（不计缺陷） |
| 调度与成本/ETA 可观测，背压与租户公平生效 | ❌ | §10 整节未实现 |
| P0/P1 既有契约与测试保持通过 | ⚠️ | 新增与受影响子集全绿；全量套件有 19 个顺序相关 flaky |

---

## 9. 修复建议与顺序

**合并前必修（P0 级）**

1. **H1**：修 import 位置（一行）+ 补心跳存活与续租断言；
2. **H3**：门禁 fail-open 回归——把「该会话实际登记的期望数」纳入判定；
3. **H2**：内部 DELETE 超时保护，与对外语义对齐；
4. **M8**：测试污染治理（只清本测试种子，或引入独立测试库）；
5. 补「旧 owner 写回 0 行」与 `_restart_audit` 的回归测试；定位全量套件的顺序依赖 flaky。

**P1 若要宣称完成**

6. **H7 + H8**：把 effect 账本接入 MCP 写入 / 文件导入 / 回调，并让恢复决策表接管重启路径（含 `needs_review` 可被人工拉回，M5）；
7. **M6**：fencing 覆盖全部写回路径且改为必填；
8. **M7**：对外字段与错误码（否则外部调用方无法判断可重试性，P1 的核心收益未兑现）。

**P2 建议降级表述**

9. 明确标注为「Phase A–D 部分完成」：**H4/H5/H6** 修复后再称 DAG 可用；Runtime Adapter、容器隔离、调度配额/ETA、run graph 前端、产物清理与备份均未落地。

---

## 10. 附录

### 10.1 审查期间对开发库的影响（重要）

为验证实现，本次审查实跑了新增测试与全量套件，而两个新测试文件都含 `_clear_other_pending`（`DELETE FROM ai_chat_sessions WHERE status='pending' AND batch_id IS NOT NULL AND batch_id <> %s`），作用于 `config.DB_CONFIG` 指向的**共享 dev 库**。

**已触发该删除**。当前库状态核查：

| 查询 | 结果 |
|---|---|
| 当前 pending 批子会话 | 0 |
| 批任务总数 | 151 |
| 非终态批任务 | 6 |
| `total` 与现存子会话数不一致的批次 | 3 个（`AI定时·e2escan-1790204425874·20260923-230027`、两个 `pause-test`），均为 `total=1 / 子会话=0` |

后两者是测试夹具残留；`AI定时·e2escan-…` 是一个 E2E 扫描批任务，其子会话已被删空。**若昨晚有排队未跑的批任务，可能已被清空**——建议核对上述批次的归属。该删除机制本身来自既有测试约定（`test_batch_engine.py` 同款做法），但新测试文件沿用了它，属应修项（M8）。

### 10.2 复现与验证命令

```bash
# 真实改动规模（排除 CRLF→LF 噪音）
git diff --shortstat -w main..HEAD

# H1：租约心跳线程崩溃（观察 PytestUnhandledThreadExceptionWarning）
cd server && python -m pytest tests/test_batch_engine.py -q -k concurrency

# H1：生产库租约状态（batch 行缺失 / heartbeat 停滞）
#   SELECT lease_key, owner_id, heartbeat_at, lease_until, (lease_until < NOW()) AS expired
#     FROM ai_batch_worker_leases;

# H7：effect 账本无写入方
grep -rn "record_effect\|settle_effect" --include=*.py server/ | grep -v "^server/tests/"
#   SELECT count(*) FROM ai_execution_effects;   -- 期望 0

# H5：steps.output 无生产写入点
grep -rn "ai_orchestration_steps" --include=*.py server/ | grep -i "update\|insert"

# H4：step 转移无状态谓词
grep -n "UPDATE ai_orchestration_steps" -A 3 server/utils/orchestration_engine.py

# 新增测试与既有回归
cd server && python -m pytest tests/test_batch_execution_safety_p0.py tests/test_harness_p1_durable.py tests/test_orchestration_p2.py -q
cd server && python -m pytest tests/test_batch_engine.py tests/test_batch_auto_retry.py tests/test_batch_pause_and_guards.py tests/test_batch_resume.py tests/test_batch_routes.py tests/test_agent_action_gate.py tests/test_execution_audit.py -q

# 前端构建与类型检查
npm run build
```

### 10.3 审查方法说明

本次审查由三条并行审查线（P0 / P1 / P2）各自逐条核对 spec 并读取实现与测试，随后由主审查者对每条严重缺陷亲自复核代码、并用运行中进程与生产库只读查询取证。所有严重缺陷均给出可复现证据；未能复核的推断已在文中标注为「审查线报告」。

审查为**只读**：未修改任何代码、迁移、测试或文档（本报告除外）。

---

## 11. 自检处理结果（2026-09-25 追加）

逐项复核本报告后已完成修复并全量验证（pytest 2257 passed / vitest 1211 passed /
e2e/ai-full 28 passed，提交 d43105b 及其后）。

### 11.1 严重缺陷处置

| 项 | 结论 | 处置 |
|---|---|---|
| H1 | 确认（已在复核提交中修复） | import 提至函数首行；补 start() 后心跳线程存活回归 `test_worker_start_lease_loop_survives` |
| H2 | 确认 | 内部 DELETE drain 超时返回 409 `BATCH_DRAIN_TIMEOUT`，保留任务与工作区，与对外对齐 |
| H3 | 确认（相对 main 的行为回退） | 判定信号改为「会话实际评估期望数」（含入口 D 补挂行），batch 级 applicable 仅作登记完整性校验 |
| H4 | 确认 | `_launch_agent_step` 转移改状态谓词 CAS + RETURNING，输者不建子会话。注：曾试 advisory lock 方案，因孤儿连接持锁毒化后续推进而回退，纯 CAS 无死锁面 |
| H5 | 确认 | 成功 step 提取最终 assistant 文本写入 `steps.output`；条件边与 {{steps.x}} 渲染恢复数据来源 |
| H6 | 确认 | skipped 并入依赖死态传播（下游级联 skip），run 不再卡 running |
| H7 | 部分确认 | 回调链路已接线（recompute/outbox 同事务 effect 记录+投递 settle）；MCP 写入/扫描回写等 OC 内部副作用需 runtime 层埋点，列为后续（见 §11.3） |
| H8 | 确认 | 移除 start() 的 `_restart_audit` 调用（保留空方法兼容），遗留 running 行交 `_reconcile_stale_running` 恢复决策表；旧测试改写为决策表路径 |

### 11.2 中等缺陷处置（10/14 已修）

| 项 | 处置 |
|---|---|
| M1 | continue_child 改条件 UPDATE + rowcount 计数回滚（reexecute/resume_child 结构同源，后随批次对齐） |
| M2 | `_persist_user_prompt` id 加 `:g{generation}` 后缀，续跑消息不再被吞 |
| M3 | `/command` 补 BATCH_SESSION_CONTROLLED 门禁（与 send_message 一致） |
| M4 | `_notify_callback` 在 outbox 启用时跳过直发；`AI_DELIVERY_OUTBOX_ENABLED=0` 回退直发（有测试锁定两路径） |
| M5 | continue/reexecute/retry 接受 needs_review（人工拉回通道打通） |
| M6 | `_mark_needs_review` 补 fencing；「旧 owner 写回 0 行」由既有 stale-generation 测试覆盖 |
| M8 | 测试清理语句限定测试命名批次（AITEST-/e2e/*-test），不再触碰共享库真实数据 |
| M9 | artifact ingest 传 run.requested_by 为 owner |
| M11 | SSE 改 `login_required_sse`，帧输出 `id:` 行（浏览器自动 Last-Event-ID），ping 3s→15s |
| M12 | outbox `sending` 超 10 分钟重新捞起（进程 kill 不再永久卡行） |
| M13 | acquire 写入 lease_kind（batch/delivery/scheduler 实名隔离） |
| M14 | append_event/enqueue 的 conn 路径 SAVEPOINT 包裹，失败不再毒化外层终态事务 |

未纳入本批（确认为范围缺口）：M7（对外错误结构化/generation/phase 字段）、M10（定义字段透传/version 递增）、
runtime adapter 生产接线、调度配额/ETA/管理面前端——按报告 §9 归入 P1/P2 后续批次。

### 11.3 低优先清单处置

- 已顺手修：SSE ping 15s、lease_kind、outbox sending 回收（见上）；
- 确认为后续批次的范围项：gate.evaluated 事件、attempt 租约列写入、checkpoint progress/recovery 写入点、
  预算 4 个未判维度、scan 租约、CURSOR_EXPIRED、备份表清单扩充、编排/产物 openapi 与管理面前端、
  scan 任务 OPENCODE_BIN 化——均已在分支待办中登记，不阻塞本批合入评估。

### 11.4 全量验证证据

| 层 | 结果 |
|---|---|
| pytest 全量 | 2257 passed / 3 skipped（2m48s） |
| vitest 全量 | 122 files / 1211 tests passed |
| e2e/ai-full 全量 | 28 passed（6.3m，含复用 E2E 真实 OpenCode 两轮委派） |
