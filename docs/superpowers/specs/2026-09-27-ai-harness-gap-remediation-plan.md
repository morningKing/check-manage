# AI Harness 缺口补齐分批开发计划

> 日期：2026-09-27 ｜ 依据：`2026-09-27-ai-harness-p0-p1-p2-implementation-audit-spec.md`（§8.5 合并清单 27 项）
> 原则：每批**可独立发布**（不破坏既有契约）、**带判别性测试**、全量 pytest 通过后提交；
> 前端改动批次额外跑 vitest + 相关 e2e。状态标记：⬜ 未开始 / 🔄 进行中 / ⬜ 完成（含提交号）。

---

## 批次 1：P0 安全收口 + 高价值快赢（低风险，先行）

| # | 项 | 来源 | 内容 | 状态 |
|---|---|---|---|---|
| 1.1 | `gate.evaluated` 事件 | P0 §8.3 | 门禁核对完成处经 `execution_audit.record_event` 落 `gate.evaluated`（payload 含结论与缺失明细），best-effort | ✅ |
| 1.2 | 错误码表补齐 | P0 §7.3 | `OPENCODE_UNAVAILABLE`（502）、`WORKSPACE_CLEANUP_FAILED`（207/500）、`TURN_ALREADY_RUNNING`（409）、`VERSION_CONFLICT`（命令 generation 失配 409）；`ACTION_GATE_INCONCLUSIVE` 按 spec 属任务态（error_message 承载），不新增 HTTP 码 | ✅ |
| 1.3 | scan scheduler 单实例租约 | P1 §5.8 | `ai_scan_scheduler` 接入 `execution_lease`（key=`scan_scheduler`，acquire-retry 同款），与既有锁并存为 DB 级互斥 | ✅ |
| 1.4 | F1 回归测试 | P0 §9 | `_persist_conversation` 中 `_write_subtask` 先于 `record_messages` 的顺序断言（monkeypatch 调用序） | ✅ |
| 1.5 | F10 回归测试 | P0 §9/§11.2 | vitest：`batchStatusLabel` 含 paused/cancelled 映射 | ✅ |
| 1.6 | 竞态矩阵 cancel↔resume | P0 §12 | 并发 cancel/resume 无「pending 且 cancel_requested=true」僵死组合 | ✅ |

## 批次 2：P1 持久化执行收口

| # | 项 | 来源 | 内容 | 状态 |
|---|---|---|---|---|
| 2.1 | checkpoint 写入时机补全 | P1 §5.3 | worker 轮询循环写 `progress` checkpoint（去抖）；requeue 前写 `recovery` checkpoint | ⬜ |
| 2.2 | 恢复决策消费 checkpoint | P1 §4.2 | reconcile 新增 spec 分支：oc 存活 + 无 checkpoint + 无副作用 + 无消息 → `failed(retryable)`（原盲目 requeue 收敛）；有 checkpoint/有消息维持原地续跑 | ⬜ |
| 2.3 | effect 生产写入方 | P1 §4.3 | `mcp_write`（ai_data_internal）、`file_import`（import_recorded_files）、`scan_writeback`（回写路径）、`artifact`（orchestration ingest）四处按 spec key 登记 planned→committed/failed | ⬜ |
| 2.4 | `AI_WORKSPACE_QUOTA_MB` 消费 | P1 §9.1/§9.3 | workspace 创建时按配额拒绝超限并告警事件 | ⬜ |
| 2.5 | workspace retention 回收 | P1 §9.3 | scheduler 每日任务：终态批次工作区超 `AI_WORKSPACE_RETENTION_DAYS`（默认 30）回收 | ⬜ |
| 2.6 | 事件保留 + `CURSOR_EXPIRED` | P1 §7.3 | `ai_batch_events` 保留期任务（180 天默认）；`afterSeq` 早于最旧保留 seq → `CURSOR_EXPIRED` | ⬜ |
| 2.7 | 对外状态扩展字段 | P1 §8.3 | `/v1/ai-batches` detail 增 `generation/queueWaitMs/runningMs/lastProgressAt`，子会话增 `phase` | ⬜ |
| 2.8 | 故障注入测试 | P1 §12.2 | send 后 kill（RequestException 已执行）→ 续跑收敛；投递中 kill → sending 回收；reconcile 期间 OC 不可达跳过 | ⬜ |

## 批次 3：P2 编排补齐

| # | 项 | 来源 | 内容 | 状态 |
|---|---|---|---|---|
| 3.1 | Runtime Adapter 生产接线 | P2 §8.1 | `BatchWorker`/`orchestration_engine` 的 OC 调用改经 `OpenCodeLocalRuntime`（默认实例，行为不变）；删除「死代码」定位 | ⬜ |
| 3.2 | `ai_runtime_manifests` 启用 | P2 §5.6 | run 创建时冻结 manifest（当前环境 OpenCodeLocal 参数） | ⬜ |
| 3.3 | 结果 contract 多类型 | P2 §9.2 | `json_schema`（output text JSON 校验）、`db_record`（复用账本 db-record 证据）；`external_response`/`action` 登记后续 | ⬜ |
| 3.4 | 审批 edit + 超时升级事件 | P2 §7.2 | approve 支持修改输入参（重算 prompt）；`expire_overdue` 落 `approval.expired` 事件 + inbox 可见 | ⬜ |
| 3.5 | artifacts 备份接入 | P2 §9.3 | `backup.py` 增 artifacts 表 + 文件清单导出 | ⬜ |
| 3.6 | artifacts 保留期清理 | P2 §9.3 | `expires_at` 过期且无引用 → 清理任务删除 | ⬜ |
| 3.7 | `declared_plan` 集成 | P2 §6.4 | step 执行的 todo/plan 记录入 `ai_orchestration_steps`（观测字段） | ⬜ |
| 3.8 | `/v1/ai-orchestrations` 对外契约 | P2 §6.3 | definitions/runs 只读 + create_run（API Key 隔离） | ⬜ |

## 批次 4：前端与管理面（体验层）

| # | 项 | 来源 | 内容 | 状态 |
|---|---|---|---|---|
| 4.1 | 前端批任务 SSE 客户端 | P1 §7.2 | `src/api/batchEvents.ts`（EventSource + Last-Event-ID）接入 AiChatView 替代轮询主通道（轮询留降级）；e2e 冒烟 | ⬜ |
| 4.2 | 管理面面板 | P1 §10 | AiBatchAdmin 抽屉增 Attempt 链 / 投递状态（含重放按钮）/ 预算消耗 | ⬜ |
| 4.3 | 内部 commands 端点 | P1 §8.1 | `POST/GET /ai/chat/batches/<bid>/commands`（幂等，语义同对外） | ⬜ |
| 4.4 | 调度优先级/公平/限流、ETA/成本 | P2 §10 | ⏸ 暂缓（依赖真实多租户负载，登记下一版本） | ⏸ |
| 4.5 | Phase A 投影、Docker/K8s adapter | P2 §11/§8.2 | ⏸ 暂缓（可选/演进项） | ⏸ |
| 4.6 | M7 对外错误全面结构化 | P1 §8.3 | ⏸ 暂缓（涉及全部 /v1 路由重构，单独批次）；本计划批次 1 已覆盖新增端点的结构化错误 | ⏸ |

## 验证要求

- 每批：`cd server && python -m pytest tests/ -q`（后端停止场景）全绿后提交；
- 批次 4 追加 `npx vitest run` 与 `npx playwright test e2e/ai-full`；
- 计划文档随每批更新状态与提交号。
