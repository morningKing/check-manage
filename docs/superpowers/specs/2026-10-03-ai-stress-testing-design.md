# AI 压力测试方案 设计

> 日期：2026-10-03 ｜ 状态：已评审（用户确认方案 A 与 §1–§6 设计）
> 前置事实（已核实）：worker 经 `get_runtime().get_client()` 消费客户端（`batch_engine._client()`，`utils/runtime/__init__.py` 的 `AI_AGENT_RUNTIME` 可切换但当前仅 `opencode_local`）；worker 判完成依赖 `get_messages` 返回的 OpenCode 原始消息形状（`info.role/finish/time.completed`）；`/metrics`（`routes/metrics.py`）已有 7 个 gauge（queue_depth/running/failed_last_hour/orchestration_runs_active/attempts_running/outbox_pending/needs_review）；`init_db` 按领域拆分可重复建库；端口经 `FLASK_PORT` 注入；批量 worker 租约按 DB 键互斥——**压测必须用专属 DB**，否则 dev 后端 worker 会抢走压测批次用真模型执行。

## 1. 背景与目标

存量测试无任何负载/压力覆盖（02 号用例仅 TC-PERF-001/002 单测提速与 TC-PERF-003 单条长任务）。本方案回答三类问题：

1. **容量上限探索**：阶梯加压找 Harness 稳住的最大并发子任务数与拐点行为；
2. **混沌/故障注入**：验证 P0/P1 安全机制（租约/fencing/CAS/效果账本/outbox）在真实故障下兜得住；
3. **读写路径压载**：SSE 连接数、事件风暴分页、大列表、outbox 洪峰排空。

明确不做：数小时级 soak 耐久测试（未选）；压测平台化（时序库/仪表盘，无历史基线前 YAGNI）。

## 2. 总体架构：专属压测栈 + 两层负载

```
压测编排器（server/tests/stress/conftest.py）
 ├─ 建/初始化专属 DB casemanage_stress（复用 init_db）→ 测试结束 DROP（finalizer 保底）
 ├─ 启动压测后端（FLASK_PORT=3092，AI_AGENT_RUNTIME=stub，
 │   AI_BATCH_CONCURRENCY 按阶梯注入；AI_STUB_PROFILE 注入行为面）
 ├─ 混沌层追加：专属 serve（端口 4097 + 临时 GLOBAL_DIR，按 fe21209 隔离要求）
 │   + 后端#2（AI_AGENT_RUNTIME=opencode_local，OPENCODE_BASE_URL=http://127.0.0.1:4097）
 ├─ 采样器线程：2s 周期抓 /metrics + DB 不变量查询 + 进程 RSS（psutil）
 └─ 结束钩子：DROP DB、终止全部子进程（kill 进程树）
```

与 dev 环境零共享：不同库、不同端口、不同 serve、不同 GLOBAL_DIR——kill/重启只伤压测自己的实例。容量层全程 stub（不出网、零 token）；混沌层用真 serve 小规模验证进程语义。服务启动顺序：Postgres（共用实例、独立 DB）→ 压测后端 →（混沌层）专属 serve。

## 3. StubRuntime 设计

`server/utils/runtime/stub.py`：

- `StubRuntime(AgentRuntime)`：实现 7 个抽象方法（capabilities/create_session/dispatch/list_messages/get_messages/abort/health）+ `get_client()` 返回 `StubClient`（`batch_engine._client()` 消费的就是 get_client）。
- `StubClient` 内存态模拟 agent 生命周期（单 worker 进程语义，够用）：
  - `create_session(directory, title)` → 返回 `stub_<uuid>`；
  - `send_prompt_async(sid, content, ...)` → 记录 (sid, prompt, 派发时刻) 进完成器队列；
  - `get_messages(sid)` → 派发前仅 user 消息；延迟到期后追加 assistant 消息，形状对齐 OpenCode 原始结构（`info.role='assistant'`、`finish='stop'`、`time.completed`）——与 worker `_await_finished` 的完成判据逐字段对齐；
  - `abort_session(sid)` → 立即落终止形状（协作取消语义生效）；
  - `delete_session(sid)` → 清内存态。
- 完成器线程：按 profile 延迟后落完成；错误类按 `error_rate` 直接落「失败形状」（worker 判 failed）。
- 行为面 `AI_STUB_PROFILE`（JSON，env 注入）：`delay_ms:[min,max]`（默认 [2000,8000] 均匀）、`error_rate`（默认 0）、`hang_rate`（卡死不回，验工具看门狗）、`hang_recover_after_ms`。
- 单测要求：消息形状与 `opencode_client.get_messages` 真实返回的关键字段对照测试（防形状漂移）。

## 4. 套件设计（server/tests/stress/）

pytest marker `stress`（pytest.ini 注册，默认排除；`pytest -m stress` 显式触发）。数据统一 `STRESS-` 前缀。

### 4.1 test_capacity_ladder.py（stub 层）

- 阶梯：并发 5→10→20→35→50（`AI_BATCH_CONCURRENCY` 逐级重启压测后端），每级 ≥100 children、批次累计约 500（标准批：上传 1 小文件 + N children；附带 1 个「空壳批 + append 填充」场景覆盖 0 文件路径）。
- 每级升/停规则：成功率 ≥99% 且不变量全绿 → 升级；首次失败即停，**容量结论 = 最后全绿级**，并记录失败级的失败形态（排队堆积/超时/错误率）。
- 断言（每级）：计数守恒 `total = done+failed+cancelled`；无孤儿会话（batch_id 悬空）；无僵尸 running（终态采样窗口后仍 running 的行数=0）；error_rate ≤ profile 注入值 + 容差。

### 4.2 test_chaos_injection.py（专属真 serve 层，小规模 ≤3 并发）

| # | 注入 | 预期（对照 P0/P1 不变量，01 号 §8.2 八条） |
|---|------|--------------------------------------------|
| C1 | 运行中 kill 专属 serve | 活动子任务落 failed（带错误信息）而非僵尸；重启 serve 后队列继续消化 |
| C2 | 运行中 kill -9 压测后端 worker → 起第二 worker 进程（同库） | 租约 TTL 到期被接管、fencing token 递增、旧实例写回 0 行；同一子任务无双重执行（attempt 唯一） |
| C3 | outbox 目标 503 / 超时（本地假回调服务可控返回） | 指数退避 → dead_letter；unknown effect → needs_review 且不自动重放 |
| C4 | serve 运行中重启守卫 | 有活动会话时 restart 无 force 返回 409 ACTIVE_WORKLOAD |

C2 的「第二 worker」：同后端代码、同压测库再起一个进程，模拟双实例抢租约。

### 4.3 test_readpath_load.py（stub 层后端）

| # | 场景 | 断言 |
|---|------|------|
| R1 | 50 并发 SSE 连接（会话消息流 `ai_chat.py:1457` / 批事件流 `ai_chat_batches.py:871`）持续 5 分钟 | 无断流崩溃；断线重连后事件不丢（afterSeq 幂等）；后端 RSS 平稳 |
| R2 | 事件风暴：容量层残留事件之上压 events 分页端点 | P95 延迟阈值（首跑基线定，回归对照） |
| R3 | 管理列表 500 批次大分页 | P95 延迟阈值；分页边界正确 |
| R4 | outbox 洪峰 1 万行 + 假回调服务收集 | 排空吞吐（行/分钟）；无重复投递（幂等键） |

## 5. 指标采集与报告

- 采样器（conftest 内线程）：每 2s 记录 `/metrics` 7 gauge + 自查 SQL（排队等待时长分布、终态分布）+ psutil（后端/serve RSS、CPU）→ 每级/每场景一个 JSON。
- 衍生指标：吞吐（children/min）、排队等待 P50/P95（created_at→claimed）、执行时长 P95、错误率。
- 汇报：跑完自动汇总 `docs/ai-testing/evidence/stress/<date>-<suite>.md`（容量拐点结论 + 每级表格 + 混沌场景判定），JSON 同目录归档。

## 6. 数据治理

专属 DB 由 conftest 建/删，finalizer 保证失败路径也 DROP——**结构性杜绝 9/28 式 debris**。STRESS- 前缀仅作辨识。压测窗口与 dev 日常互不影响（不同库/端口/实例），可随时跑。

## 7. 交付物

| 交付物 | 位置 |
|--------|------|
| StubRuntime + 形状对照单测 | `server/utils/runtime/stub.py`、`server/tests/test_runtime_stub.py` |
| 压测栈编排（建库/起停服务/采样/清扫） | `server/tests/stress/conftest.py` |
| 三套件 | `server/tests/stress/test_capacity_ladder.py` / `test_chaos_injection.py` / `test_readpath_load.py` |
| 报告生成 | 并入 conftest（跑完即出 md+JSON） |
| marker 注册与使用说明 | `pytest.ini`（或 pyproject 配置处）+ 套件 docstring |
| 假回调服务（outbox 目标） | `server/tests/stress/fakes/callback_target.py` |

运行方式：`cd server && python -m pytest tests/stress -m stress -k capacity`（逐套件可独立跑；全量 `pytest -m stress`）。真模型混沌层 C1/C4 默认含在 chaos 套件内（已用专属 serve，无需额外标注）；不引入独立 `stress_real` 标记——C2/C3/R4 本来就不依赖真模型，C1/C4 用专属真 serve 但小规模，成本分钟级。

## 8. 不做（YAGNI）

- soak 长跑（未选）；压测平台化（时序库/仪表盘/历史基线对比）；DB 断连注入（危险且 dev 共库，savepoint 层模拟不可信）；分布式压测（单机足够到 50 并发量级）；前端浏览器级压测（读路径压载走 API 即可覆盖）。
