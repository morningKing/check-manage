# AI Harness P0/P1/P2 落地与验证汇总 Spec

**版本**：v1.0
**日期**：2026-09-27
**性质**：对三份实现 Spec 的**逐功能点落地核查 + 验证覆盖汇总**（只读排查，不改代码）
**核查对象**：
- `docs/superpowers/specs/2026-09-23-ai-harness-p0-execution-safety-spec.md`
- `docs/superpowers/specs/2026-09-23-ai-harness-p1-durable-execution-spec.md`
- `docs/superpowers/specs/2026-09-23-ai-harness-p2-orchestration-runtime-spec.md`

**核查方法**：以工作树（HEAD `a04b653`，产品代码最后变更 `83309ed`/`d6e960a`）为准，逐条 `git grep` 定位实现与测试；测试覆盖以 `server/tests/*.py` 与 `e2e/ai-full/ai-harness-safety.spec.ts` 为准。未运行测试（数量以既有报告留档为准）。

---

## 0. 状态图例

| 记号 | 含义 |
|---|---|
| ✅ | 已落地，且有单元测试**或** e2e 覆盖 |
| 🟡 | 已落地，但**无验证**（单测/e2e 均无） |
| 🟠 | 部分落地（子项缺失或实现与 Spec 有偏差） |
| ❌ | 未落地 |
| — | 按设计不适用（说明原因） |

**验证资产基线**

| 层 | 文件 | 规模 |
|---|---|---|
| P0 单测 | `server/tests/test_batch_execution_safety_p0.py` | ~35 例 |
| P1 单测 | `server/tests/test_harness_p1_durable.py` | 17 例 |
| P2 单测 | `server/tests/test_orchestration_p2.py` | 12 例 |
| E2E | `e2e/ai-full/ai-harness-safety.spec.ts` | 3 例（P0/P1 全链路、P0 continue、P2 3 节点 DAG） |
| 前端 | `src/views/workflow/__tests__/WorkflowInbox.test.ts`（AI 审批投影）、`src/utils/__tests__/artifacts.test.ts` | — |

> **关键结论**：P0 基本完整（✅）；P1 核心机制落地，但**前端 SSE 客户端、工作区/配额生命周期、管理面 UI、内部 commands 端点、checkpoint 的 progress/recovery 写入**缺失或未验证；P2 的**编排内核与审批/产物最小集落地，但 Runtime Adapter 未接入执行路径、runtime manifest 未启用、output_contract 仅 file、补偿/调度配额/管理面 UI 未落地**。

---

## 1. P0 执行安全基线

### 1.1 数据模型与 CAS

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P0-1 | `ai_chat_turns` 表 + 3 索引（active 部分唯一） | §5.1 | `migrations/2026_09_23_harness_p0_execution_safety.py:20-46` | `test_claim_creates_turn_and_closes_stale`、`test_active_turn_unique_index_blocks_second`、`test_mark_done_closes_turn` | 隐式（全链路） | ✅ |
| P0-2 | `ai_chat_sessions` 增列 `execution_generation`/`active_turn_id`/`gate_status/gate_error/gate_checked_at` | §5.2/§5.4 | 同迁移 `:51-56` | 多例 | — | ✅ |
| P0-3 | `ai_batch_worker_leases` 表 | §5.3 | 同迁移 `:60+` | `test_lease_single_owner`、`test_worker_start_without_lease_disables_dispatcher` | — | ✅ |
| P0-4 | 终态 CAS（`transition_child` 单入口，rowcount 才计数） | §6.1 | `batch_repo.transition_child`（fencing/generation/标志谓词） | `test_mark_done_counted_exactly_once`、`test_stale_generation_write_rejected`、`test_mark_done_redirects_to_pause_flag` | — | ✅ |
| P0-5 | 控制命令 CAS（cancel/pause/resume/retry/continue/reexecute/cancel_child） | §6.2 | `batch_repo.py` | `test_f5_cancel_child_on_paused_lands_cancelled`、`test_f7_retry_failed_keeps_paused_status`、`test_continue_completed_child_keeps_counters` | — | ✅ |
| P0-6 | generation 语义（写回失配 0 行、旧 turn 消息不入库） | §6.3 | `batch_engine.py` | `test_reexecute_and_continue_bump_generation`、`test_persist_skipped_for_stale_generation` | — | ✅ |

### 1.2 API 契约与删除语义

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P0-7 | 普通发送门禁 `409 BATCH_SESSION_CONTROLLED` | §7.1 | `routes/ai_chat.py` `_execution_controlled` | `test_send_message_rejected_on_running_batch_child`、`..._pending_and_paused_child`、`..._other_users_session_404` | ✅ 全链路 | ✅ |
| P0-8 | 内部 continue 端点（202，CAS+写消息+递增 generation） | §7.1 | `routes/ai_chat_batches.py` | `test_internal_continue_endpoint` | ✅ continue 用例 | ✅ |
| P0-9 | 对外 DELETE：非终态 409 / stop=true drain / 超时 409 保留 | §7.2 | `routes/open_api_batches.py` | `test_openapi_delete_nonterminal_rejected`、`test_openapi_delete_terminal_ok` | ✅ 全链路 | ✅ |
| P0-10 | 对外 `actionChecks` 校验 + PATCH 扩展 | §7.2 | `open_api_batches.py` | `test_openapi_create_invalid_action_checks_400`、`test_openapi_patch_action_checks_validated` | — | ✅ |
| P0-11 | 结构化错误码表 | §7.3 | 散落实现 | 部分错误码被断言 | — | 🟠 仅部分错误码有覆盖 |

### 1.3 门禁 fail-closed 与确定性缺陷

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P0-12 | 门禁 fail-closed（登记/账本/核对任一不可证实 → inconclusive 不 completed） | §8.1 | `batch_engine.py:1298-1351`、`agent_ledger.gate_participates` | `test_gate_registration_failure_returns_error`、`test_run_one_fail_closed_on_broken_checks`、`test_run_one_completes_without_checks`、`test_attach_expectations_fail_closed_on_unhealthy_ledger` | — | ✅ |
| P0-13 | `apply_to` 保留与校验（F2） | §8.2 | `agent_ledger.validate_checks` | `test_apply_to_invalid_rejected`、`test_apply_to_preserved_through_validation`、`test_apply_to_end_to_end_registration` | — | ✅ |
| P0-14 | 子代理账本 FK 顺序（F1：先 upsert subtask 再写账本） | §8.2 | `batch_engine.py:2300`（`_write_subtask`）先于 `:2324`（`record_messages`） | `test_subagent_reuse.py` 的 `_write_subtask` 用例（间接）；无"快速完成多子代理账本无缺口"专项断言 | — | 🟠 |
| P0-15 | `gate_status` 变更写 `ai_execution_events`（`gate.evaluated`） | §8.3 | **无写入方**（仅迁移注释提及） | — | — | ❌ |
| P0-16 | F5 paused 子任务可取消 | §9 | `batch_repo.cancel_child` | `test_f5_cancel_child_on_paused_lands_cancelled` | — | ✅ |
| P0-17 | F6 `UNIQUE(batch_id,batch_seq)` + 顺序分配 | §9 | 迁移 `:101` | `test_f6_batch_seq_unique_index`、`test_f6_append_allocates_sequential_seqs` | — | ✅ |
| P0-18 | F7 retry 保留 paused 聚合 | §9 | `batch_repo` 统一重算 | `test_f7_retry_failed_keeps_paused_status` | — | ✅ |
| P0-19 | F8 软删后 results/usage 口径 | §9 | `batch_repo` | `test_f8_results_exclude_soft_deleted` | — | ✅ |
| P0-20 | F9 重试关闭旧 attempt + `parent_attempt_id` | §9 | `batch_engine` | `test_f9_retry_closes_attempt_with_parent` | — | ✅ |
| P0-21 | F10 前端 `paused`/`cancelled` 文案 | §9 | `AiChatView.vue:297-298,845-847` | — | E2E 断言文案正则 | ✅ |
| P0-22 | F11 resume 清双标志（回归约束） | §9 | 既有行为（`batch_repo` resume 清 cancel/pause） | 仅 `cancel > pause` 有测（`:354`）；无"resume 后两标志均 false"专项回归断言 | — | 🟠 |
| P0-23 | F12 retention 任务单次注册 | §9 | `app.py` | `test_f12_retention_registered_once` | — | ✅ |
| P0-24 | 单实例 lease | §5.3 | `batch_engine` acquire dispatcher | `test_lease_single_owner`、`test_worker_start_lease_loop_survives` | — | ✅ |
| P0-25 | 降级开关 `AI_BATCH_STRICT_OWNERSHIP` | §10.3 | **未实现** | — | — | ❌ |

**P0 小结**：25 项中 ✅20、🟠3、❌2（`gate.evaluated` 事件、降级开关；🟠 为 F1 账本缺口无专项断言、F11 无回归断言、错误码覆盖不全）。执行安全核心（ownership/CAS/generation/删除/门禁）完整且验证充分。

---

## 2. P1 持久化执行

### 2.1 租约 / 恢复 / attempt

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P1-1 | `execution_lease` 原语 + `ai_chat_sessions` 租约列 + `ai_execution_attempts` 扩展 | §5.1/§5.2 | `utils/execution_lease.py`、迁移 `:27-37` | `test_claim_sets_lease_and_increments_fencing`、`test_expired_lease_taken_over_by_new_owner`、`test_child_heartbeat_renewal` | — | ✅ |
| P1-2 | 对账/接管决策（lease 未过期跳过 / 404 失败 / unknown→needs_review / 续跑） | §4.2/§6.3 | `batch_engine._reconcile_stale_running:2611-2730` | `test_reconcile_skips_unexpired_lease`、`test_reconcile_unknown_effect_needs_review`、`test_reconcile_alive_session_requeues_continue` | — | ✅ |
| P1-3 | 恢复决策表**完整对齐** | §4.2 | 偏差：`有 oc 无 checkpoint` 实现为原地续跑、`无 oc` 实现为重排（非 spec 的 needs_review/failed），已在 docstring 声明取舍 | — | — | 🟠 |
| P1-4 | attempt 闭环 + `parent_attempt_id` + 每 session 唯一 running | §2.1/§5.1 | 迁移 `uniq_attempt_running_per_session:57` | `test_attempt_running_unique_per_session`、`test_attempt_events_survive_concurrency`、`test_f9_...` | — | ✅ |
| P1-5 | 子会话租约心跳（20s 续租，失租即停） | §6.2 | `batch_engine:1953-1958` | `test_child_heartbeat_renewal` | — | ✅ |

### 2.2 checkpoint / effect / commands

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P1-6 | `ai_execution_checkpoints` 表 + `is_latest` 唯一 | §5.3 | 迁移 `:63-84` | `test_checkpoint_latest_swap` | — | ✅ |
| P1-7 | checkpoint 写入时机（dispatch/**progress**/turn_complete/**recovery**） | §5.3 | 仅 `dispatch`（`batch_engine:1250`）与 `turn_complete`（`:1436`）两处写入 | 部分 | — | 🟠 |
| P1-8 | `ai_execution_effects` 表 + 幂等 key + unknown | §5.4 | 迁移 `:86-105`、`utils/execution_effect.py` | `test_effect_key_dedup_and_unknown`、`test_settle_effect_by_key_transitions`（review_gap） | — | ✅ |
| P1-9 | 各副作用类型的**生产写入方**（mcp_write/file_import/scan_writeback/artifact） | §4.3 | 仅 `callback` 有生产写入方（`batch_engine:411`、`batch_repo:80`）；其余类型无 writer | — | — | ❌ |
| P1-10 | `ai_execution_commands` 表 + 幂等 | §5.5 | `utils/execution_commands.py` | `test_command_idempotency` | ✅ 对外 pause 幂等 | ✅ |
| P1-11 | 命令由 **worker 异步消费** | §4.4 | 实现为**同步应用**（module docstring 声明取舍），无异步消费 | — | — | 🟠 |
| P1-12 | `force_stop` 命令 | §6.4 | 仅列入 `COMMAND_TYPES`，无处理逻辑/端点 | — | — | ❌ |
| P1-13 | 内部命令端点 `POST .../commands` + `GET .../commands/<cid>` | §8.1 | 外部有；**内部无**（`ai_chat_batches.py` 无 commands 路由） | — | — | ❌ |

### 2.3 事件流 / 投递 / 预算 / 管理面

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P1-14 | `ai_batch_events` + 原子 seq | §5.6 | `utils/batch_events.py` | `test_events_concurrent_append_no_loss`、`test_events_after_seq_semantics` | ✅ events API | ✅ |
| P1-15 | 内部 SSE `/ai/chat/batches/events`（cursor/补发/ping） | §7.2 | `ai_chat_batches.py:573-638` | — | ❌（e2e 只测分页 API，未测流式） | 🟡 |
| P1-16 | 前端 SSE 主通道 + cursor 客户端（`src/api/batchEvents.ts`） | §7.2 | **不存在**；前端仍 5s/10s 轮询 | — | — | ❌ |
| P1-17 | 对外 events 端点 | §7.3 | `open_api_batches.py` `/<id>/events` | — | ✅ | ✅ |
| P1-18 | `ai_delivery_outbox` + 同事务入队 + 退避/dead_letter/重放 | §5.7/§7.4 | `utils/delivery_outbox.py`、`batch_engine:378-412` | `test_outbox_enqueue_idempotent`、`..._delivery_and_replay`、`..._dead_letter_and_manual_replay` | 🟠（机制单测；e2e 未覆盖失败重试） | ✅ |
| P1-19 | `delivery`/`scheduler` 独立租约 | §5.8 | `delivery_outbox.py:317-366`（delivery）、`orchestration_engine.py:593-614`（scheduler） | 间接 | — | ✅ |
| P1-20 | **scan scheduler** 单实例租约 | §5.8 | `ai_scan_scheduler.py` 无 lease | — | — | ❌ |
| P1-21 | 对外 pause/resume/child cancel/commands | §8.2 | `open_api_batches.py` 全有 | — | ✅ pause 幂等 | ✅ |
| P1-22 | 对外状态/错误扩展字段（generation/queueWaitMs/runningMs/lastProgressAt/children phase/attempt/retryable/evidenceRefs） | §8.3 | 仅 `retryable` 与 opaque `childId` 落地；其余未实现 | — | — | 🟠 |
| P1-23 | `ai_execution_budgets`/`ai_execution_usage` + 并发/预算判定 | §9.1/§9.2 | `utils/execution_budget.py`、`batch_engine:896,1427-1433` | `test_budget_evaluate_and_claim_block` | — | ✅ |
| P1-24 | `workspace_bytes` 预算 + `AI_WORKSPACE_QUOTA_MB` | §9.1/§9.3 | 配置存在（`config.py:90`）但**零使用** | — | — | ❌ |
| P1-25 | workspace 生命周期回收（`AI_WORKSPACE_RETENTION_DAYS`） | §9.3 | **未实现** | — | — | ❌ |
| P1-26 | 管理面（attempt 链/事件/投递/预算/命令）后端端点 | §10 | attempts✅（`ai_chat_batches`）、deliveries+replay✅（`ai_batch_admin`）、events✅ | — | — | ✅ |
| P1-27 | 管理面**前端面板**（AttemptTimeline/DeliveryPanel/预算/命令） | §10 | `AiBatchAdmin.vue` 无这些区块；无独立组件 | — | — | ❌ |

**P1 小结**：27 项中 ✅15、🟡1、🟠5、❌6。核心持久化机制（租约/fencing/恢复/事件/outbox/effect/checkpoint/预算）落地；**前端 SSE、workspace 生命周期、scan 租约、内部命令端点、effect 生产写入方、管理面 UI** 未落地。

---

## 3. P2 编排与 Runtime

### 3.1 编排内核

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P2-1 | `ai_orchestration_definitions`（版本化/不可变/发布校验） | §5.1/§6.2 | `utils/orchestration_defs.py`、迁移 `:19` | `test_definition_validation_rejects_bad_graphs`、`test_publish_and_get_latest` | — | ✅ |
| P2-2 | `ai_orchestration_runs` / `ai_orchestration_steps` | §5.2/§5.3 | 迁移 `:40/:63`、`utils/orchestration_engine.py` | `test_linear_dag_end_to_end` | ✅ 3 节点 DAG | ✅ |
| P2-3 | DAG：线性 / 条件分支 / 并行 fan-out-fan-in / join | §6.1 | `orchestration_engine._advance_run_locked/_active_targets` | `test_conditional_branch`、`test_parallel_fanout_join`、`test_linear_dag_end_to_end` | — | ✅ |
| P2-4 | 模型不得推进 DAG（Todo 仅 declared_plan） | §6.4 | 设计上 step 状态仅由服务端事实推进 | — | — | ✅ 设计符合 |
| P2-5 | 编排 agent step 复用批 worker 执行 | §4/§6.1 | `_launch_agent_step:330-379` 建 `ai_chat_sessions`（`orchestration_run_id`）由批 worker 认领 | 间接 | ✅ | ✅ |
| P2-6 | 补偿（compensation）执行 | §6.1-9 | `VALID_EDGE_KINDS` 含 `compensation`，但引擎**无执行逻辑** | — | — | ❌ |

### 3.2 审批 / 产物 / contract

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P2-7 | `ai_approval_requests` + approve/reject/超时过期 | §5.4/§7.2 | `utils/approval_repo.py`、`routes/ai_approvals.py` | `test_approval_flow_approve`、`test_approval_reject_fails_run`、`test_approval_timeout_expires` | ✅ approve | ✅ |
| P2-8 | 审批 `edit`（改参后批准） | §7.2 | **未实现** | — | — | ❌ |
| P2-9 | 审批超时**升级/通知** | §7.2 | 仅 `expire_overdue`，注释声明"升级语义后续扩展" | — | — | ❌ |
| P2-10 | inbox 投影 `kind='ai_approval'` + 前端渲染 | §3/§7.2 | `routes/workflows.py:116-119`、`approval_repo.get_pending_for_inbox`、`WorkflowInbox.vue` | `WorkflowInbox.test.ts`（AI 审批项） | — | ✅ |
| P2-11 | `artifacts`/`artifact_refs` + 内容寻址 + owner 隔离 | §5.5/§9.1 | `utils/artifact_store.py`、`migrations/2026_09_25_artifact_owner_scope.py` | `test_artifact_put_dedup_and_auth`、`test_ingest_session_outputs` | — | ✅ |
| P2-12 | artifacts **纳入备份** | §9.3 | `utils/backup.py` 明确排除 `AI_WORKSPACE_ROOT`（artifacts 在其下） | — | — | ❌ |
| P2-13 | artifacts 保留期/过期清理 | §9.3 | 未实现 | — | — | ❌ |
| P2-14 | 结果 contract：`file` | §9.2 | `orchestration_engine.py:443-480` | 间接 | — | ✅ |
| P2-15 | 结果 contract：`json_schema`/`db_record`/`external_response`/`action` | §9.2 | 未实现（仅 `file`） | — | — | ❌ |

### 3.3 Runtime / 调度 / 管理面

| # | 功能点 | Spec | 落地证据 | 单测 | E2E | 状态 |
|---|---|---|---|---|---|---|
| P2-16 | `ai_runtime_manifests` 表 + run 冻结 manifest | §2.3/§5.6 | 表已建（迁移 `:145`），**无任何写入/读取** | — | — | ❌ |
| P2-17 | Runtime Adapter 抽象 + `OpenCodeLocalRuntime` | §8.1/§8.2 | `utils/runtime/{base,opencode_local}.py` 存在 | `test_runtime_adapter_default_and_capabilities`、`test_opencode_local_wraps_client` | — | 🟡 |
| P2-18 | Adapter **接入执行路径**（引擎不直连 OpenCode） | §8.1 | **未接入**：product 代码无 `utils.runtime` 引用；batch/orchestration 仍走既有 client | — | — | ❌ |
| P2-19 | Docker / Windows Job / K8s adapter | §8.2 | 未实现（设计为可选） | — | — | ❌（可选） |
| P2-20 | 调度：step 优先级 / 租户公平 / provider 限流 / 背压 | §10 | 未实现（`orchestration_scheduler.py` 不存在；`start_scheduler` 仅 tick 推进 run） | — | — | ❌ |
| P2-21 | 可观测：queueWaitMs/ETA/成本预测/失败聚合 | §10 | 未实现 | — | — | ❌ |
| P2-22 | Run graph 管理面（`AiOrchestrationManager.vue`/`RunGraphView.vue`/`api/orchestration.ts`） | §13/§10 | **前端不存在**（且 settingsCatalog 无编排入口） | — | — | ❌ |
| P2-23 | 编排/审批/产物路由注册 | §13 | `app.py:371-373` | — | ✅ | ✅ |
| P2-24 | 迁移 Phase A–F | §11 | 仅 Phase C/D 部分（definitions/runs/steps + artifact）；A/B/E/F 未落地 | — | — | 🟠 |

**P2 小结**：24 项中 ✅8、🟡1、🟠1、❌14。编排内核（DAG/线性/条件/并行/审批/产物/事件）真实可用且有单测+e2e；**Runtime Adapter 未接入、runtime manifest 未启用、结果 contract 仅 file、补偿/审批 edit/升级/调度配额/管理面 UI/备份保留**未落地。

---

## 4. 未落地清单（❌，按优先级）

**P0**
1. `gate.evaluated` 事件写入（§8.3）
2. `AI_BATCH_STRICT_OWNERSHIP` 降级开关（§10.3）

**P1**
3. 前端批任务 SSE 主通道 + cursor 客户端（§7.2）——后端已就绪，前端仍轮询
4. 内部 `POST/GET .../commands` 端点（§8.1）——外部有、内部无
5. `force_stop` 命令处理（§6.4/§8.2）
6. effect 生产写入方：`mcp_write`/`file_import`/`scan_writeback`/`artifact`（§4.3）
7. `workspace_bytes` 预算 + `AI_WORKSPACE_QUOTA_MB` 落地（§9.1/§9.3）
8. workspace 生命周期回收 `AI_WORKSPACE_RETENTION_DAYS`（§9.3）
9. scan scheduler 单实例租约（§5.8）
10. 管理面前端面板：Attempt 链 / 投递 / 预算 / 命令（§10）

**P2**
11. Runtime Adapter 接入执行路径（§8.1，含 `ai_runtime_manifests` 启用 §5.6）
12. 结果 contract 的类型补齐 `json_schema`/`db_record`/`external_response`/`action`（§9.2）
13. 补偿步骤执行（§6.1）
14. 审批 `edit` 与超时升级（§7.2）
15. 调度：优先级 / 租户公平 / provider 限流 / 背压（§10）
16. 可观测：ETA / 成本预测 / 失败聚合（§10）
17. 编排管理面 UI + run graph（§13）
18. artifacts 备份接入与保留期清理（§9.3）
19. Docker / Windows Job / K8s adapter（§8.2，可选）

---

## 5. 已落地但验证不足清单（🟡/🟠）

| 项 | 层 | 已落地部分 | 缺失的验证 | 建议 |
|---|---|---|---|---|
| P1-checkpoint 写入时机 | P1 §5.3 | dispatch/turn_complete 两处 | `progress`/`recovery` 写入未实现；无对应单测 | 补写入 + 恢复后不重放已提交 effect 的恢复测试 |
| P1 恢复决策表 | P1 §4.2 | unknown→needs_review、lease skip、404 fail | `无 checkpoint→needs_review`、`无副作用→failed` 两格实现偏差且无测试 | 明确偏差为正式语义并加测试，或补齐 |
| P1 命令平面异步消费 | P1 §4.4 | 同步应用 + 幂等 + 审计 | 无 worker 消费路径测试；`force_stop` 无实现 | 记录取舍并补端点测试 |
| P1 对外状态扩展字段 | P1 §8.3 | 仅 `retryable`/`childId` | generation/queueWaitMs/runningMs/lastProgressAt/phase/attempt 未实现 | 作为对外契约增量排期 |
| P1 内部 SSE 流 | P1 §7.2 | 后端端点存在 | 无流式 e2e（当前 e2e 只测分页）；`proxy.py` 靠通用后缀规则覆盖、未显式登记 | 补 `curl -N`/Playwright SSE 冒烟；在 `_is_sse` 注释登记该路由 |
| P2 Runtime Adapter | P2 §8.1/§8.2 | 模块存在 + 单测 | 未接入执行路径（生产零引用） | 接入 `OpenCodeLocalRuntime` 或删除死代码 |
| P2 output_contract | P2 §9.2 | 仅 `file` | 其余类型未实现、无测试 | 排期补齐或明确裁剪 |
| P0 错误码覆盖 | P0 §7.3 | 部分实现 | 非全部错误码有断言 | 补错误码矩阵测试 |

---

## 6. 验证资产覆盖矩阵（单测 / E2E）

| 能力域 | 单元测试 | E2E | 说明 |
|---|---|---|---|
| P0 发送门禁 / continue / 删除保护 | ✅ 充足 | ✅ 3 例含 | 最完整 |
| P0 CAS / generation / 门禁 fail-closed | ✅ 充足 | — | 纯后端，单测足够 |
| P1 租约 / 恢复 / attempt / 事件 / outbox / effect / 预算 | ✅ 17 例 | 🟠 仅 events/命令幂等 | 恢复/outbox 失败重试无 e2e |
| P1 内部 SSE 流 / 管理面 | 🟡 无 | ❌ | 前端未接，无验证 |
| P2 DAG / 审批 / 产物 | ✅ 12 例 | ✅ 1 例（3 节点 DAG） | 条件/并行/产物无 e2e |
| P2 Runtime Adapter | ✅ 2 例 | — | 仅包装语义，未接入 |
| 前端组件 | 🟠 WorkflowInbox 审批投影；`AiChatView` 文案 | 🟠 部分 | composer 门禁主要靠 e2e + pytest |

---

## 7. 结论

1. **P0 执行安全基线已完整落地并充分验证**，仅 2 个低风险子项（`gate.evaluated` 事件、降级开关）未落地。
2. **P1 持久化执行内核已落地并有扎实单测**（lease/fencing/恢复/事件/outbox/effect/checkpoint/预算）；缺口集中在**前端 SSE、工作区生命周期、内部命令端点、effect 生产写入方、管理面 UI**。
3. **P2 编排内核（DAG/审批/产物）可用且通过 e2e**；Runtime Adapter、runtime manifest、结果 contract 多类型、补偿、调度配额、管理面 UI 未落地——这些多为 Spec 标注的可选/后续项（§2.2 非目标），但 **Runtime Adapter 未接入**与 **runtime manifest 未启用**属"已声明为 P2 首发"却未完成的部分，需明确排期或裁剪。
4. 所有未落地项均**不影响 P0/P1 核心安全契约**；未验证项主要是"已落地但缺端到端证据"，建议按 §5 逐条补测。

> 本汇总为只读核查结论；落地状态以工作树为准，测试数量引用既有报告，未在本轮重跑套件。
