# 能力稳定性压测首跑报告（子代理 SSE × 门禁并发）

> 日期：2026-10-06 ｜ spec：`docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md`（§10 为发现清单权威口径）
> 套件：`server/tests/stress/test_capability_stress.py`（S0–S5 共 7 用例）｜ 运行：`cd server && python -m pytest tests/stress/test_capability_stress.py -m stress`
> 分支：`feat/capability-stress`（spec c472ab9 → 计划 7477daf → 实施至本报告，全程 0 token）

## 1. 跑动结论

| 轮次 | 结果 | 说明 |
|---|---|---|
| run 1（首跑） | 6/7，S5 挂 | S5 期望行停留 pending——暴露 FK 竞态（发现 #B） |
| run 2 | 6/7，S5 挂 | 30s 自愈容忍仍不够——暴露监听器晚订阅饿死（发现 #C/修复 #4） |
| run 3 | **7/7 全绿，14:54** | 全部修复后第一遍干净通过 |
| run 4 | **7/7 全绿，14:47** | 可重复性确认（专属栈每轮全新建/拆，天然隔离） |

分用例实测时长（run 3）：S0 ~20s｜S1 ~8.3min（50 连接真 5min 持流）｜S2 ~3.2min｜S3×2 ~3.5min｜S4 ~35s｜S5 ~25s。总时长 ~15min，优于 spec 预算（30–40min）——S1 占大头。

## 2. 各场景验证了什么（全部为可失败断言）

- **S0 交互全链路冒烟**：建会话→先挂 SSE→发送→帧序（message.updated 开场、idle 收尾、3 个 part）→消息落库→账本恰 2 行 bash。是接线改动的回归锚。
- **S1 批次 SSE 帧级正确性**：50 观察者 1s 爬坡持流 5min；帧 seq 对 `ai_batch_events` 全集精确对账（watcher 内严格递增、并集==表全集）；1/3 观察者流内主动断开带 Last-Event-ID 重连，跨重连无缺口；终态 batch_done 关流。**旧 R1 只压连接不断流，本场景首次把帧内容纳入验证。**
- **S2 并发终态 × 门禁评估**：50 子任务 × 3 期望（2 file 应过 + 1 tool 必败对照）并发终态：期望恰好核对一次、gate_passed/gate_failed 与期望行逐一对账、gate.evaluated 审计事件 == 子任务数、无 drain 超时、守恒全绿。
- **S3 门禁修正/重试/取消竞态**：必败期望 + gate_retry 并发 cancel：预算不超发（retry_count≤3）、终态合法唯一、无 pending 孤儿；成功侧（S3b）continue attempt 出现后补种文件 → 修正轮转 passed → completed 且 retry_count 恰 1。
- **S4 交互收口竞争**：20 会话 × 双 SSE 流并发收口：账本按 (oc_session_id, part_id) 幂等恰 2 行/会话、交互期望登记恰 n 行全核对。
- **S5 树作用域聚合**：委派负载下（explorer+writer 各 1 子代理各 2 bash）：tree+subagents=['explorer'] 期望 ('passed',2)、writer 排除对照 ('failed',0)、账本明细按 agent 聚合与核对结果一致。

## 3. 判别力登记（沿 12 号铁律，逐条实测）

| 用例 | base 必失败声明 | 验证方式 | 结论 |
|---|---|---|---|
| Task1 事件总线/门面单测 | 是（结构性） | base 无 `subscribe_events` → AttributeError；S0 对同一接线链路 live A/B | 结构性成立 |
| S0 交互冒烟 | 是（接线） | checkout f041f71~1 两文件实跑 | **实测红**（ConnectionError），恢复后绿 |
| S1 帧对账 | 结构性 | 现网无帧解析实现（R1 不解析帧） | 结构性成立 |
| S2 并发门禁 | 首跑即 base | 首跑压出 MAX_CONCURRENT 缺陷 | **实测压出缺陷 #2** |
| S3 预算竞态 | — | 依赖既有单元/生产验证；压测实证预算语义（retry_count 恰 1） | 放弃独立 base 对照（理由已记） |
| S4 账本幂等 | 唯一索引结构性 | 压测实测默认池下「丢失」而非「重复」 | 结构性成立 |
| S5 聚合 | 首跑即 base | 首跑压出 FK 竞态/监听器饿死 | **实测压出 #B/#C** |
| 交互账本 map_part 修复 | 是 | 回归测试走真实 apply_event 累积路径 | **修复前 commit 实测红** |
| MAX_CONCURRENT 修复 | 是 | 单测 env=7 断言线程数 | **修复前 commit 实测红** |
| stub info.id 修复 | 是 | 形状锁断言 + S3b attempt 24ms 实证 | **实测压出** |

## 4. 产品修复（4 项，均已合分支）

| # | 缺陷 | 修复 commit |
|---|---|---|
| 1 | 交互路径动作账本恒 0 落账（map_part 映射形状 vs extract 原始形状）——交互工具型门禁恒 failed | 41271d3 |
| 2 | `AI_BATCH_CONCURRENCY` 只放大认领数、执行线程池恒 3——**容量阶梯"无拐点"结论需据此重审**（此前各级实际并行恒 3） | a49e8b0 |
| 3 | StubClient REST 消息缺 info.id → continue 基线快照恒空 → gate-retry 修正轮 24ms 假完成 | 2d61733 |
| 4 | stub 总线无视 read_timeout → 监听器晚订阅错过短回合后永久饿死，无人持久化/收口 | d0af9e1 |

## 5. 登记待决发现（3 项，未修）

- **#A 池饥饿**：默认池 20 下 20 并发 finalize 突刺 → 账本行丢失、门禁 inconclusive（方向 fail-closed 安全，但高并发下交互门禁不可靠）。S4 用 `DB_POOL_MAXCONN=60` 隔离被测属性。建议：收口对 PoolError 短重试或生产调大池。
- **#B FK 竞态自愈窗口**：SSE 收口落账与监听器持久化竞态（`agent_tool_calls.subtask_id` FK），监听器 finalize 幂等补账自愈；自愈不达时期望行停留 pending 一轮。良性。
- **#C SSE 兜底持久化被 has_listener 单边压制**：监听器已注册即跳过 SSE 兜底 persist——监听器饿死时本回合消息无人持久化。建议：兜底改「监听器已收口才跳过」或 idle 后 N 秒无落库补偿。

## 6. 指标与证据

- 采样 JSON/对账 dump：`docs/ai-testing/evidence/stress/2026100*-s*.json`（s1-sse-reconcile / s2-gate-concurrent / s3-gate-retry-race / s3b-retry-forensics / s4-finalize-contention / s5-tree-scope）
- 压测栈日志：`docs/ai-testing/evidence/stress/backend.log`（跨轮追加，注意 8KB 块缓冲——末端行可能滞后落盘）
- S5 探针存档：`docs/ai-testing/evidence/stress/2026-10-05-s5-probe.md`

## 7. 遗留

1. **容量阶梯结论重审**：发现 #2 意味着 2026-10-04 容量跑各级实际并行恒 3，「50 并发无拐点」实为「3 并行无拐点」——修复后重跑容量阶梯属后续任务（本 spec §9 YAGNI 边界外）。
2. S1.5（`: pool-busy` 降级帧观察项）：标准档建连爬坡下未捕获样本，保持观察性用例定位。
3. `test_batch_engine` 4 例在本分支批量跑挂、单跑全过（stash A/B 实证与本批改动无关）——顺序/状态敏感，沿既有「dev 库残留 pending」口径，另立处理。
4. S3 判别力无独立 base 对照（缺陷注入成本高），以既有单元 + 生产 e2e 验证背书，已登记。
5. S2 门禁为 stub 层（无真实工具调用）；真模型门禁行为由 e2e 域承担（spec §9 边界）。
