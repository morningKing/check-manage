# 批任务 E2E 测试体系重设计 设计

## 1. 背景与目标

批任务（1 Prompt × N 文件 → N 个隔离 AI 子会话）的功能面横跨内部 API（`/ai/chat/batches`，约 25 端点）、对外 OpenAPI（`/v1/ai-batches`）、管理 API、MCP 工具与前端四大组件。现有 e2e 约 17 个 spec 分散在 `e2e/` 根目录与 `e2e/ai-full/`，存在三类病灶：

- **重复**：建批对话框 UI 流程复制 6+ 份；`waitBatchTerminal` 4 份实现；staging 上传与 adminToken 各有双实现（`helpers.ts` 走 vite 代理 vs `batch-helpers.ts` 直连 3002）。
- **过时/脆弱**：`ai-chat-batch.spec.ts` 标题含 "retry" 却无 retry 步骤；`ai-chat-batch-search.spec.ts` 依赖环境残留批任务否则 skip；`ai-chat-subtask-trace.spec.ts` 含分支漂移注释与占位级用例。
- **缺口**：管理页交互、配置编辑生效、建批校验负路径、SSE 实时事件 UI 消费、worker 容灾自愈、多用户权限隔离、partial 批混合重试、结果下载 UI、对外 webhook 回调，e2e 全为零。

目标：按功能域重组为单一 `e2e/ai-full/batch/` 套件，收敛 helper，修掉病灶，补齐缺口；**确定性构造优先**——系统栈保持真实（Playwright + 后端 + OpenCode 真链路 + 真库），但不烧 LLM 能验证的功能点一律用确定性构造，真 LLM 只留给「模型行为本身是验证目标」的用例。

## 2. 总体架构：域目录 + 三档断言策略

```
e2e/ai-full/batch/
  toolbox.ts               # 确定性构造工具箱（新）
  batch-helpers.ts         # API helper 权威版（自 e2e/ai-full/ 迁入，唯一实现）
  ui-helpers.ts            # 建批对话框 / 批组操作等 UI 流程封装（新）
  lifecycle.spec.ts        # 生命周期：创建/详情/编辑/追加/删除治理/事件增量
  control.spec.ts          # 控制面：pause/resume/cancel/单子控制/命令幂等
  retry-reexecute.spec.ts  # retry-failed / reexecute / continue / partial 混合重试
  gate.spec.ts             # 门禁：期望核对/verifier/委派级/gate_retry/dry-run/attach
  reuse.spec.ts            # 子代理会话复用 + 并发拉满与排队观测
  openapi.spec.ts          # 对外 API：鉴权/隔离/契约/webhook 回调
  admin.spec.ts            # 管理页交互闭环（新）
  resilience.spec.ts       # 容灾自愈：对账矩阵 DB 种子 + 进程级（新）
  permissions.spec.ts      # 多用户与权限隔离（新）
  ui-journeys.spec.ts      # 用户旅程（收编根目录批 spec + SSE 实时消费）
```

每域内三档断言策略，**真 LLM 用例每域最多 1-2 个**（打 `@llm` 标签）：

- **L1 API 契约**：确定性构造驱动，断言 detail JSON / DB / 文件系统 / 端点错误码。占大头。
- **L2 UI 交互**：API 或 fs 造好前置状态，UI 只验展示与操作生效（徽标、按钮、列表行、抽屉）。
- **L3 端到端语义**：真 LLM，仅用于模型行为是验证目标的用例——复用委派、门禁达标、技能注入、委派轨迹气泡、停止续跑消息保留。

非目标：不改任何产品代码；不动 `server/tests/test_batch_*.py` 路由级 pytest 与 MCP 函数级测试（它们与 e2e 分层互补，spec 中注明互引关系即可）。

## 3. 确定性工具箱（toolbox.ts）

| 工具 | 契约 | 服务域 |
|---|---|---|
| `failFastBatch(opts)` | 未知 agent 建批 → 子任务秒级确定性 failed（`_check_agent` fail-fast，不烧 LLM），返回批 id | retry / gate 负向 / partial / permissions |
| `sleepBatch(n, files, prompt?)` | bash `sleep` 长任务构造稳定 running 窗口（沿用 `ai-batch-control` 的构造，收编） | control / resilience / ui-journeys |
| `provisionRepo(agents)` | 临时 git 仓注入自定义 primary/subagent agent 定义（收编 `batch-helpers.makeProvisionRepo`） | gate / reuse |
| `workspaceIO(token, bid, sid)` | fs 直读直写子任务工作区（沿用 `ai-retry-failed` 的 `workspace_path` 手法） | retry / admin / openapi 文件面 |
| `secondUser()` | 注册非 admin 测试账号并返回 token（AITEST- 前缀防撞名，用后清理） | permissions |
| `dbSeed(fn)` | psycopg 直连共享开发库执行种子/清理 SQL（复用 `mcp-server/tests` 的连接模式） | resilience / admin 数据构造 |
| `restartBackend()` | kill + 重启后端进程（后端不 auto-reload，重启才生效；复用现有重启脚本路径） | resilience 进程级 |
| `waitTerminal` / `countByStatus` 等 | 唯一权威轮询与计数工具 | 全部 |

关键取舍：

- **对账器矩阵用 DB 种子而非真杀 worker**——`_reconcile_stale_running` 的输入是 DB 行（租约过期、checkpoint、副作用、OpenCode 会话号），逐分支种行即可廉价确定性测；真进程级用例（租约接管、重启自愈）控制在 1-2 个并打 `@resilience` 标签。
- **看门狗类用例走已支持的 env**：`AI_BATCH_TOOL_STALL_SEC`（默认 900）缩短后配 `sleepBatch` 长任务，确定性触发「工具卡死→可重试失败→自动重试链」；`AI_BATCH_SESSION_TIMEOUT_SEC`（默认 0）缩短后触发「硬超时→不可重试直接 failed」分支。两者均需重启后端生效，用后恢复默认重启。注意 `STALL_TIMEOUT_SEC=180`（无产出停滞）是硬编码常量不可配，其自动重试链路由工具卡死用例代为覆盖（共用 `_maybe_auto_retry`），真「无产出停滞」分支留后续迭代。
- 全部构造函数自带清理路径（删批、删文件、删账号），沿用现有 `cleanupBatch(?stop=1)` 语义。

## 4. 套件设计（逐域用例清单）

### 4.1 lifecycle.spec.ts（收编 `ai-batch-lifecycle` + 补缺）

收编：staging 上传→建批→详情 sessions 对齐→子会话消息落库→终态删除级联 404；删除治理（非终态 409 / `?stop=1` 有界 drain 409 重试 / 终态删除）。

新增（全部 L1）：

- `PATCH /<id>` 配置编辑生效：改 prompt / agent / 门禁期望后对重试与重执行生效，未终态子任务期望被 `sync_batch_expectations` 同步（以重试后子任务行为或 detail 回读为断言）。
- `POST /<id>/append`：追加文件产生新 pending 子任务并被派发收敛。
- 建批校验负路径：0 文件、空 prompt、staging 路径穿越、超过 maxSessions 上限 → 各自 4xx 与错误码。
- `GET /<id>/events?afterSeq=` 增量语义（自 `ai-harness-safety` 收编），含 `CURSOR_EXPIRED` 410 保护。

### 4.2 control.spec.ts（收编 `ai-batch-control` + `ai-chat-stop-resume` API 面 + 补缺）

收编：批级 pause→收敛 paused（不占 failed）→单子 resume 独立续跑→批级 resume；批级 cancel→cancelled 记入 failed 聚合→批 failed；单子 cancel→partial；命令平面 `/commands` 幂等键语义（自 `ai-harness-safety` 收编）。

新增（L1）：pause 后 cancel 的状态迁移；paused 批上 retry-failed 的行为契约；重复 resume / 重复 cancel 的幂等；已终态批上 pause/cancel 的拒绝码。

### 4.3 retry-reexecute.spec.ts（收编 `ai-retry-failed` + `ai-reexecute` + 补缺）

收编：fail-fast 重试全语义（retried 计数、工作区清空残留清零、uploads 输入保留、计数回滚、消息跨轮不累积）；单子 reexecute 同语义族。

新增（L1，`failFastBatch` 构造）：

- partial 批混合重试：completed + failed 混合批上 retry-failed 后，completed 子任务原样保留（消息与状态不动）、failed 恰好重排。
- gate-failed 子任务（门禁未过落 failed）参与 retry-failed 的计数与重排。
- continue 通道：终态子会话 `/continue` 202 → 历史上下文保留（自 `ai-harness-safety` 第 2 用例收编为确定性断言）。

### 4.4 gate.spec.ts（收编 verifier/delegation 三 spec 的 API 面 + 补缺）

收编：verifier 三向（达标 completed / 不达标 failed 且 error 带 action_gate 明细 / gate_retry 闭环第二轮补写文件 completed 且 `/attempts` 出 GATE_RETRY）；verifier 定向 subagent 组；委派级门禁提前判定与「不过即停」。

新增：`gate/dry-run` 契约（正则预演命中数；verifier 类型明确不可预演的错误面）；`action-checks/attach` 运行中补挂期望后该子任务终态被核对；`children/<sid>/tool-calls` 账本取材端点。正向达标用例保留 `@llm`（rubric 可满足设计保证方向确定性）。

### 4.5 reuse.spec.ts（`ai-subagent-reuse` + `ai-batch-concurrency-reuse` 去重合并）

现状两 spec 断言核心重复约七成，合并为：一个 L1 用例管权威锚点（`/reuse` 端点 + tool-calls 账本 + provision 自定义 agent + 复用违规事件）；一个 L3 用例管 UI（气泡 subtaskId 一致、「已复用·N 段」徽标）。并发拉满与 pending 排队观测（MAX_CONCURRENT=3、running 峰值===3）保留在本域。

### 4.6 openapi.spec.ts（收编 `ai-openapi` 批部分 + `ai-harness-safety` 对外面 + 补缺）

收编：无 Key/假 Key/不存在资源 404 面、staging 路径穿越防护、X-API-Key 建批→终态→`/results`。

新增：跨 API Key 隔离（Key A 的批对 Key B 不可见/404）；终态 webhook HMAC 签名回调真实送达（Playwright 起 localhost receiver 接收，校验签名与载荷；覆盖成功与密钥不符两种投递）；`file-records` / files download 契约。

### 4.7 admin.spec.ts（全新，管理页闭环，全确定性）

- API 面：跨用户列表筛选（status/owner/source/keyword）、详情、子任务消息（最近 500）、tool-calls、attempt-timeline、非 admin 403（权限面归 permissions 域）。
- UI 面（L2）：`/admin/ai-execution?tab=batches` 列表 → 筛选 → 详情抽屉 → 子任务消息/工具调用/attempt 时间线弹窗 → 「重试全部失败」与单子「重跑」真实生效（状态与计数变化）→ 终态软删后消失。
- 文件面：AdminBatchFiles 产出文件分组（uploads/outputs/workspace）→ 预览/下载 → 勾选导入 data_files（归属=原属主）→ 数据文件页可见。`workspaceIO` + `failFastBatch` 构造数据，全程不烧 LLM。

### 4.8 resilience.spec.ts（全新，容灾自愈）

- 对账矩阵 DB 种子用例（`dbSeed` 直种孤儿 running 行 + 对应批/子会话/租约/checkpoint/副作用组合，触发对账后断言决策）：租约未过期不动；unknown 副作用→needs_review（禁自动重放）；OpenCode 会话 404→failed（可重试）；有 checkpoint→原地 continue 续跑；无 checkpoint 无消息无副作用→failed 可重试；无 oc 会话号→原样重排。每分支一个用例，发 `child.recovered` 事件为辅助断言。
- 停滞超时自动重试：缩短 `AI_BATCH_TOOL_STALL_SEC` 重启后端 + `sleepBatch`，断言自动重试链（attempt `recovering`、AUTO_RETRY_CONTINUE_PROMPT 原会话续跑、`AI_BATCH_MAX_AUTO_RETRY` 预算用尽才终局）；另一用例缩短 `AI_BATCH_SESSION_TIMEOUT_SEC` 断言硬超时不可重试直接 failed。
- 进程级（`@resilience`）：worker 运行中重启后端 → 新进程租约接管 → 批继续收敛；不产生双重执行（fencing 断言：旧 generation 写入被拒）。

### 4.9 permissions.spec.ts（全新，多用户隔离）

`secondUser()` 注册普通用户乙，用户甲建批后：乙的列表不可见、详情 404（不泄漏存在性）、控制面 pause/cancel/retry 403、删除 403；乙访问 admin 端点 403；对外 API Key 维度隔离与 4.6 互引；MCP 归属校验已有函数级测试，e2e 不重复。

### 4.10 ui-journeys.spec.ts（收编根目录批 spec 存活价值 + 补缺）

收编并修病灶：

- 对话框建批端到端（`@llm`）：建批→批组→子会话→终态（自 `ai-chat-batch`，删除虚构的 retry 步骤）。
- 表单校验阻断（L2，不烧 LLM）：空 prompt / 无文件 / 门禁期望不完整时提交被阻断（自 `agent-action-gate` 用例 1 收编）。
- 批组按钮面（L2）：`sleepBatch` 驱动 paused/cancelled/failed 态，断言暂停/继续/重试失败/删除按钮的显隐与生效。
- 批内搜索：**自建批**消除环境耦合，断言范围标签切换、命中标注、空态文案、命中跳转打开子会话（自 `ai-chat-batch-search`）。
- 技能注入（`@llm`，自 `batch-skill-check`）：改为经 `provisionRepo` 自带 skill 构造，消除对磁盘预置技能的环境依赖。
- 委派轨迹气泡（`@llm`，自 `ai-chat-subtask-trace` 用例 3）：去掉占位用例与分支漂移断言。
- 停止/续跑消息保留（`@llm`，自 `ai-chat-stop-resume` 用例 1/2）：停止前消息 id 在续跑后原样保留——批任务「原工作继续」核心语义，保留真 LLM。
- SSE 实时消费（L1+L2，新）：订阅批后由 API 触发 `child.status` 事件，断言 UI 状态更新先于列表轮询周期（证明 SSE 生效而非轮询兜底）。

### 4.11 旧文件处置

- 删除：`e2e/ai-chat-batch.spec.ts`、`e2e/ai-chat-batch-search.spec.ts`、`e2e/batch-skill-check.spec.ts`。
- 改造：`e2e/ai-chat-stop-resume.spec.ts`、`e2e/ai-chat-subtask-trace.spec.ts` 的批用例移入 batch/，原文件改为纯普通会话域并更名（如 `ai-chat-session-control.spec.ts` / `ai-chat-subtask-trace.spec.ts` 保留非批用例）。
- 改造：`e2e/ai-full/ai-harness-safety.spec.ts` 的批相关用例（对外门禁、events、命令幂等、drain 删除、continue）收编入对应域后，原文件保留 P2 编排 DAG 等非批用例；`ai-openapi.spec.ts` 仅批相关用例收编入 batch/openapi，其余保留；`agent-action-gate.spec.ts` 整体收编（门禁配置 UI 区块→ui-journeys，门禁徽标与 db_record 效果→gate 域）后删除。
- `e2e/ai-full/` 上层被收编的批 spec 文件删除；`helpers.ts` 中批任务相关函数（stagingUpload、waitBatchTerminal、BATCH_TERMINAL 等）标注 deprecated 并注明 batch-helpers 替代，待消费方（ai-harness-safety 残留用例、ai-openapi 残留用例）迁移完毕后删除。

## 5. 运行编排与证据规范

- 单入口不变：`npx playwright test`（workers=1、共享 admin 会话约束沿用）。
- 用例标签：`@llm`（烧模型）/ `@deterministic`（默认无标签）/ `@resilience`（进程级，串行）。
- 时长预算：确定性用例 ≤60s/例；真 LLM 用例 ≤10 分钟（并发拉满观测、复用 UI 两个重用例放宽至 15 分钟）；全套件目标 ≤45 分钟。
- 证据规范沿用：截图落 `e2e/screenshots/ai-full/batch/`；关键断言（状态迁移、计数、回调载荷）附响应摘录落盘，可复核。
- 更新 `docs/ai-testing/` 回归入口文档与套件计数。

## 6. 实施顺序

1. **骨架**：toolbox + helper 收敛 + 目录迁移（纯搬迁不改行为，旧用例在新结构跑通）。
2. **收编**：lifecycle / control / retry-reexecute / reuse / gate / openapi 六域迁移与去重。
3. **新域**：permissions → admin → lifecycle/control/retry 补缺打包。
4. **resilience**：DB 种子矩阵 → 停滞超时 → 进程级。
5. **ui-journeys**：收编 + SSE 新旅程。
6. **收尾**：删旧文件、`helpers.ts` deprecation 注释、文档更新。

每步收尾跑一次 ai-full 全量回归防退化；新增用例遵循先失败后通过的核对纪律（构造手段先验证能稳定触发目标状态）。

## 7. 后续迭代（本期不做）

- MCP batch 工具的 MCP 协议层 e2e（现函数级测试已覆盖归属校验与筛选语义）。
- worker 双实例租约竞争（多进程部署面）。
- 超大文件 / 多文件上限的性能面（路由级已有 limit 测试）。
- 真正的「无产出停滞」分支 e2e（`STALL_TIMEOUT_SEC=180` 为硬编码常量，需代码提供可注入 seam 或真模型停滞，本期由工具卡死用例代为覆盖共用重试链）。
- 无人值守 question 防线第二层（系统自动拒绝）的 e2e 确定性触发（已文档化由 pytest 承担）。
- 管理页 deliveries 重放（replay）的 UI 深链（API 契约可先在本期 openapi/admin 域顺带覆盖，UI 深链留后续）。
