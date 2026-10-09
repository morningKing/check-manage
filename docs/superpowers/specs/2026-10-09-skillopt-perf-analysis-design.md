# SkillOpt 任务性能分析（agent/skill 耗时分析与优化诊断）— 设计

日期：2026-10-09 ｜ 状态：已评审（brainstorming 定稿，待出实施计划）
集成位置：后台 SkillOpt 单页（`AiSkillOpt.vue`）新增「性能分析」视图

## 1. 背景与目标

SkillOpt 页面现有能力回答「步骤到没到位」（拟合），缺「时间花在哪」。本特性为
agent/skill 任务提供耗时可视化与针对性优化诊断：

- 按定义（skill/agent）查看其任务的耗时趋势与分布，发现变慢的任务；
- 下钻单个任务，看到时间分解（模型推理 / 子代理等待 / 引擎间隙）与时间轴瀑布；
- 规则引擎自动产出优化诊断（只读结论 + 下钻定位），不代用户做改动。

分期：**一期**纯读既有数据（attempt/消息/子代理三层数据已落库），零写路径改动；
**二期**补工具级耗时采集，页面自动升级出工具热点。

## 2. 非目标

- 不做一键优化动作（改批配置/换模型等写操作）——诊断只给结论与定位，动作后续按需逐条加；
- 不建预聚 rollup 表——现阶段数据量级（数千 attempt）下现算毫秒级，页面打开时计算、手动刷新；
- 不覆盖 kefu 来源任务（`source_type = 'kefu'` 全链路排除）；
- 不做实时自动刷新与导出；
- 二期上线前不提供任何工具级耗时数字（宁可标注缺失，不估算）。

## 3. 口径与数据源（一期，零迁移）

### 3.1 锚点与关联

- **任务 = attempt**（`ai_execution_attempts`，一次派发/重试/继续/重跑 = 一次任务执行）。
  墙钟 `wallMs = finished_at - started_at`；running/未完成的以 now 截止并标注状态。
- **定义 → 任务**：`ai_execution_manifests`（attempt_id, kind ∈ {'skill','agent'},
  name）。交互（`ai_chat.py` send）、批任务、管理重跑、Open API 四条入口均已存
  manifests，全来源覆盖；kefu 行按口径排除。
- **模型轮次**：`ai_chat_messages`（session_id = attempt.session_id，role='assistant'），
  `meta` 的 `durationMs / tokensInput / tokensOutput`（`meta_from_info` 已持久化）。
- **子代理**：`ai_chat_subtasks`（root_session_id = attempt.session_id），
  区间 `created_at → completed_at`（未完成为 running，now 截止）。
- 消息/子代理 meta 缺失（旧数据）→ 该维度标注 `completeness`，不伪造
  （沿用执行审计 §17.1 data_completeness 惯例）。

### 3.2 覆盖切分（指标层核心）

模型轮次与子代理时间在真实执行中**重叠**（父轮次时钟在子代理运行时同样在走），
禁止简单加总。算法：把区间投到 attempt 时间轴做覆盖切分，产出一组**互斥**时长：

```
wallMs（attempt 全窗）
├── modelMs        模型活跃：assistant 消息区间（created → created+durationMs）的并集覆盖
├── subagentWaitMs 子代理等待：子代理区间并集覆盖中，不与模型活跃重叠的部分
│                  （= 父轮次挂起等 task 工具返回的时间）
└── idleMs         引擎间隙：其余（worker 轮询、持久化、OC 开销）
```

- 实现：边界点排序 → 逐原子段扫描 → 按「被模型覆盖 / 仅被子代理覆盖 / 无覆盖」
  归类累加。三类之和 = wallMs，占比各 = 本类 / wallMs。
- 纯函数 `coverage_split(wall, model_intervals, subagent_intervals) -> dict`，独立单测
  （重叠/嵌套/空区间/running 截止）。
- 二期工具区间作为第四类 `toolMs` 叠加（见 §7），一期不产出。

### 3.3 每任务指标

wallMs、三类覆盖与占比、轮次数、模型耗时合计（modelMs）、最慢单轮（durationMs
与 tokensInput）、tokensIn/tokensOut 合计、子代理数与各自 wallMs、source_type、
任务状态、completeness。

### 3.4 每定义聚合指标

任务数、wallMs P50/P95/均值、tokens 均值、模型占比均值、任务趋势序列
（按 started_at 倒序的列表即趋势）、最慢任务引用。**P50/P95/均值只统计已完结
任务**（completed/failed/partial 等，running 无 finished_at 不进分母，列表中
单独标注）。跨定义聚合仅「最慢 Top」。

### 3.5 索引与规模

一期不加索引不加迁移：现量级（数千 attempts / 每任务数十消息行）下顺序扫
manifests 与按 session_id 取消息均在毫秒级。二期迁移时顺带补
`ai_execution_manifests(kind, name)` 幂等索引（§7）。

## 4. 端点契约（5 个，管理权限）

全部挂既有 `ai_execution_admin_bp`（前缀 `/ai/chat/admin`），权限
`admin.ai_chat_admin`（与 skill-fit 一致），纯读。camelCase 出参。

| 端点 | 出参 | 用途 |
|---|---|---|
| `GET /perf/overview` | `{defs:[{defKind,defName,tasks,p50Ms,p95Ms,avgModelRatio,lastActivity}]}` | 性能视图首屏定义清单 |
| `GET /perf/defs/{kind}/{name}/tasks?limit=50`（limit ≤200） | `{tasks:[{attemptId,sessionId,sourceType,status,startedAt,finishedAt,wallMs,modelMs,modelRatio,subagentWaitMs,idleMs,turns,tokensIn,tokensOut,subtaskCount,completeness}]}` | 定义详情趋势/对比 |
| `GET /perf/attempts/{id}` | `{attempt:{...单条指标,requestedModel,effectiveModel}, coverage:{wallMs,modelMs,subagentWaitMs,idleMs}, turns:[{messageId,createdAt,durationMs,tokensIn,tokensOut,preview}], subtasks:[{subtaskId,agent,description,status,startedAt,finishedAt,wallMs}], completeness}` | 任务下钻主数据 |
| `GET /perf/attempts/{id}/diagnosis` | `{diagnoses:[{ruleId,severity,text,anchor:{type,ref}}]}` | 诊断结论 |
| `GET /perf/slow-tasks?limit=10` | `{tasks:[...同任务条目 + defKind,defName]}` | 全局最慢 Top |

kefu 排除在 SQL 层统一实现（`source_type <> 'kefu'`）。404 语义：attempt 不存在
或其 session 无权可见 → 404；无 manifest 关联的定义 → 空 tasks 列表而非报错。
`completeness` 对象形如 `{turnsWithoutDuration: number, runningSubtasks: number}`
（缺 durationMs 的轮次数 / 仍 running 的子代理数），前端据此显示「数据不完整」
标注。

实现落点：`server/utils/perf_analysis.py` 纯函数模块（`get_db` 参数注入，照
`skill_fit` 惯例），聚合 SQL / 覆盖切分 / 诊断规则全部可独立单测；路由只做参数
解析与 JSON 化。

## 5. 前端 UI 与图表

### 5.1 挂载与三层结构

`AiSkillOpt.vue` 单页内加视图切换「任务拟合 ｜ 性能分析」（不动菜单/路由）：

1. **首屏**：左栏定义清单（任务数/P95 速览）；右侧 `PerfSlowTop`「最慢任务
   Top10」卡片（跨定义快捷发现，点击直达任务下钻）。
2. **定义详情**（点定义）：指标卡（任务数 / P50 / P95 / 模型占比均值）+
   `PerfTrendChart` 耗时趋势（x=任务时间序，y=墙钟，柱色区分 source_type，
   叠加模型占比折线）+ 可排序任务表格。
3. **任务下钻**（点任务行，页内展开）：`PerfTaskDetail` 含——
   - 覆盖占比条：单根堆叠横条（模型活跃/子代理等待/引擎间隙）+ 图例；
   - 时间轴瀑布（ECharts custom series）：模型轮次段与子代理区间按时序铺开，
     hover 显示 token/委派描述；
   - 子代理表、模型轮次表（最慢单轮高亮）；
   - `PerfDiagnosisList` 诊断列表：每条带「定位」，点击滚动到对应时间段/表格行
     并脉冲高亮（复用 todo 执行轨迹「定位」交互）。
   - 二期占位：工具耗时区块，数据缺失显示「该任务早于工具耗时采集上线，仅
     新跑任务可见」。

### 5.2 组件与图表封装

`src/components/admin/skillopt/` 下：`PerfView.vue`（壳）、`PerfSlowTop.vue`、
`PerfTrendChart.vue`、`PerfTaskDetail.vue`、`PerfDiagnosisList.vue`。ECharts 用按需
懒加载小封装（bar/custom/tooltip 按需注册；echarts 已是仓库依赖，零新增包体）。
API 封装加在 `src/api/aiSkills.ts`。

## 6. 诊断规则引擎（一期 8 条）

形态：`diagnose(breakdown) -> list[Diagnosis]` 纯函数，输入为 §4 attempt breakdown
的内存结构，零额外查询。`Diagnosis = {ruleId, severity: 'info'|'warn', text,
anchor:{type:'turn'|'subtask'|'segment'|'def', ref}}`。每条规则独立函数 + 一个
编排器；阈值集中为模块常量；单测逐规则喂构造数据。

| ruleId | 触发条件 | 结论示例 | 级别 | anchor |
|---|---|---|---|---|
| subagent_wait_dominant | subagentWaitMs/wallMs > 0.5 且 wallMs > 60s；若任务属批且批未配 subagent_reuse，追加复用建议 | 「68% 时间在等子代理（3 次委派）」 | warn | subtask（最慢者） |
| model_dominant | modelMs/wallMs > 0.7 | 「时间主要花在模型推理（共 Xs / N 轮）」 | info | segment（最慢轮） |
| slow_turn_big_context | 单轮 durationMs > 30s 且 tokensInput > 80k | 「第 2 轮 45s、输入 180k token——建议拆分任务或压缩历史」 | warn | turn |
| repeated_tool_calls | agent_tool_calls 同 tool + 规范化 args_text ≥3 次（一期无时长，只报次数） | 「read 同一参数重复 5 次」 | warn | segment |
| tool_error_storm | state='error' 工具调用 ≥3 | 「7 次工具失败（bash×4、write×3）」 | warn | segment |
| engine_overhead | idleMs/wallMs > 0.3 且 wallMs > 120s | 「引擎开销占 35%——看同时段任务是否普遍如此」 | warn | segment |
| outlier_vs_peers | wallMs > 该定义 P50 × 4 | 「比该 skill 典型水平慢 5 倍（P50 20s vs 100s）」 | warn | def |
| sequential_subagents | ≥2 个子代理且两两重叠率均 < 0.1（重叠率 = 区间交集时长 / 较短者时长） | 「3 个子代理串行执行，评估并行委托」 | info | subtask |

阈值常量：`SUBAGENT_WAIT_RATIO=0.5 / SUBAGENT_MIN_WALL_MS=60_000 /
MODEL_RATIO=0.7 / SLOW_TURN_MS=30_000 / BIG_CONTEXT_TOKENS=80_000 /
REPEAT_TOOL_COUNT=3 / TOOL_ERROR_COUNT=3 / IDLE_RATIO=0.3 /
IDLE_MIN_WALL_MS=120_000 / OUTLIER_P50_FACTOR=4 / SEQUENTIAL_OVERLAP=0.1`。

## 7. 二期：工具级耗时采集（独立小特性，单独计划）

- **数据源**：OpenCode 工具 part 的 `state.time.{start,end}`（前端工具气泡已在
  用它现算时长），持久化时目前被丢弃。
- **迁移**：`agent_tool_calls` 加 `started_at TIMESTAMPTZ`、`duration_ms INTEGER`
  （幂等），顺带 `ai_execution_manifests(kind,name)` 幂等索引；走双注册 + 既有
  护栏 `test_migration_boot_registration.py` 自动把关。
- **写路径**：批任务 `agent_ledger.record_messages` 与交互收敛两条路径均从 part
  `state.time` 提取落列（账本本就是工具调用唯一落点，不新增表）。实施时确认
  part 映射层（`opencode_parts.map_part`）透传或旁路暴露原始 time。
- **旧数据不回填**（沿用审计惯例），缺失行在 breakdown 中缺省。
- **页面升级**：下钻加工具聚合表（每工具 调用数/总时长/占比）+ 可选工具时间轴；
  规则升级：repeated_tool_calls 带真实时长，新增 tool_hotspot（单工具耗时占比
  > 0.4 → warn）。

## 8. 测试策略

- **后端** `server/tests/test_perf_analysis.py`：
  - 覆盖切分器单测：区间重叠/嵌套、空区间、running 截止、meta 缺失、kefu 排除；
  - 诊断规则逐条：构造 breakdown 断言触发/不触发与阈值边界；
  - 端点测试：app fixture 播种 attempt+manifests+消息+子代理（风格照
    `test_subagent_reuse`），断言 JSON 契约与 404/过滤语义。
- **前端** vitest：PerfView mock api 渲染、覆盖条比例、诊断锚点映射、趋势图
  ECharts option 形状断言（mock init，断言 series 配置）。
- **真实链路 E2E**（`e2e/ai-full/`）：真 LLM 建批跑至终态 → 打开 SkillOpt 性能
  视图 → 断言趋势图出现该任务、下钻覆盖条与诊断列表非空（@llm 标注）；另配
  一条纯 DB 播种确定性用例盖零 LLM 回归。

## 9. 风险与取舍

- **现算性能**：定义详情一次聚合按 session_id 取消息 + manifests 过滤，数千
  attempt 量级毫秒级；若未来上十万级再评估 rollup（YAGNI 预登记）。
- **durationMs 口径**：消息 meta 的 durationMs 是 OpenCode 报告的模型回合时长，
  含 provider 侧排队，不代表纯 token 生成时间——诊断文案统一说「模型回合耗时」。
- **多轮任务的归属**：continue/重跑产生新 attempt，同一 session 的历史轮次不计入
  当前任务指标（按 attempt 时窗切），诊断文案涉及历史时明确说「上一执行」。
