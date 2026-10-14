# 运行诊断与技能进化闭环（O1+O2）— 设计

日期：2026-10-14 ｜ 状态：已评审（brainstorming 定稿，待出实施计划）
集成位置：执行审计（`docs/design/ai/` 执行审计分册）· SkillOpt 技能拟合/性能分析 · 管理端 AI 执行中心
依据：李博杰《深入理解 AI Agent》第 8 章（持续进化）——8.1 三层轨迹验证、8.2 四种更新载体、8.3 双循环与睡眠学习、表 8-3 分层指标

## 1. 背景与目标

系统当前有大量**散点式**的运行评估：`trace_auditor` 的确定性审计报告（契约步骤 +
violations + 证据引用）、门禁终态（三型 + 期望清单）、任务拟合（steps 命中率）、
性能分析（覆盖/轮次/9 条诊断规则）、以及按需的 LLM"轨迹分析"会话与拟合偏差诊断
（`skill_fit_ai.diagnose_result` → suggestions → applied 反馈 → 前后 7 天效果）。

第 8 章的框架指出两处结构性缺口：

1. **学习信号不统一**（8.1）：评价应产出"结构化多维诊断"——维度化 pass/fail/
   uncertain + 证据位置 + 置信度，而不是散落在五个子系统里的分块与标量；
   "承诺—行动一致性"这类检查数据齐全却无人做；成功轨迹不积累诊断，
   导致后续进化缺"保留集/成功约束"的数据源。
2. **进化闭环不成环**（8.2/8.3）：现有 diagnose→建议→人工改→效果追踪是"无门槛
   快路径"——修改直接生效、无候选区、无回归门槛、无最小 diff、无来源 manifest。

本设计交付两件事：

- **O1 统一运行诊断**：把上述散点归一为统一维度模型，attempt 收敛时**全量自动**
  落确定性诊断快照（成功/失败都落），LLM 质量维度按需触发；提供维度卡展示、
  失败簇聚合与重算能力。**这是所有进化动作的信号底座。**
- **O2 技能进化闭环**：在现有建议链路上补"有门槛版本"——候选（最小 diff +
  来源 manifest，存 DB 不碰正式路径）→ **双轨回归**（离线重拟合全量免费 +
  内容类修改小样本重跑）→ **人工审批发布** → 版本归档回滚 + 效果追踪。

已与需求方对齐的四个关键决策（brainstorming 定稿）：

1. **诊断全量自动**：确定性维度纯读现有表（零 LLM 成本），成功/失败都落快照；
   LLM 质量维度按需（簇自动触发只留触发点）。
2. **回归双轨**：离线重拟合承担大部分回归（利用"拟合是轨迹事后匹配"这一
   书里没有的天然优势）；只有触及正文/指令内容的修改才追加小样本重跑。
3. **人在两处**：候选手动触发、发布人工审批；簇自动触发生成候选登记为快跟进。
4. **架构取"trace_auditor 升格"**：加维度归一层 + 快照落库，旧 report 结构
   向后兼容，不新建并行诊断体系。

## 2. 非目标（v1 不做）

- **验证器校准**（专家标注集 / 低置信度二次复核 / 模型变更重校准）——登记为
  后续项；维度 schema 预留 `confidence` 与校准位。
- **LLM 质量维度的簇自动触发**与**候选的簇自动生成**——依赖 O1 簇聚合先积累
  数据；本设计只在端点与数据结构上留触发点。
- **门禁策略候选**（由失败簇建议生成 gate 规则，即 O3）——不同载体，独立排期。
- **睡眠学习/修剪**（重复技能检测、陈旧标记、经验淘汰，即 O5）——独立排期。
- **参数层更新**（SFT/RL）——不适用当前场景。
- **灰度发布**：系统无流量灰度形态；v1 以"发布即生效 + 版本归档回滚 + 前后
  7 天效果追踪"作为对齐替代（见 §5.4）。
- O2 只覆盖 **Skill 定义**（fit frontmatter + 正文）；其他定义类型不在此期。

## 3. 现状底账（本设计复用的事实）

| 能力 | 位置 | 本设计如何用 |
|---|---|---|
| 确定性审计报告 v1 | `utils/trace_auditor.py`：execution / contract（steps+violations+evidence_refs）/ declared_plan / observed_evidence / tool_failures / step_completion / data_completeness | O1 的维度归一器主输入；旧字段原样保留 |
| 门禁三型与终态 | `action_expectations` + `ai_chat_sessions.gate_status/gate_error` + `/gate` 端点 | `gate_compliance` 维度 |
| 拟合结果 | `ai_skill_fit_results`（steps_hit/total、status）+ `match_steps` | `fit_following` 维度；O2 轨 1 重拟合 |
| 性能指标 | `utils/perf_analysis.py`（token/墙钟/等待占比/9 诊断规则） | `efficiency` 维度（信息型） |
| 诊断任务行 | `ai_execution_diagnoses`（轨迹分析会话作业行，report JSONB） | 保留不动；LLM 质量维度结果写入 O1 快照而非此表 |
| 建议链路 | `skill_fit_ai.diagnose_result`（cause/suggestions/revised_steps）→ `ai_suggestion_feedback`（applied+before_metrics）→ `/skill-suggestions/<id>/effect`（前后 7 天） | O2 在其旁补候选链路；发布时自动补写 applied 反馈保持效果追踪连续 |
| 定义版本归档 | `ai_skill_def_versions`（content/hash/avgScore/fitRate/labels）+ apply/rollback 写路径与路径白名单 | O2 发布/回滚直接复用 |
| 迁移运行器 | `migrations/` + `app.py` 显式注册表双注册 | 新迁移按此交付 |

## 4. O1：统一运行诊断

### 4.1 维度模型

统一清单，每条维度：

```jsonc
{
  "id": "claim_action",            // 稳定标识（聚合与簇查询键）
  "label": "承诺—行动一致性",
  "status": "pass | fail | uncertain | not_applicable",
  "source": "deterministic | llm",
  "confidence": 1.0,               // deterministic 恒 1.0；llm 维度给 0~1，证据不足 → status=uncertain
  "evidence_refs": [               // 证据坐标（复用既有锚点体系）
    {"turn": 0, "part_id": "prt_…", "subtask_id": "ses_…|null",
     "step_id": "…|null", "call_index": 3}
  ],
  "detail": "人类可读一句话（含关键数字/缺失对照）"
}
```

状态语义：`pass` 达标；`fail` 有明确反证；`uncertain` 证据不足/无法判定（**不得**
用 fail 冒充）；`not_applicable` 本次运行不含该维度前提（如无门禁配置）。缺失数据
不得判 fail——由 `data_completeness` 表达缺口（沿用审计报告的口径）。

### 4.2 v1 维度集（全部确定性）

| id | 判定来源 | 规则 |
|---|---|---|
| `task_outcome` | attempt.status + 会话终态 | failed/cancelled→fail；completed→pass；终态缺失（运行中/中断）→uncertain |
| `contract_following` | trace_auditor 契约块 | violations 含 missing/out_of_order/failed→fail（取首条为 detail）；全 confirmed→pass；无契约→not_applicable（detail 引 data_completeness.limitations） |
| `claim_action` | 新检查（§4.3） | 完成型声明无对应 completed 工具→fail；全部有据→pass；无法解析→uncertain |
| `gate_compliance` | gate_status + 期望清单 | gate failed→fail；pre 命中且绕行成功→pass（detail 记命中次数）；checkpoint 命中已注入→pass；无门禁→not_applicable |
| `tool_reliability` | tool_failures + 恢复检测 + perf repeats/errorCount | 失败未恢复→fail；失败已恢复→pass（detail 注明 N 次失败已恢复）；无失败→pass |
| `fit_following` | `ai_skill_fit_results` | diverged→fail；partial→uncertain；100→pass；无拟合结果→not_applicable |
| `efficiency` | perf（token/墙钟/子代理等待占比/慢规则） | 信息维度：detail 承载指标；阈值默认关闭（阈值键放全局 AI 设置 `ai_settings`，配置后才判 fail） |

（客服类场景专属维度——隐私边界、合规变通——不在 v1：当前系统无对应数据源；
维度模型按 id 可扩展。）

### 4.3 承诺—行动一致性检查（新算法）

- **声明抽取**：对 attempt 内 assistant 文本做保守规则匹配——完成型句式
  （"已提交/已完成/已创建/已发送/已保存/已写入"+ 宾语；"成功" + 工具动作词）。
  规则表可配（正则数组），宁可漏报不误报。
- **核对**：抽取到的声明与账本（`agent_tool_calls`）对照——同 attempt 内是否存在
  语义对应的工具调用且 `state='completed'`；宾语 → 工具映射用保守关键词表
  （如"提交/发送"→ bash/网络类工具，"写入/保存"→ write/edit，"读取"→ read），
  映射不确定时降级为 uncertain 而非 fail。
- **证据**：声明所在 turn + 对应工具 part_id；无对应工具时 evidence_refs 只带
  声明坐标，detail 给出"声明了 X，未找到对应工具调用"。
- 边界：声明在子代理内发生同样适用（evidence 带 subtask_id）；纯文本问答任务
  无可核对的工具语境 → not_applicable（无完成型声明时）。

### 4.4 LLM 质量维度（按需，v1 最小版）

- 复用现有 verifier/rubric 基建（`utils/verifier.py` + `check_type='verifier'` 的
  rubric 机制），新增一个通用 `quality` 维度入口：管理员在诊断卡上点"质量评估"。
- 逐项给分 + 引用轮次证据；**证据不足必须输出 uncertain**（rubric 协议里已要求）；
  结果以 `source='llm'` 写回同一 dimensions 清单，带 confidence。
- rubric 默认可配（全局默认一套通用维度：任务对齐/表达质量/工具使用恰当性）。
- 成本可见：沿用既有 LLM 调用记账（operation_logs + token 统计）。
- **簇自动触发**（未来）：`GET clusters` 端点就绪后，同维度 fail 跨任务达阈值
  N 条 → 建议触发；本设计只留端点契约。

### 4.5 落库：`ai_run_diagnoses`（快照）

新迁移 `2026_10_14_run_diagnoses.py`（**双注册**：migrations/ + app.py 启动钩子）：

```sql
CREATE TABLE IF NOT EXISTS ai_run_diagnoses (
  id                VARCHAR(100) PRIMARY KEY,
  attempt_id        VARCHAR(100) NOT NULL UNIQUE
                    REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  session_id        VARCHAR(100) NOT NULL,
  source_type       VARCHAR(30),
  dimensions        JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary           JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 计数/首要失败维度/uncertain 数
  engine_version    VARCHAR(30) NOT NULL,                -- 维度模型版本，升级后可批量重算
  data_completeness NUMERIC(4,3),
  computed_by       VARCHAR(20) NOT NULL DEFAULT 'auto', -- auto | manual
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ai_run_diagnosis_dim_gin
  ON ai_run_diagnoses USING gin (dimensions jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ai_run_diagnosis_session_idx
  ON ai_run_diagnoses(session_id, computed_at DESC);
```

- **写入时机（统一钩子，best-effort 不阻断主流程）**：批任务 `_mark_done/
  _mark_failed/_mark_cancelled` 收敛点与交互 `finalize_interactive_turn` 收敛点，
  调 `run_diagnosis.compute_for_attempt(attempt_id)`；成功失败都落。
- **幂等与重算自愈**：按 `attempt_id` upsert；`engine_version` 变化或管理员手动
  触发时重算覆盖（沿用拟合"重算自愈"模式）；LLM 质量维度结果在重算时保留
  （合并进新清单，避免确定性重算冲掉已花钱的 LLM 结果）。
- **失败簇读**：`SELECT id, count(*) … GROUP BY` 由 `dimensions @> '[{"id":…,
  "status":"fail"}]'` 过滤（jsonb_path_ops 索引支撑），v1 提供端点与列表展示。

### 4.6 读端点与前端展示

- `GET /ai/chat/admin/run-diagnoses/<attempt_id>`：维度卡数据（维度清单 + summary +
  completeness；不存在时即时编译一次并落库，复用审计报告的 live-compile 惯例）。
- `GET /ai/chat/admin/run-diagnoses/clusters?dimension=<id>&days=7`：失败簇
  （维度 → 最近 N 条 fail + 样本 attempt 链接 + 涉及定义）。
- `POST /ai/chat/admin/run-diagnoses/<attempt_id>/recompute`：手动重算。
- `POST /ai/chat/admin/run-diagnoses/<attempt_id>/quality`：LLM 质量维度按需。
- **前端**：管理端"会话审计"既有报告视图（`ExecutionAuditDrawer.vue`）顶部加
  "诊断维度"区块——维度徽标（pass 绿/fail 红/uncertain 黄/na 灰）+ detail +
  **证据跳转**（点击锚到既有轮次时间轴/工具明细行，复用 `turnIndex/part_id`
  锚点体系）+ source 标注；审计页新增"失败簇"列表（点维度看样本）。
  旧 report 区块原样保留。

## 5. O2：技能进化闭环

### 5.1 候选载体与最小 diff

新迁移（与 O1 同批或相邻）建表 `ai_skill_candidates`：

```sql
CREATE TABLE IF NOT EXISTS ai_skill_candidates (
  id            VARCHAR(100) PRIMARY KEY,
  def_kind      VARCHAR(20) NOT NULL,          -- skill（v1 仅此）
  def_name      VARCHAR(100) NOT NULL,
  base_hash     VARCHAR(100) NOT NULL,         -- 基于哪个生效版本
  base_path     TEXT NOT NULL,
  edits         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{old_str,new_str}] 最小编辑序列
  steps         JSONB,                         -- 候选 fit steps 快照（frontmatter）
  manifest      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 来源/根因/预期/风险/用例（§5.2）
  status        VARCHAR(20) NOT NULL DEFAULT 'draft',
                -- draft → validating → validated | rejected → released | rolled_back
  validation    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 双轨回归结果（§5.3）
  created_by    VARCHAR(100),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  released_at   TIMESTAMPTZ,
  released_hash VARCHAR(100)
);
```

- **存 DB 不碰正式路径**：候选内容 = base 文件内容 + edits 重放；验证时物化到
  专用临时目录（工作区根之外的受管候选目录），**正式定义文件只在"发布"时改写**。
- **最小 diff**：edits 为文件级 `old_str→new_str` 序列（章的最小 diff 原则）；
  LLM 生成器按此协议产出（old_str 必须唯一命中，否则候选判 draft 生成失败）；
  现有诊断的 `revised_steps`（结构化）由确定性转换器转成 frontmatter 编辑。
- **发布零 LLM**：发布 = 重放 edits 写回正式路径（复用现有 apply 写路径与
  路径白名单校验），不调任何 LLM。

### 5.2 来源 manifest 与双集自动选样

manifest（章的四件套 + 用例设计）：

```jsonc
{
  "evidence": [{"run_diagnosis_id": "…", "dimension": "fit_following",
                "fit_result_id": "…"}],
  "root_cause": "…",              // 推断根因（LLM 生成，来源=诊断维度 + 轨迹）
  "target_component": "fit_steps | body_section:<heading>",
  "expected_fix": "…",            // 预期修复的行为
  "at_risk_behaviors": ["…"],     // 可能受损的既有行为（保留集守护的项）
  "cases": {"boundary": ["att_…"], "holdout": ["att_…"]},
  "generated_by": "llm | manual | revised_steps_converter"
}
```

- **boundary 集自动选样**：该技能历史上 `fit_following=fail/uncertain` 或
  `claim_action=fail` 的 attempt（来自 O1 快照，取最近 K 条，默认 K=10）；
- **holdout 集自动选样**：该技能历史 `fit=100` 的成功 attempt（默认最近 K=10）；
- 人工可在候选详情里增删样本；学习集与验证集互斥校验（防"用答案当经验"）。

### 5.3 双轨回归

**轨 1 · 离线重拟合（全量、零 LLM）**：
- `skill_fit` 新增纯函数入口 `score_steps_against_trace(steps, trace)`——给定候选
  steps 与给定 attempt 的轨迹，复用 `match_steps` 产出命中率（不改动既有落库路径）。
- 对 boundary + holdout 全量重算，与"基准分"（现生效版本的拟合分）对比。
- **门槛**：boundary 平均分**提升** ∧ holdout 平均分**不退化**（容差 0）。

**轨 2 · 小样本重跑（仅内容类修改触发）**：
- 触发条件：edits 触及 frontmatter 之外的内容（正文/段落）。纯 steps 修改只跑轨 1。
- 样本：boundary N 条 + holdout M 条真实重跑（默认 N=2/M=2，全局可配），复用批
  任务基建（同一 agent/model 与历史一致），产物用 O1 诊断引擎 + 拟合评估。
- 门槛：boundary 样本 `fit_following/claim_action` 无 fail（或较基准改善）∧
  holdout 样本无新增 fail。
- 成本可见：重跑走既有 token/成本统计；候选详情展示预计与实耗。

**判定**：两轨都过 → `validated`；任一不过 → `rejected`，失败原因保留在
validation（供下一轮生成参考，避免换说法重复提交）。回归执行异步（后台线程 +
状态机，页面轮询进度），不阻塞管理端。

### 5.4 发布、回滚与效果追踪

- **人工审批发布**：管理员在候选详情点"发布"→ 校验 base_hash 仍等于当前生效
  版本（防并发漂移，不等则要求重算回归）→ 重放 edits 写回 → 版本归档自动记录
  （`ai_skill_def_versions` 新 hash 行）→ 候选置 `released`。
- **回滚**：复用现有 rollback 写路径；回滚后候选置 `rolled_back`。
- **效果追踪**：复用 `GET /skill-suggestions/<id>/effect`（前后 7 天调用成功率）；
  候选发布时自动补写一条 `ai_suggestion_feedback(action='applied', before_metrics)`
  保持既有列表/效果端点连续可用。
- **灰度替代说明**：系统技能为全局注入配置，无流量分流形态；以"发布即生效 +
  归档回滚 + 7 天效果对比 + 候选失败原因留档"对齐章的灰度/回滚意图。此取舍
  为已知设计决定，非遗漏。

### 5.5 与现有链路的关系

- 保留 `diagnose → suggestions → applied → effect`（无门槛快路径，人工直接改）；
- 新候选链路是"有门槛版本"：入口 = O1 诊断卡（维度 fail）或拟合诊断结果上的
  "生成候选"按钮；诊断建议文本进 manifest 的 root_cause/expected_fix；
- 两条链路的修改产物都进版本归档，效果追踪共用。

### 5.6 端点与前端

- `POST /ai/chat/admin/skill-candidates`：从诊断/拟合结果生成候选（手动触发，
  体含 `{defKind, defName, source: {runDiagnosisId|fitResultId}}`）。
- `GET /ai/chat/admin/skill-candidates`（列表，可按 status/def 过滤）、
  `GET /ai/chat/admin/skill-candidates/<id>`（详情：manifest/edits/validation）。
- `POST /ai/chat/admin/skill-candidates/<id>/validate`（触发双轨回归，异步）、
  `POST …/<id>/release`（审批发布，校验 base_hash）、
  `POST …/<id>/reject`（驳回并留原因）。
- **前端**：SkillOpt 页（`/admin/ai-skills`）新增"进化候选"区块——候选列表
  （状态徽标/定义/来源）+ 详情（manifest 四件套、edits diff 预览、双轨回归结果
  与门槛判定、发布/驳回按钮、发布后效果入口）；诊断卡上的"生成候选"按钮为
  入口之一。

## 6. 安全边界（章的三条，落地映射）

1. **证据与指令隔离**：轨迹中的工具输出/网页内容是**不可信证据**——候选生成的
   root_cause/expected_fix 由 LLM 在诊断证据上总结产出（不直接把原始输出写进
   edits）；manifest 标注证据来源 id；发布前人工审批构成审阅门。
2. **候选与正式隔离**：候选存 DB、验证物化在受管临时目录，正式路径只在发布时
   改写（复用既有路径白名单）；未 validated 的候选不可发布（状态机强制）。
3. **安全机制不可自改**：回归用例集、门槛判定、验证器、发布校验、审计日志均在
   候选可修改范围之外（代码/常量，不由 Agent 产出）；候选生成器不能改这些。

## 7. 登记项（明确不做，留点）

1. 验证器校准（专家标注集 / 低置信度复核 / 模型变更重校准）——dimensions 已留
   confidence 位；
2. 簇自动触发（LLM 质量评估、候选生成）——端点契约已留（§4.4/§4.6）；
3. O3 门禁策略候选（失败簇 → 建议 gate 规则）；
4. O5 睡眠学习/修剪（重复技能、陈旧标记、经验淘汰）；
5. 表 8-3 四指标拆分展示与双向模型替换实验（评估体系，O4）。

## 8. 验收与测试策略

- **TDD 失败先行**：维度归一器（各维度判定规则 × pass/fail/uncertain/na）、
  承诺—行动核对、双集选样、门槛判定、状态机、edits 重放/唯一命中校验——每条
  先红后绿；
- **迁移**：`2026_10_14_run_diagnoses.py`（含 candidates 表）**双注册**
  （migrations/ + app.py 钩子），护栏测试 `test_migration_boot_registration`
  自动覆盖；
- **真实链路**：批任务（含子代理/门禁/拟合）与交互回合各跑一遍，验证收敛钩子
  落快照、维度判定与真实数据一致、失败簇端点数据正确；
- **E2E**：管理端维度卡展示 + 证据跳转 + 候选全流程（生成→回归→发布→回滚）
  的 Playwright 用例；
- **零 LLM 约束**：发布路径与轨 1 回归不产生任何 LLM 调用（测试断言）。

## 9. 风险与取舍

| 风险 | 取舍 |
|---|---|
| 全量落库的写放大 | 一行/attempt + 纯读计算；写失败 best-effort 不阻断主流程；重算自愈 |
| 承诺—行动规则误报 | 保守规则 + 映射不确定降级 uncertain（宁缺勿错）；规则表可配 |
| 双集样本偏差（历史数据即样本） | 学习集/验证集互斥校验；样本人工可调；轨 2 重跑兜底 |
| 正文修改无法离线验证 | 轨 2 小样本重跑强制触发（内容类 edits） |
| 候选生成质量未验证 | 人在两处（手动触发 + 审批发布）；拒绝原因留档反哺 |
| 无灰度形态 | 发布即生效 + 归档回滚 + 7 天效果对比（§5.4 明示） |
