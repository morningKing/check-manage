# AI Harness P0/P1/P2 落地核对与验证缺口审计 Spec

> 日期：2026-09-27 ｜ 审计对象：`2026-09-23-ai-harness-p{0,1,2}-*.md` 三份 spec ｜ 代码基线：main `229ffc4`
> 方法：逐节通读三份 spec 提取功能点 → 对照实现（路由/引擎/迁移/前端）→ 对照验证资产
> （pytest 89 例 harness 专项 + 既有套件 + vitest 1207 + e2e 90 例）逐点标注落地与验证状态。
> 口径：**落地** = ✅ 完整 / 🟡 部分 / ❌ 未落地；**验证** = 单测、E2E 各标 ✅ 有效 / ⚠️ 有但弱（绕过生产路径或仅间接）/ ❌ 无。
> 验证有效性按 14 号复审标准计（判别力、是否走生产路径）。

---

## 0. 汇总结论

| 阶段 | 功能点 | ✅ 完整 | 🟡 部分 | ❌ 未落地 | 单测验证 | E2E 验证 |
|---|---|---|---|---|---|---|
| P0 执行安全 | 25 | 22 | 2 | 1 | 23/25 | 6/25 |
| P1 持久化执行 | 22 | 13 | 6 | 3 | 15/22 | 6/22 |
| P2 编排 Runtime | 14 | 7 | 4 | 3 | 10/14 | 4/14 |

- 三条主链路（执行安全 / 持久化执行 / 编排审批）**核心功能全部落地且有验证**；
- 系统性缺口集中在：**故障注入类测试（kill executor）、内部 SSE 前端接线、管理面 UI、Runtime Adapter 生产接线、结果 contract、调度配额可观测**；
- 两个「落地但生产未消费」的点需要特别留意：**checkpoint（恢复决策未实际使用它）**、**ai_runtime_manifests（表在、run 未冻结）**。

---

## 1. P0 执行安全基线

| # | 功能点（spec 章节） | 落地 | 单测 | E2E | 缺口说明 |
|---|---|---|---|---|---|
| 1 | 单一 active turn（部分唯一索引，§4.1/5.1） | ✅ | ✅ `test_claim_creates_turn_and_closes_stale` / `test_active_turn_unique_index_blocks_second` | ✅ harness-safety P0-1（UI+API 双断言） | — |
| 2 | 普通发送 409 `BATCH_SESSION_CONTROLLED`（§7.1） | ✅ | ✅ `test_send_message_rejected_on_running/pending_and_paused/other_users` | ✅ 同上 | 编排子会话也已覆盖（`test_m3_orchestration_child_gates`，15 号加） |
| 3 | 终态子会话 continue 端点 202（§7.1） | ✅ | ✅ `test_internal_continue_endpoint` | ✅ harness-safety「终态子会话经批通道 continue」 | — |
| 4 | CAS 终态写入 + 计数同事务（§6.1） | ✅ | ✅ `test_mark_done_counted_exactly_once` / `test_stale_generation_write_rejected` | ⚠️ 无直接 e2e（并发语义不适合 e2e，单测锁定） | — |
| 5 | fencing_token 必填 + 旧 token 0 行（§6.1，15 号回修） | ✅ | ✅ `test_stale_fencing_token_write_rejected` | ❌ | DB 层语义，无需 e2e |
| 6 | generation 语义：旧写回 0 行、消息不污染（§6.3） | ✅ | ✅ `test_reexecute_and_continue_bump_generation` / `test_persist_skipped_for_stale_generation` | ❌ | 同上 |
| 7 | cancel > pause 优先级（§4.2） | ✅ | ✅ `test_pause_write_blocked_when_cancel_flag_set` / `test_mark_done_redirects_to_pause_flag` | ❌ | — |
| 8 | 内部 DELETE bounded drain → 409 `BATCH_DRAIN_TIMEOUT`（§7.2） | ✅ | ✅ `test_h2_internal_delete_drain_timeout` | ✅ ai-batch-lifecycle 删除治理（含按契约重试） | — |
| 9 | Open API 非终态 DELETE 409 / stop-and-delete（§7.2） | ✅ | ✅ `test_openapi_delete_nonterminal_rejected` / `test_openapi_delete_terminal_ok` | ✅ lifecycle + harness-safety stop-and-delete | — |
| 10 | 门禁 fail-closed（登记/账本/核对不可证实不 completed，§8） | ✅ | ✅ `test_gate_registration_failure_returns_error` / `test_run_one_fail_closed_on_broken_checks` / `test_attach_expectations_fail_closed_*` | ✅ agent-action-gate「gate fail 哨兵」真实批任务 | — |
| 11 | F1 子代理账本先建行后写账本 | ✅ | ❌ **无专项回归**（spec §11.1-6 要求「快速完成多子代理账本完整」） | ❌ | **验证缺口**：靠执行顺序约定，无测试锁定 |
| 12 | F2 `apply_to` 保留+校验 | ✅ | ✅ `test_apply_to_invalid_rejected` / `test_apply_to_preserved_through_validation` / `test_apply_to_end_to_end_registration` | ❌ | — |
| 13 | F4 Open API actionChecks 校验 400 | ✅ | ✅ `test_openapi_create_invalid_action_checks_400` / `test_openapi_patch_action_checks_validated` | ⚠️ openapi 套件间接 | — |
| 14 | F5 paused 子任务单独取消 | ✅ | ✅ `test_f5_cancel_child_on_paused_lands_cancelled` | ❌ | — |
| 15 | F6 append 唯一序号 + 顺序分配 | ✅ | ✅ `test_f6_batch_seq_unique_index` / `test_f6_append_allocates_sequential_seqs` | ❌ | — |
| 16 | F7 retry 保留 paused 聚合 | ✅ | ✅ `test_f7_retry_failed_keeps_paused_status` | ❌ | — |
| 17 | F8 软删 results/usage 口径 | ✅ | ✅ `test_f8_results_exclude_soft_deleted` | ❌ | — |
| 18 | F9 重试 attempt 闭环 | ✅ | ✅ `test_f9_retry_closes_attempt_with_parent` | ❌ | — |
| 19 | F10 前端 paused/cancelled 文案 | ✅ | ❌ **无 vitest 断言**（spec §11.2 要求） | ⚠️ 间接 | **验证缺口** |
| 20 | F11 resume 清双标志回归 | ✅ | ⚠️ 既有 `test_batch_resume.py` 覆盖行为，无专项防回退断言 | ❌ | 低风险 |
| 21 | F12 审计 retention 单次注册 | ✅ | ✅ `test_f12_retention_registered_once` | ❌ | — |
| 22 | 单实例安全（lease 单 owner） | ✅ | ✅ `test_lease_single_owner` / `test_worker_start_without_lease_disables_dispatcher` / `test_worker_start_lease_loop_survives` / `test_start_retries_until_lease_released` | ❌ | — |
| 23 | 错误码表（§7.3） | 🟡 | 已落地并验证：`BATCH_SESSION_CONTROLLED` / `BATCH_NOT_TERMINAL` / `BATCH_DRAIN_TIMEOUT` / `ACTION_CHECK_INVALID`；**未落地**：`TURN_ALREADY_RUNNING`（turn 冲突由 claim 内部消化，未对外暴露）、`STALE_EXECUTION_GENERATION`/`VERSION_CONFLICT`（控制命令 409 用通用文案，无结构化 code）、`OPENCODE_UNAVAILABLE`、`WORKSPACE_CLEANUP_FAILED`、`ACTION_GATE_INCONCLUSIVE`（作为 200 任务态） | ⚠️ 已落地码有 e2e | **M7「对外错误结构化」整体维持未纳入**（07/08 号声明） |
| 24 | 生产代理验证（§11.4，proxy :8080 实测） | ❌ | — | — | **未执行**，无记录 |
| 25 | 竞态矩阵（§12 每格一测） | 🟡 | pause↔cancel ✅、cancel↔完成 ✅（重定向）、retry↔auto-retry ⚠️（`test_batch_auto_retry` 部分）、append↔delete ✅（f6）、stale worker ✅；**cancel↔resume 僵死组合 ❌ 无专项** | ❌ | 缺 1 格 |

**P0 小结**：安全核心全部落地；验证缺口 = F1 账本顺序、F10 前端文案两项无测试锁定，错误码表只落了 4/10，§11.4 代理验证未执行。

---

## 2. P1 持久化执行与控制面

| # | 功能点（spec 章节） | 落地 | 单测 | E2E | 缺口说明 |
|---|---|---|---|---|---|
| 1 | 执行租约落库 + 跨实例接管 + 旧 owner 写回被拒（§4.1/6.1） | ✅ | ✅ `test_claim_sets_lease_and_increments_fencing` / `test_expired_lease_taken_over_by_new_owner` / `test_stale_fencing_token_write_rejected` | ❌ | — |
| 2 | child 租约心跳（§6.2） | ✅ | ✅ `test_child_heartbeat_renewal` | ❌ | — |
| 3 | 恢复决策表（§4.2，7 行） | 🟡 | ✅ `test_reconcile_skips_unexpired_lease` / `test_reconcile_unknown_effect_needs_review` / `test_reconcile_alive_session_requeues_continue` | ❌ | **「有 checkpoint → continue」分支是文案**：恢复实际按「oc 会话存在 → 原会话续跑」分流，`latest_checkpoint` 无生产调用方（08 号定位，未接线） |
| 4 | attempt 闭环（parent_attempt_id、终态无 running attempt） | ✅ | ✅ `test_f9_retry_closes_attempt_with_parent` / `test_attempt_running_unique_per_session` / `test_attempt_events_survive_concurrency` | ❌ | — |
| 5 | checkpoint 持久化（§5.3） | 🟡 | ✅ `test_checkpoint_latest_swap` | ❌ | **原语落地（写入幂等/is_latest 唯一），但生产恢复路径未消费**——与 #3 同一缺口 |
| 6 | effect 账本（幂等 key、unknown 禁自动重放、正向收口） | ✅ | ✅ `test_effect_key_dedup_and_unknown` / `test_record_effect_same_transaction_rollback` / `test_settle_effect_by_key_transitions` / `test_outbox_uncertain_delivery_sets_unknown_effect` / `test_settle_unknown_can_fail_later` | ❌（dev 库 effects=0 行：仅 callback 批次登记，常规 e2e 不产生） | unknown→needs_review 的**端到端恢复场景**无 e2e（kill 注入缺失，见 #20） |
| 7 | 事件流原子 seq（§7.1） | ✅ | ✅ `test_events_concurrent_append_no_loss` / `test_events_after_seq_semantics` / SAVEPOINT 隔离 `test_m14_*` | ✅ harness-safety 事件流断言 | 并发压测曾出现 1 次负载性 flaky（单跑稳定） |
| 8 | 内部 SSE（`/batches/events`，Last-Event-ID 补发，§7.2） | 🟡 | ❌ 无帧格式测试 | ❌ | **后端能力完整**（login_required_sse、`id:` 帧、15s ping、断线补发），但**前端无消费者**（仍轮询降级）；spec §12.3「断开重连补发无漏帧」未做 |
| 9 | 对外事件读取 afterSeq（§7.3） | ✅ | ✅ `test_events_after_seq_semantics` | ✅ harness-safety | — |
| 10 | `CURSOR_EXPIRED` / 事件保留策略 | ❌ | ❌ | ❌ | 事件无保留期清理，语义无从触发 |
| 11 | outbox 可靠投递（同事务/幂等/退避/dead_letter/重放） | ✅ | ✅ `test_outbox_enqueue_idempotent` / `test_outbox_delivery_and_replay` / `test_outbox_dead_letter_and_manual_replay` / `test_deliver_one_real_classification` / `test_settle_skips_event_for_deleted_batch` | ✅ openapi「HMAC 完成回调（真实子任务收敛触发）」 | 分类语义（2xx/4xx/超时三分）15 号回修后落地 |
| 12 | 命令平面（幂等键/expected_generation/终态拒绝，§4.4/5.5） | ✅ | ✅ `test_command_idempotency` | ✅ harness-safety「命令幂等」 | **内部统一命令入口路由未做**（对外 `/v1` commands ✅、内部既有 pause/resume 路由保留，未收编成 command） |
| 13 | attempts 查询端点（§8.1） | ✅ | ❌ 无专项测试 | ❌ | `ai_chat_batches.py:546` 只返回数据 |
| 14 | 管理端投递状态 + 重放（§8.1/§10） | ✅ | ✅ `test_outbox_dead_letter_and_manual_replay` | ❌ | 路由在（`ai_batch_admin.py:373-387`）；**前端重放按钮 ❌** |
| 15 | 管理面其余区块（§10：lease/attempt 链/事件时间线/门禁证据/预算 UI） | ❌ | — | — | **未落地**（AiBatchAdmin.vue 无这些区块） |
| 16 | 资源预算（§9：token/cost 维度、claim 前并发检查、budget.exceeded） | 🟡 | ✅ `test_budget_evaluate_and_claim_block` | ❌ spec §12.3「超预算触发 BUDGET_EXCEEDED 且 UI 可见」未做 | 已落地：batch scope 的 tokens/cost + drain/abort；**未落地**：wall_clock/tool_calls/subagents/workspace_bytes 维度、user/api_key/tenant scope 实战、`max_concurrency` |
| 17 | workspace 生命周期（retention 天数、QUOTA_MB 消费，§9.3） | ❌ | ❌ | ❌ | `AI_WORKSPACE_QUOTA_MB` 仍在 config 中无消费方 |
| 18 | 对外 pause/resume/child cancel（§8.2） | ✅ | ⚠️ 内部语义有测试，对外路由无专项 | ⚠️ `ai-chat-stop-resume`（内部 UI 路径） | 对外路由在（open_api_batches.py:995+） |
| 19 | childId 对外别名（§8.2） | 🟡 | ⚠️ openapi results 契约测试间接 | ⚠️ | seq/文件名寻址保留；childId 字段覆盖面未逐端点核对 |
| 20 | 故障注入（§12.2：kill executor / serve 重启 / 投递中 kill） | ❌ | ⚠️ reconcile 系列等价覆盖「lease 过期+会话 404/存活」两行 | ❌ | **系统性故障注入未执行**（spec 要求 dispatch/tool/effect/approval 前后 kill） |
| 21 | 对外错误结构化（§8.3：code/retryable/phase/attempt/evidenceRefs） | ❌ | ❌ | ❌ | = M7，07/08 号声明维持未纳入 |
| 22 | 验收量化指标（§13：SSE ≤2s、回调 8 次内 100% 等） | ❌ | ❌ | ❌ | 未测量 |

**P1 小结**：租约/事件/outbox/命令/预算主链路落地且验证充分；**三大缺口** = checkpoint 未接入恢复决策（半成品）、故障注入测试缺失、管理面 UI 与前端 SSE 接线未做。

---

## 3. P2 编排与 Runtime

| # | 功能点（spec 章节） | 落地 | 单测 | E2E | 缺口说明 |
|---|---|---|---|---|---|
| 1 | 版本化 definition + 发布校验（环/悬空边/join/条件字段，§5.1/6.2） | ✅ | ✅ `test_definition_validation_rejects_bad_graphs` / `test_publish_and_get_latest` | ❌ | — |
| 2 | run 冻结定义版本与输入（§5.2） | ✅ | ✅ `test_linear_dag_end_to_end` | ✅ harness-safety P2 DAG | — |
| 3 | 线性 DAG 依赖推进 | ✅ | ✅ 同上 | ✅ 同用例 | — |
| 4 | 条件分支（命中/默认/无出边） | ✅ | ✅ `test_conditional_branch` | ✅（同用例条件边） | `field='text'` 三算子（H5 回修）；结构化字段条件见 #9 |
| 5 | 并行 fan-out / fan-in / join 语义 | ✅ | ✅ `test_parallel_fanout_join` | ⚠️ 同用例含并行段 | — |
| 6 | step CAS 派发（并发只建一个子会话） | ✅ | ✅ `test_h4_concurrent_advance_dispatches_once` / `test_h4_failed_step_not_redispatched` | ❌ | — |
| 7 | 审批流（waiting_approval / approve / reject / 超时过期，§7） | ✅ | ✅ `test_approval_flow_approve` / `test_approval_reject_fails_run` / `test_approval_timeout_expires` | ✅ harness-safety P2（真实等待审批） | **edit（改参数后批准）❌；策略拦截（approval_policy 按 tool/effect）❌；运行中人工挂起 step ❌** |
| 8 | 审批投影到工作流收件箱（Phase B） | ✅ | ❌（前端 vitest 无） | ✅ WorkflowInbox 渲染 AI 审批（e2e DAG 用例路径） | — |
| 9 | 结果 contract（§6.1-6：schema/文件/DB 校验 step 输出） | 🟡 | ⚠️ 间接（`test_linear_dag_end_to_end` 走 file 校验路径） | ❌ | **【8 号核对勘误】`file` 类型最小集已实现**（`orchestration_engine.py:443-480`：`output_contract.type='file'` 检查产物存在，不过则 step 不 succeeded）；`json_schema/db_record/external_response/action` 未实现。原判「❌ 全部未实现」有误，对照《落地与验证汇总》P2-14 更正 |
| 10 | compensation 补偿（§6.1-9） | 🟡 | ⚠️ 边类型校验含 compensation（`VALID_EDGE_KINDS`） | ❌ | **补偿执行引擎未实现** |
| 11 | Artifact Store（内容寻址/去重/引用/权限，§5.5/9） | ✅ | ✅ `test_artifact_put_dedup_and_auth` / `test_ingest_session_outputs` / `test_m9_artifact_dedup_scoped_by_owner` | ❌ 下载/隔离无 e2e | 过期清理（保留策略）❌ |
| 12 | Runtime Adapter 接口 + OpenCode 本地实现（§8，Phase D） | 🟡 | ✅ `test_runtime_adapter_default_and_capabilities` / `test_opencode_local_wraps_client` | ❌ | **生产执行未切 adapter**（仍走批 worker/OpenCode 直连）——多轮复审登记的残留 |
| 13 | 容器 / Windows Job / K8s adapter（Phase E） | ❌ | ❌ | ❌ | 未启动 |
| 14 | `ai_runtime_manifests` run 冻结 manifest（§5.6） | ❌ | ❌ | ❌ | **表已建、`runtime_manifest_id` 无写入方/消费方**（落地但未接线，同 checkpoint 形态） |
| 15 | 编排 API（`routes/ai_orchestrations.py`：list/publish/create_run/runs/run_events） | ✅ | ⚠️ 经引擎单测间接覆盖；权限键 `admin.ai_orchestration_admin` 有测试 | ⚠️ e2e 经 admin 同权路径 | spec 的对外 `/v1/ai-orchestrations/*`（§6.3）❌ 未暴露 |
| 16 | Phase A：批任务隐式单 step run 投影 | ❌ | ❌ | ❌ | 未启动 |
| 17 | 调度/配额/ETA/成本/run graph 管理面（目标 7 / Phase F） | ❌ | ❌ | ❌ | 未启动 |
| 18 | 「模型不得推进 DAG」+ declared_plan（§6.4） | 🟡 | ✅ 状态仅服务端推进有测试 | ❌ | todo_trace 的 `declared_plan` 记录集成 ❌ |

**P2 小结**：DAG/审批/Artifact/调度器主链路完整且经真实审批 E2E 验证；**缺口** = 结果 contract 多类型（`file` 最小集已落地）、补偿执行、runtime manifest 冻结、adapter 生产接线与容器/K8s、管理面与对外 /v1 契约、artifacts 备份/保留期、审批超时升级（【8 号核对增补】后三项源自《落地与验证汇总》）。

---

## 4. 「落地但未验证 / 落地未接线」专项清单

按 16 号复审「新增测试必须在修复前代码上失败」「验证须走生产路径」的标准，以下是**当前最容易被误认为已完成**的条目：

| 项 | 状态 | 风险 |
|---|---|---|
| checkpoint（P1 §5.3） | 原语+单测 ✅；**恢复决策不消费它**（「有 checkpoint→continue」是文案） | 中：故障恢复粒度退化为「会话存在性」二分 |
| ai_runtime_manifests（P2 §5.6） | 表 ✅；run 不冻结、无读写方 | 低：当前单 runtime 环境无感 |
| 内部批任务 SSE（P1 §7.2） | 后端 ✅；**前端零消费者**，补发语义无 e2e | 中：轮询降级掩盖断线补发缺陷 |
| effect unknown→needs_review（P1 §4.3） | 单测 ✅（15 号回修后走真实分类）；**e2e/生产 0 行**（无 callback 批次） | 中：端到端恢复场景未实证 |
| attempts/deliveries 管理端点（P1 §8.1） | 路由 ✅；无专项测试、无前端 | 低 |
| F1 账本 FK 顺序（P0 F1） | 代码顺序已调；**无回归测试** | 中：重构易回退 |
| F10 前端终态文案（P0 F10） | 已实现；**无 vitest** | 低 |

## 5. 「未落地」功能点汇总（按建议补齐优先级）

| 优先级 | 项 | 来源 |
|---|---|---|
| P1 高 | 故障注入测试（kill executor / serve 重启 / 投递中 kill）——spec 验收的核心证据 | P1 §12.2 |
| P1 高 | checkpoint 接入恢复决策（或从 spec 中降级该分支为可选） | P1 §4.2 |
| P1 中 | 内部 SSE 前端接线 + 断线补发 e2e | P1 §7.2/§12.3 |
| P1 中 | 预算补全维度（wall_clock/tool/subagent/workspace_bytes、user/api_key scope）+ BUDGET_EXCEEDED e2e | P1 §9 |
| P1 低 | 对外错误结构化（M7）、CURSOR_EXPIRED、workspace retention/QUOTA、管理面 UI | P1 §8.3/§9.3/§10 |
| P2 高 | 结果 contract（step 输出 schema 校验） | P2 §6.1-6 |
| P2 高 | Runtime adapter 生产接线（至少让编排 agent step 走 adapter） | P2 §8 |
| P2 中 | 审批 edit / 策略拦截 / 人工挂起；补偿执行；runtime manifest 冻结 | P2 §7/§6.1 |
| P2 低 | Phase A 投影、调度配额 ETA 成本、run graph 管理面、对外 /v1/ai-orchestrations | P2 Phase A/F |
| P0 中 | 错误码表补齐（TURN_ALREADY_RUNNING / STALE_EXECUTION_GENERATION / OPENCODE_UNAVAILABLE / WORKSPACE_CLEANUP_FAILED） | P0 §7.3 |
| P0 低 | 竞态矩阵 cancel↔resume 僵死组合专项、F1/F10 回归测试、proxy :8080 生产代理验证 | P0 §12/§11.4 |

## 6. 验收对照（spec 验收标准逐条）

- P0 §12 八条不变量：**七条有测试/运行态证据**；「多进程单实例」由租约单测+运行态三租约佐证；生产代理验证未做。
- P1 §13 十条验收：**七条满足**（租约/恢复分流/attempt/事件/outbox/命令/预算核心）；「checkpoint 恢复」「对外错误结构化」「管理面可见」三条未满足；量化指标未测量。
- P2 §12.4：DAG/审批/artifact 验收满足；runtime 隔离、调度公平、结果 contract 未满足。



---

## 8. 二次核对增补（2026-09-27，对照《2026-09-27-ai-harness-p0-p1-p2-落地与验证汇总.md》）

《落地与验证汇总》（独立核查，基线 `a04b653`）与本审计交叉核对后，发现本审计 **8 处遗漏/偏差**，已逐条 `git grep` 复核属实并收录如下；同时本审计亦有两项为《汇总》未覆盖的补充（见 8.3）。两文档合并后即完整核对集。

### 8.1 本审计遗漏的功能点（已核实，均为 ❌ 未落地）

| # | 功能点 | Spec 章节 | 核实证据 | 应归属 |
|---|---|---|---|---|
| A1 | `gate_status` 变更写 `ai_execution_events`（`event_type='gate.evaluated'`） | P0 §8.3 | 全仓仅迁移注释提及，无写入方 | P0 审计表新增 |
| A2 | `AI_BATCH_STRICT_OWNERSHIP` 降级开关（多进程渐进迁移用） | P0 §10.3 | 全仓零命中 | P0 审计表新增 |
| A3 | scan scheduler 单实例租约（纳入 `ai_batch_worker_leases` 体系） | P1 §5.8 | `ai_scan_scheduler.py` 仅用进程内/文件锁 `lock.acquire`，非租约表 | P1 审计表新增 |
| A4 | `force_stop` 命令处理逻辑 | P1 §6.4/§8.2 | 仅列入 `COMMAND_TYPES`（`execution_commands.py:18-19`），无专属处理分支/端点语义 | P1 命令行细化 |
| A5 | checkpoint 写入时机补全（`progress`/`recovery`） | P1 §5.3 | 仅 `dispatch`（batch_engine:1250）与 `turn_complete`（:1436）两处 | P1 checkpoint 行细化 |
| A6 | artifacts 纳入备份 | P2 §9.3 | `backup.py` 明确排除 `AI_WORKSPACE_ROOT`（artifacts 在其下）→ 产物不随备份 | P2 审计表新增 |
| A7 | artifacts 保留期/过期清理 | P2 §9.3 | 未实现（本审计 Artifact 行只提了「过期清理❌」，未单列） | P2 审计表新增 |
| A8 | 审批超时**升级/通知** | P2 §7.2 | 仅 `expire_overdue` 置 expired（按 reject 处理），代码注释自认「升级语义后续扩展」（approval_repo.py:99） | P2 审批行细化 |

### 8.2 本审计的判断修正

| 项 | 原判断 | 修正后 | 依据 |
|---|---|---|---|
| P2 结果 contract | ❌ 全部未实现 | 🟡 `file` 最小集**已实现**（含条件拦截：contract 不过 step 不 succeeded），`json_schema/db_record/external_response/action` 未实现 | `orchestration_engine.py:443-480` 实读 |
| P1 命令平面 | 「内部统一命令入口路由未做」 | 补充实现取舍：**同步应用**（路由内联 CAS，pause 立即生效）+ 幂等/审计，**无异步 worker 消费**——`execution_commands.py` docstring 已声明该取舍 | 模块 docstring 实读 |
| P0 F10 前端文案 | 「无 vitest ❌」 | 验证口径放宽：文案映射存在 + e2e 间接断言（stop-resume 流程的暂停/继续文案）；专项 vitest 断言仍缺 | 《汇总》P0-21 |

### 8.3 本审计独有、《汇总》未覆盖的补充项

| 项 | 说明 |
|---|---|
| P0 §11.4 生产代理验证（proxy :8080）从未执行 | 两文档合并后才覆盖 |
| P0 §12 竞态矩阵逐格盘点（cancel↔resume 僵死组合无专项测试） | 同上 |
| P2 §6.4 `declared_plan`/todo_trace 集成缺失（「模型不得推进 DAG」的状态推进部分 ✅，Todo 记录部分未接） | 同上 |
| P2 对外 `/v1/ai-orchestrations/*` 契约未暴露 | 同上 |
| P1 验收量化指标（SSE ≤2s、回调 8 次内 100%）从未测量 | 同上 |

### 8.4 基线差异说明

《汇总》基线 `a04b653`；本审计基线 `229ffc4`（多 5 个提交：15/16 号复审处置 + 14 号文件入库 + `_settle` 批次存活检查），两者对本审计功能点清单均无影响（差异提交只动文档与孤儿事件回收）。

### 8.5 合并后的未落地清单（最终口径，替代本审计 §5 与《汇总》§4 的并集）

**P0**：① `gate.evaluated` 事件（A1）② `AI_BATCH_STRICT_OWNERSHIP`（A2）③ 错误码表补齐（4/10）
**P1**：④ 前端 SSE 主通道+cursor 客户端 ⑤ 内部 commands 端点（A：`force_stop` 处理、异步消费取舍已声明）⑥ effect 生产写入方（mcp_write/file_import/scan_writeback/artifact）⑦ checkpoint 写入时机补全 + 恢复决策消费（A5 + 本审计 #3/#5）⑧ `workspace_bytes`/`AI_WORKSPACE_QUOTA_MB` ⑨ workspace retention 回收 ⑩ scan scheduler 租约（A3）⑪ 管理面前端面板 ⑫ 对外状态扩展字段（generation/queueWaitMs/runningMs/lastProgressAt/phase）⑬ CURSOR_EXPIRED/事件保留 ⑭ 故障注入测试 ⑮ 对外错误结构化（M7）
**P2**：⑯ Runtime adapter 生产接线 + `ai_runtime_manifests` 启用 ⑰ 结果 contract 多类型（file 已落地）⑱ 补偿执行 ⑲ 审批 edit/超时升级（A8）/策略拦截/人工挂起 ⑳ artifacts 备份接入 + 保留期清理（A6/A7）㉑ 调度优先级/公平/限流 ㉒ 可观测 ETA/成本 ㉓ 编排管理面 UI + run graph ㉔ `/v1/ai-orchestrations` 对外契约 ㉕ Phase A 投影 ㉖ Docker/Job/K8s adapter（可选）㉗ `declared_plan` 集成

## 9. 维护说明

- 本文档为核对基线，后续每轮补齐应直接更新对应行状态并注明提交号；
- 已知残留（M7/M10/M11 前端、effect 生产写入方、深链挂载竞态等）已在 09–16 号报告登记，本文不重复展开，仅收录 spec 功能点维度。
