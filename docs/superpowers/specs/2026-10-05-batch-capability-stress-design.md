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

1. **接线面为 5 处**（实施期核实修正）：`routes/ai_chat.py` 的 create_session、send_message、sse_events + `utils/chat_persist.py` 的监听线程事件源、REST 回填。实施期核实发现监听线程与建会话同样硬编码，漏接任何一处 stub 栈下交互链路断裂（监听器注册但不收事件还会挡住 SSE 兜底落库）。其中 create_session/send_message 与 REST 回填走 `get_runtime().get_client()`，sse_events 与监听事件源走 `get_runtime().subscribe_events(...)`（经门面默认委托）。
2. **门面**（`utils/runtime/base.py`）：`AgentRuntime` 增加 `subscribe_events(directory='', read_timeout=None)`，默认实现委托 `self.get_client().subscribe_events(...)`——OpenCodeLocalRuntime 天然继承（行为 byte 级不变）；StubRuntime 经 StubClient 新总线实现（§4）。
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

### S2 并发终态 × 门禁评估（实测 ~3.5min）

批 50 children，concurrency=10 + delay 8–12s；action_checks：2 file 类（轮询子任务工作区就绪即预置应过）+ 1 tool 类（stub 无工具部分必败，做 failed 侧对照）。
**实施修订**：① 应过侧只用 file 类（db_record 建表不在 migrations，避免无谓 schema 耦合，功能正确性由既有单测覆盖）；② `gate.evaluated` 实落 **`ai_execution_events`**（按 session_id 关联子任务对账，不在 `ai_batch_events`）；③ workspace_path 由 worker 认领时才落库，播种改轮询式；④ 依赖「执行线程池跟随 AI_BATCH_CONCURRENCY」修复（发现 #2，见 §10）——否则并发终态窗物理不成立。

| # | 断言（落地版） |
|---|---|
| S2.1 | 每子任务 3 条期望行 last_status 全部非空（恰好一次终态核对） |
| S2.2 | get_batch_detail 逐子任务 gate_passed==2 / gate_failed==1（DB 对账） |
| S2.3 | `ai_execution_events` 中 gate.evaluated 事件数 == 子任务数（经 session_id 对账） |
| S2.4 | 本轮后端日志增量无 drain 超时、批次可达终态 |
| S2.5 | invariants() 守恒全绿 |

### S3 修正/重试/取消竞态（实测 ~3.5min）

必败 file 期望（不预置）+ 批级 `gate_retry=TRUE` → 门禁 fail → continue 修正轮（stub 重跑仍 fail）→ 预算耗尽 failed；子任务进入 running 后并发施加 cancel。
**实施修订**：成功侧（S3b）的补种触发信号 = **`ai_execution_attempts` 出现 attempt_no≥2 的 continue attempt**——不能用 last_status='failed' 轮询：round-2 认领时的期望重登记（幂等覆盖）会把行重置回 pending，last_status 轮询只留 0.1–1s 窗口（实测必错过）；同时 stub delay 拉宽到 6–9s 给补种留窗。

| # | 断言（落地版） |
|---|---|
| S3.1 | retry_count ≤ auto-retry(2) + gate 预算(1)，不超发 |
| S3.2 | 终态合法唯一（completed/failed/cancelled） |
| S3.3 | 终态批无 pending 残留（修正轮孤儿） |
| S3.4 | 修正成功侧：continue attempt 出现后补写期望文件 → 修正轮 gate 转 passed → completed，retry_count==1 |
| S3.5 | 批次终态守恒 + invariants 全绿 |

### S4 交互收口竞争（实测 ~35s）

20 交互会话并发 + 每会话双 SSE 流（模拟重连期旧流未死形态）+ attach 交互期望；`DB_POOL_MAXCONN=60`（发现 #A：默认池 20 下 20 并发 finalize 突刺池饥饿，账本行丢失、门禁转 inconclusive——本用例需在充分资源下测「双流收口幂等」这一被测属性）。

| # | 断言（落地版） |
|---|---|
| S4.1+S4.3 | 每 oc 会话账本 bash 行数 == 2（双流 × finalize 幂等键兜住，重复落账即红） |
| S4.2 | 交互期望登记恰 n 行且全部已核对（无未核对残留） |

### S5 树作用域聚合正确性（实测 ~25s）

tree 作用域期望 + 总线 `delegate` 委派形状（每会话委派 explorer+writer 各 1 个子代理、各带 2 次 bash）→ `apply_event` 发现子代理 → 子代理动作计入根会话核对；`subagents` 定向过滤混编。
**实施修订**：① 探针（2026-10-05-s5-probe.md）证实事件驱动发现可行，**主路成立、降级预案未启用**；② delay 2–3s 给监听器订阅留必胜窗口（修复 #4：监听器晚订阅错过短回合会永久饿死，见 §10）；③ 断言前容忍 30s 自愈窗口（发现 #B：SSE 收口落账与监听器持久化存在 `agent_tool_calls.subtask_id` FK 竞态，监听器 finalize 幂等补账）。

| # | 断言（落地版） |
|---|---|
| S5.1 | 每根会话 tree-explorer == ('passed', 2)（explorer 的 bash 计入，writer 不参与） |
| S5.2 | 每根会话 tree-exclude-writer == ('failed', 0)（不相关 agent 动作不参与的必败对照） |
| S5.3 | 账本明细对账：每根会话 explorer/writer 各恰 1 个子代理、各恰 2 行 bash |

**S5 降级预案**：探针证实主路可行，未启用（存档于 evidence/2026-10-05-s5-probe.md）。

## 6. 规模、指标与产物

- 标准档总时长目标 **30–40min**（S1–S5 之和 + 栈启动），运行入口 `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress`。
- 指标 dump 落 `docs/ai-testing/evidence/stress/`（沿 `_dump` 惯例）：SSE 帧对账结果/重连补发延迟、drain 时长分布、gate 评估延迟、账本行数、吞吐。
- 跑完自动汇总 md 报告（沿压测栈 report 惯例）。

## 7. 判别力与回归保护（实测登记，2026-10-06）

| 用例 | base 必失败声明 | 验证方式 | 结论 |
|---|---|---|---|
| Task1 事件总线/门面单测 | 是（结构性） | base 无 `subscribe_events` → AttributeError；S0 已对同一接线链路做 live A/B | 结构性成立 |
| S0 交互冒烟 | 是（接线） | `git checkout f041f71~1 -- ai_chat.py chat_persist.py` 实跑 | **实测红**（ConnectionError→500），恢复后绿 |
| S1 帧对账 | 结构性成立 | 现网无帧解析实现可对照（R1 不解析帧） | 结构性成立 |
| S2 并发门禁 | 无产品 diff——首跑即 base | 首跑暴露 MAX_CONCURRENT 缺陷（发现 #2）→ 修复 | **实测压出产品缺陷** |
| S3 预算竞态 | 缺陷注入成本高 | 依赖既有单元验证 + 压测中实证预算语义（retry_count 恰 1/不超发） | 声明放弃独立 base 对照（理由：修复侧闭环 e2e 已有生产验证） |
| S4 账本幂等 | 唯一索引结构性兜底 | 压测中实测：默认池下丢失（发现 #3）而非重复——幂等方向从未被破坏 | 结构性（唯一索引）+ 负载发现 |
| S5 聚合 | 首跑即 base | 首跑暴露 FK 竞态/监听器饿死（发现 #4/#5）→ 修复 | **实测压出保真度缺口** |
| 交互账本 map_part 修复 | 是 | 回归测试走真实 apply_event 累积路径，修复前 commit 实测红（gate failed 0 行） | **实测红→绿** |
| MAX_CONCURRENT 修复 | 是 | 单测 env=7 断言执行器线程数，修复前 commit 实测红（恒 3） | **实测红→绿** |
| stub info.id 修复 | 是 | 形状锁断言 info.id，修复前实车（S3b attempt 24ms 假完成） | **实测压出** |

## 8. 交付物

1. 本 spec；2. writing-plans 实施计划；3. 门面/stub/sse_events 改动 + 形状锁单测；4. `test_capability_stress.py` 六用例（S0–S5，S3 含正反两侧）；5. evidence 报告（含判别力登记表与发现清单）；6. 压测压出的产品缺陷 4 项已按「先固化失败测试（修复前 commit 实测红）再修复」处置（见 §10）。

## 9. 不做（YAGNI）

- 真模型门禁链路压测（烧 token；行为正确性由 e2e 域承担）；
- 数小时 soak 耐久；容量梯档升级（更高并发阶梯另立）；
- 前端 SSE 消费端（batchEvents.ts store）UI 压测——以 API 层为准；
- 压测平台化（时序库/仪表盘）；
- ai_chat.py 辅助端点（providers/abort/summarize 等）保持硬编码 `OpenCodeClient(OPENCODE_BASE_URL)` 不接入门面——压测套件不触达，stub 栈下不可用属已知面（AI_STUB_ALLOW 防呆挡误配生产）。

## 10. 实施压出的产品修复与发现（2026-10-05/06）

**已修复 4 项**（均按「先固化失败测试、修复前 commit 实测红」处置）：

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | 交互路径动作账本恒 0 落账、交互工具型门禁恒 failed | `record_state` 走 `extract_from_parts` 只认原始 'tool' 形状；交互累积态（apply_event）存的是 map_part 映射后 'tool_use'（无 id 字段，id 在 dict key 上）——既有单测喂手工原始形状掩盖 | `extract_from_part_map` 双形状兼容（41271d3） |
| 2 | `AI_BATCH_CONCURRENCY` 只放大认领数，执行线程池恒 3（硬编码类属性）——「并发终态窗」物理不成立；**容量阶梯"无拐点"由此而来，该结论需重审** | `_executor = ThreadPoolExecutor(max_workers=3)` 不读 env | `_resolve_max_concurrent()` 跟随同一 env，缺省仍 3（a49e8b0） |
| 3 | gate-retry 修正轮 24ms 即时假完成（stub 栈） | StubClient REST 消息 info 缺 `id`——worker continue 基线快照（_snapshot_assistant_ids）恒空集，旧终态消息被判为本轮完成 | stub 补 info.id + 形状锁断言（2d61733） |
| 4 | 监听器晚订阅错过整个短回合后永久饿死（stub 栈）——无人持久化/收口 | stub 总线无视 read_timeout，饿死监听器永不退出 | stub 落实 read_timeout 不活动超时（对齐 OpenCode /event）（d0af9e1） |

**登记待决 3 项**（未修，属容量/健壮性权衡或低概率边界）：

- **#A 池饥饿（S4）**：默认 `DB_POOL_MAXCONN=20` 下 20 会话并发 finalize 突刺（+40 SSE 流落库）→ record_state 拿不到连接 → 账本行丢失、门禁转 inconclusive。方向 fail-closed（安全），但交互门禁在高并发下不可靠。建议：交互收口对 PoolError 短重试，或生产调大 DB_POOL_MAXCONN（env 已可配，f00284a）。
- **#B FK 竞态自愈窗口（S5）**：SSE 收口的 record_state 子代理行在 `ai_chat_subtasks` 落行前插入即 FK 失败 → 本轮 inconclusive，由监听器 finalize 幂等补账自愈；自愈不达时本回期望行停留 pending。良性（无错误数据），但交互门禁结果可能延迟一轮。
- **#C SSE 兜底持久化被 has_listener 单边压制**：监听器已注册即跳过 SSE 兜底 persist_turn——监听器若饿死（#4 场景，生产中等价于「晚订阅错过超短回合」），本回合消息无人持久化。建议：兜底 persist 改为「监听器已收口才跳过」或对 idle 后 N 秒无落库做补偿。
