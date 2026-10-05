# AI 批任务能力稳定性压测 设计（子代理 SSE × 门禁并发）

> 日期：2026-10-05 ｜ 状态：**待用户评审**（范围决策已确认：门禁覆盖全四项、规模标准档；SSE 路径推荐方案 A，见 §2）
> 上游：`2026-10-03-ai-stress-testing-design.md`（压测栈已建成，本设计是其能力域扩展，不另起炉灶）

## 0. 前置事实（均已核实，file:line 钉提交时以 HEAD 为准）

- 压测栈已存在：`server/tests/stress/`（专属库 casemanage_stress、压测后端 :3092 `AI_AGENT_RUNTIME=stub` + `AI_STUB_ALLOW=1`、混沌 serve :4097、采样器、invariants() 守恒检查）。本设计全部复用，零新增栈。
- **两条 SSE 端点**：
  - 批次级 `GET /ai/chat/batches/events`（`routes/ai_chat_batches.py:810` batch_events_sse）——读 `ai_batch_events` 表按游标补发，15s tick，30min 上限，Last-Event-ID 形如 `<batchId>:<seq>`，PoolError 降级（流内 `: pool-busy` 帧/建连期 503+Retry-After）。
  - 会话级 `GET /ai/chat/sessions/<sid>/events`（`routes/ai_chat.py:1379` sse_events）——订阅上游事件流，`apply_event` 做子代理路由，idle/error 时 `persist_turn` + `finalize_interactive_turn`（`utils/agent_ledger.py:219`，账本落库 + 交互门禁核对）。
- **基础设施缺口**：`sse_events` 硬编码 `OpenCodeClient(OPENCODE_BASE_URL)`；`AgentRuntime` 门面（`utils/runtime/base.py`）无 `subscribe_events`；StubRuntime/StubClient（`utils/runtime/stub.py`）无事件源 → **stub 栈下会话级 SSE 一连上游即断**，从未被压过。原 2026-10-03 spec §4.3 R1 本就计划压会话消息流（`ai_chat.py:1457`），实施时因上述缺口收窄为仅批次流——本设计补上这块欠账。
- **门禁调用链（批侧）**：worker 持久化 → `wait_subtasks_drained`（`utils/batch_engine.py:1551`，批 120s/交互 30s 轮询子代理收敛）→ `check_session_gate`（`utils/agent_ledger.py:646`）→ `gate.evaluated` 审计事件（`utils/batch_engine.py:1582`，批上下文逐子任务一条）→ 门禁不过且开关开 → `_maybe_gate_retry`（`utils/batch_engine.py:1853`，原会话 continue 续跑修正；预算 = 批级 `gate_retry` 列 > 全局 env `AI_BATCH_GATE_RETRY` 默认 0；与 auto-retry 共用 `retry_count`，上限 `AI_BATCH_MAX_AUTO_RETRY` 默认 2，batch_engine.py:758）。
- 期望登记入口：批定义/模板 `action_checks`、管理员 attach、MCP；tree 作用域把子代理动作计入根会话核对；定向能力 `subagents`（按 agent 过滤账本）与 `apply_to`（batch_seq/输入 glob，不匹配不登记）。

## 1. 背景与目标

用户命题：**设计并测试 AI 批任务功能的稳定性——并发下的子代理 SSE、门禁等能力**。

### 1.1 既有压测正确性核对结论（2026-10-05 逐套件过断言，用户点名要求）

| 套件/场景 | 验证了什么 | 正确性验证现状 |
|---|---|---|
| R1 批次 SSE 50 连接 5min | 不断流（ChunkedEncodingError 按批次终态分类）、批次终态、zombie==0 | **零帧解析**：eventId/eventSeq 连续性、Last-Event-ID 重连补发、与 `ai_batch_events` 表对账全未验证 |
| R2 事件分页 / R3 管理列表 | 延迟分位数 | 无内容正确性 |
| R4 outbox 洪峰 3k | deliveries==3000 精确 + outbox 残留==0 | ✅ 真判别力（精确对账先例） |
| C1–C4 混沌 | 非僵尸失败、fencing 不回退、双重执行 dup==[]、退避/dead_letter、409 守卫 | ✅ 真判别力（先例） |
| 容量阶梯 | 计数守恒 + 成功率 + cpm | 过程性正确（不丢子任务），非能力正确性 |
| **门禁** | —— | **四套件零覆盖（grep 实证）** |
| **会话级 SSE / 交互收口** | —— | **零覆盖且 stub 下不可用** |

### 1.2 目标

1. 会话级（子代理）SSE 在**并发 + 内容正确性**两个维度首次获得压测覆盖（补基础设施后）；
2. 批次级 SSE 从「连接稳定」升级到「帧级正确性」（对账/重连补发）；
3. 门禁四条并发风险线（并发终态评估 / 修正-重试-取消竞态 / 交互收口竞争 / 树作用域聚合）获得系统级稳定性验证；
4. 全程 stub 层 0 token（与 2026-10-03 方案的 stub 优先原则一致）。

## 2. SSE 路径决策：方案 A（Stub 事件总线 + sse_events runtime 接线）

三选一，推荐 A：

- **A（采用）**：runtime 门面补 `subscribe_events` 抽象 + StubClient 内存事件总线 + `sse_events` 改走 `get_runtime()`。一次小面产品改动换取「SSE→路由→落库→账本→门禁」全链路 0 token 可压。
- B（否）：独立假事件总线 serve。零产品改动，但 stub 会话 oc id 与假总线事件难对齐，只能压连接保持——验证不了本次目标里的路由与收口正确性。
- C（否）：真 serve 少量真模型用例。烧 token + 时序 flaky，只适合行为类验证（已有 e2e 域承担），不适合并发压载主路径。

## 3. 产品侧改动（最小面）

1. **门面**（`utils/runtime/base.py`）：`AgentRuntime` 增加 `subscribe_events(directory='', read_timeout=None)`，默认实现委托 `self.get_client().subscribe_events(...)`——OpenCodeLocalRuntime 天然继承（其 `get_client()` 即 OpenCodeClient，`utils/opencode_client.py:317` 已有该方法，行为 byte 级不变）；StubRuntime 经 StubClient 新总线实现。
2. **sse_events**（`routes/ai_chat.py:1379`）：`OpenCodeClient(OPENCODE_BASE_URL)` → `get_runtime()`，仅此一处替换，生成器内部逻辑不动。
3. **StubClient 事件总线**（§4）。
4. **回归保护**：① 单测锁 stub 事件形状（对齐 `opencode_client.subscribe_events` 真实产出的 `{"event": <type>, "data": {type, properties}}` 形状，沿 StubRuntime 形状锁先例）；② 该单测在 base（硬编码 OpenCodeClient、无门面方法）上必须失败——A/B 实跑记录；③ ai-full 会话域真链路 e2e 复跑，确认生产路径无行为漂移。

## 4. Stub 事件总线设计（utils/runtime/stub.py）

- 完成器线程现有生命周期（延迟→落完成）扩展为**按时间线发件**：派发后 delay 窗口内依序发 `message.part.updated`（assistant 文本部分全量快照，part id 稳定）→（profile 开启时）工具部分快照 → 终态时 `session.idle` 或 `session.error`。事件按 (directory, sessionID) 入队，`subscribe_events(directory)` 只吐本 directory 事件——对齐 OpenCode per-directory 流语义。
- profile 新增可选键：`tool_parts`（注入 N 个工具调用部分形状，供账本/门禁承压）、`delegate`（发 task 委派形状，供树作用域场景）、`event_jitter_ms`（发件间隔抖动，制造乱序到达压力面）。
- 事件先入队后改内存消息态——保证 SSE 流观察到的演进与 `get_messages` 轮询结果一致（收口双路径：SSE finalize 与 worker 持久化消费同一事实）。
- 交互会话与批子会话都可发件；批子会话 idle 不触发 persist（`sse_events` 现有守卫保持，worker 是唯一写者）。

## 5. 套件设计：`server/tests/stress/test_capability_stress.py`

pytest.mark.stress；数据 `STRESS-` 前缀；模块入口 `restart_backend` 复位 stub（沿 readpath 惯例防 chaos 泄漏）。**断言全部遵循「能在缺陷实现上失败」**（见 §7）。

### S1 SSE 并发正确性（~8min）

批次级（50 观察者并发持流 5min，对齐 R1 量级）+ 会话级（10 交互会话各 1 流）：

| # | 断言 | 判别对象 |
|---|---|---|
| S1.1 | 逐帧解析 `id: <bid>:<seq>`：seq 严格递增、无缺口无重复，流见集合与 `ai_batch_events` 表该批 event_seq 全集对账 | 游标推进/补发逻辑 |
| S1.2 | 1/3 观察者随机时刻断开、带 Last-Event-ID 重连：重连首帧 seq == 断点+1，补发区间不重不漏 | 断线补发 |
| S1.3 | 批次终态 → `batch_done` 帧后服务端关流（ChunkedEncodingError 按终态分类，沿 R1 惯例） | 关流语义 |
| S1.4 | 会话流：帧序 == 总线发件序（不发不丢不错序）；idle 后 `ai_chat_messages` 落库内容与 parts 快照一致 | apply_event 路由 + persist_turn 承压 |
| S1.5 | `: pool-busy` 降级帧出现时游标不动、后续帧无丢失（建连洪峰触发，观察性用例不强制每次复现） | PoolError 降级语义 |

### S2 并发终态 × 门禁评估（~8min）

批 50 children，`AI_STUB_PROFILE` delay 收窄制造 ±2s 并发终态窗；action_checks 混编：file 类（workspaceIO 预置应过）+ db_record 类（dbSeed 预置应过）+ tool 类（stub 无工具部分必败，做 failed 侧对照）：

| # | 断言 |
|---|---|
| S2.1 | 每条 action_expectations 行恰好一次终态核对（无 pending 残留、无重复评估） |
| S2.2 | get_batch_detail 逐子任务 gate_passed/gate_failed == 该子任务期望行计数（DB 对账） |
| S2.3 | `gate.evaluated` 事件与核对一一对应可对账（审计链完整） |
| S2.4 | `wait_subtasks_drained` 高峰无超时泄漏日志、无死锁（批次可达终态） |
| S2.5 | invariants() 守恒全绿 |

### S3 修正/重试/取消竞态（~10min）

必败 file 期望（不预置）+ 批级 `gate_retry=TRUE` → 门禁 fail → continue 修正轮（stub 重跑仍 fail）→ 预算耗尽 failed；同时间窗并发施加 cancel：

| # | 断言 |
|---|---|
| S3.1 | retry_count ≤ `AI_BATCH_MAX_AUTO_RETRY`(2) + gate retry 预算语义，不超发 |
| S3.2 | 终态唯一（attempt 谓词），cancel 与 continue 竞态不产生双写/复活 |
| S3.3 | 无孤儿修正轮（stub dispatch 记录 vs 子任务终态时序对账：终态后无新派发） |
| S3.4 | 修正成功侧（小用例）：运行中经 workspaceIO 补写期望文件 → 修正轮 gate 转 passed → completed，retry_count==1 |
| S3.5 | 批次终态守恒 + invariants 全绿 |

### S4 交互收口竞争（~8min）

20 交互会话并发（send → 挂 SSE → 总线发 `tool_parts` + idle 并发收口）：

| # | 断言 |
|---|---|
| S4.1 | agent_tool_calls 按 (oc_session_id, part_id) 幂等：无重复行，行数 == 总线发出工具部分数 |
| S4.2 | attach 登记的交互期望每条恰好一次核对结果 |
| S4.3 | 同会话双 SSE 流并发（模拟重连期旧流未死）→ 账本仍恰好一份 |

### S5 树作用域聚合正确性（~5min）

tree 作用域期望 + 总线 `delegate` 委派形状 → `apply_event` 发现子代理 → 子代理动作计入根会话核对；`subagents` 定向与 `apply_to` 过滤混编：

| # | 断言 |
|---|---|
| S5.1 | 根会话核对结果 == 账本明细按 agent 过滤聚合（DB 级对账） |
| S5.2 | 不相关 agent 动作不参与核对；apply_to 不匹配的子任务不登记期望 |

**S5 降级预案（预声明）**：若委派形状过不了 `apply_event` 发现逻辑（形状过度敏感），改为直接种 `ai_chat_subtasks` + 账本行，只验核对聚合 SQL 语义；实施第一步先做形状探针再定。

## 6. 规模、指标与产物

- 标准档总时长目标 **30–40min**（S1–S5 之和 + 栈启动），运行入口 `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress`。
- 指标 dump 落 `docs/ai-testing/evidence/stress/`（沿 `_dump` 惯例）：SSE 帧对账结果/重连补发延迟、drain 时长分布、gate 评估延迟、账本行数、吞吐。
- 跑完自动汇总 md 报告（沿压测栈 report 惯例）。

## 7. 判别力与回归保护

- S1.1/S1.2 帧对账断言在现有 R1 实现上**必然失败**（现实现不解析帧）——天然 base 必失败；
- §3.4 门面/sse_events 接线的单测在 base（硬编码 OpenCodeClient）上必须失败——A/B 实跑记录；
- 门禁场景（S2–S5）并发断言**不预声明** base 必失败，实施时逐例 `git show base` A/B 实跑后填写（沿 12 号报告铁律：判别力声明必须实测，不得推断）。

## 8. 交付物

1. 本 spec；2. writing-plans 实施计划；3. 门面/stub/sse_events 改动 + 形状锁单测；4. `test_capability_stress.py` 五场景；5. evidence 报告；6. 若压出产品缺陷：按惯例先在修复前 commit 上固化失败测试，再修复，独立核对。

## 9. 不做（YAGNI）

- 真模型门禁链路压测（烧 token；行为正确性由 e2e 域承担）；
- 数小时 soak 耐久；容量梯档升级（更高并发阶梯另立）；
- 前端 SSE 消费端（batchEvents.ts store）UI 压测——以 API 层为准；
- 压测平台化（时序库/仪表盘）。
