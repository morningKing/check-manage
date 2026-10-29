# Agent 自我进化落地设计（P1：统一运行诊断 + 技能进化闭环）

- 日期：2026-10-26（2026-10-27 依开源对照研究修订 R1–R4，见 §4.6；2026-10-28 P1 已实施，分支 feat/agent-evolution-p1）
- 前置研究：《2026-10-26-agent-self-evolution-ch8-research.md》（第 8 章对照与本系统差距盘点，O1–O6）；开源对照与优化决议：《2026-10-27-agent-self-evolution-oss-benchmark.md》
- 范围决策：P1 详设 **O1（统一运行诊断）+ O2（技能进化闭环补全）**；O3/O4 给 P2 概要；O5/O6 给 P3 路线。参数层与「更新更新方法」层（8.2.4/8.2.5）明确不做。
- 设计原则（来自第 8 章，全文约束）：候选与正式隔离；LLM 只产候选、确定性逻辑做合并与判定；评价不压缩成标量；先根因后载体；安全机制不可自改。

---

## 1. 目标与非目标

### 目标（P1 完成后可验证）

1. 一次运行（attempt）能出一张**结构化多维诊断**：维度 ∈ {任务结果, 规则遵从, 承诺—行动一致性, 技能拟合, 性能}，各维度 pass/fail/uncertain + 证据位置（表/行/轮次指针）+ 摘要；整体结论四态 success/partial/fail/uncertain。
2. 拟合建议从「整块重写直接生效」升级为「**最小 diff 候选 → 双集回归 → 人工确认发布**」：候选不进正式步骤，回归含边界集改善 ∧ 保留集不退化两项门槛，manifest 记录来源/根因/预期修复/可能受损。
3. 发布与回滚复用现有 `ai_skill_def_versions` 归档与 rollback 端点，不另起版本机制。

### 非目标

- 不自动发布任何候选（人工确认是 P1 的固定闸门，自动化留到 P3 讨论）。
- 不新建知识文档载体（8.2.1 经验知识库留 P2/P3 评估）。
- 不改门禁/策略库写入路径 —— `gate_policies` 与 `action_expectations` 的执行语义保持不变，O1 只读它们。
- 不动拟合口径（37c7057：只认 runtime 证据）。

## 2. 总体架构

对照第 8 章双循环，本系统落位：

```
在线执行循环（现状保留）                    离线进化循环（P1 新建/补全）
┌────────────────────────┐          ┌──────────────────────────────────┐
│ 批任务/交互会话执行      │          │ run_reconcile 扩展：              │
│  agent_tool_calls 账本  │  证据 →  │  ① 聚合维度信号 → ai_run_diagnoses │
│  action_expectations 门禁│ ──────→ │  ② 拟合诊断 → 最小 diff 候选       │
│  ai_skill_invocations   │          │     → ai_skill_candidates（候选区）│
│  ai_chat_messages       │          │  ③ 双集回归（离线打分，零 LLM）    │
└────────────────────────┘          │  ④ 人工确认 → apply（现有链路）    │
                                    │  ⑤ effect 复用（±7d 对比）        │
                                    └──────────────────────────────────┘
可信根（不可被进化流程修改）：gate_policies 校验逻辑、回归门槛计算、发布端点鉴权、
ai_execution_events 审计、def_versions 归档与 rollback —— 全部为服务端确定性代码。
```

关键取舍：**离线整合不新起调度器**，挂在现有 reconcile 体系上（批任务 finalize 后已有 `_check_action_gate` 与 reconcile 钩子），新增一步「run 诊断聚合」与「候选生成建议」；交互会话由管理端手动触发（与轨迹分析同模式）。

## 3. O1 统一运行诊断

### 3.1 数据模型

新表 `ai_run_diagnoses`（迁移文件 `server/migrations/2026_10_27_run_diagnoses.py`）：

```sql
CREATE TABLE IF NOT EXISTS ai_run_diagnoses (
  id            SERIAL PRIMARY KEY,
  attempt_id    INT NOT NULL REFERENCES ai_execution_attempts(id),
  root_session_id UUID,
  overall       VARCHAR(20) NOT NULL,          -- success|partial|fail|uncertain
  dimensions    JSONB NOT NULL,                -- 见 3.2
  generator_version VARCHAR(20) NOT NULL,      -- 诊断器版本，重跑可对比
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (attempt_id, generator_version)
);
CREATE INDEX IF NOT EXISTS idx_run_diag_root ON ai_run_diagnoses(root_session_id, created_at DESC);
```

`dimensions` JSONB 结构（每维度一对象，**始终包含全部五个维度键**，缺证据时 status=uncertain —— 8.1 的「允许拒绝评分」）：

```jsonc
{
  "task_result":    {"status": "pass|fail|uncertain", "summary": "...",
                     "evidence": [{"type": "gate_expectation", "id": 12, "hint": "args_text ~ '...'"}]},
  "rule_compliance":{"status": "...", "evidence": [{"type": "gate_expectation"|"gate_event", ...}]},
  "promise_action": {"status": "...", "evidence": [{"type": "message_part", "session_id": "...",
                                                     "part_id": "...", "kind": "completed_claimed"}]},
  "skill_fit":      {"status": "...", "evidence": [{"type": "fit_result", "id": 34, "skill": "..."}]},
  "performance":    {"status": "...", "evidence": [{"type": "perf_attempt", "attempt_id": 34,
                                                     "dominant_cause": "subagentWaitMs"}]}
}
```

### 3.2 维度来源映射（全部复用现有数据，O1 本身零 LLM）

| 维度 | 判定来源（确定性） | 状态规则 |
|---|---|---|
| 任务结果 | 批路径：子任务终态 + `_check_action_gate` 结果（batch_engine.py:1825-1865）；交互路径：会话 status + 有无 error | gate 失败→fail；子任务 completed 且无 gate 数据→uncertain（不冒充 pass） |
| 规则遵从 | `action_expectations` 按 attempt 关联行的 last_status 聚合 | 任一 failed→fail；有行且无 failed（未核对的 pre/checkpoint 行不阻塞，2026-10-28 T2-4a 裁定）→pass；无行→uncertain |
| 承诺—行动一致性 | 读持久化表 `ai_execution_step_results`（trace_auditor 写入，trace_auditor.py:429-448）：存在 `completed_claimed`（声称完成无工具证据）→fail；有 todo 声称且全部有证据→pass；**无行时 lazy-fill**——best-effort 调既有 `ensure_diagnosis_for_session`（trace_auditor.py:363，确定性审计零 LLM）现算补齐，仍无→uncertain | 证据指针落到 step 行（step_id/status），前端可跳转 |
| 技能拟合 | `ai_skill_fit_results` 中该 attempt 的行 | diverged→fail；partial→warn（计 partial）；aligned→pass；无拟合行→uncertain |
| 性能 | `perf_analysis.attempt_payload`（perf_analysis.py:879）四段占比 + 慢因 | 主导慢因占比 > 阈值（首版 60% wall 且超会话 P75 耗时）→fail，其余 pass；无耗时数据→uncertain |

overall 聚合规则：任一 fail→fail；无 fail 且任一 partial→partial；全 uncertain→uncertain；否则 success。**fail 的维度必须在 summary 里带证据位置**，无证据的 fail 判定视为生成器 bug（校验拒绝写入）。

### 3.3 生成时机与端点

- 批路径：`BatchWorker` finalize 后现有 reconcile 钩子处追加 `build_run_diagnosis(attempt_id)`（新函数置于 `server/utils/run_diagnosis.py`），同事务写 `ai_run_diagnoses`，幂等 upsert（UNIQUE 键冲突则按 generator_version 更新）。
- **lazy-fill（2026-10-27 用户认可）**：`ai_execution_step_results` 现仅管理端打开执行审计时按需生成（ai_session_admin.py:512/636）；聚合器在承诺—行动维度读到无行时，best-effort 调既有 `ensure_diagnosis_for_session` 现算补齐再读——零新增埋点（复用既有确定性 writer），finalize 路径多一次轻量审计计算；只在无行时触发，已有行不重算。
- 交互路径：管理端 `POST /ai/chat/admin/attempts/<aid>/diagnose-run` 手动触发（与现有 `GET /perf/attempts/<aid>/diagnosis` 并存，后者保留为性能单维视图）。
- 读取：`GET /ai/chat/admin/attempts/<aid>/run-diagnosis`；列表视图 `GET .../run-diagnoses?root_session_id=` 供会话详情页时间线。
- 前端：`SubtaskPerfReport` 抽屉顶部新增「运行诊断」分区（五维度状态条 + 证据跳转）；批任务页任务行加诊断徽标（沿用 AdminChildGate 徽标列模式）。

### 3.4 实现注意（来自代码核实的坑）

- `agent_tool_calls` 无 result 列，承诺—行动一致性只能判「有无同意图工具调用」，不能判「调用结果是否如声称」—— 声称与结果比对依赖消息 part state，首版只做 completed_claimed 检测，深化留待 result 结构化（登记为后续项，不阻塞）。
- LLM 质量验证器（表达质量/合规变通 Rubric）**P1 不做**：需要标注集校准，先以 uncertain 语义占位（维度键保留，status=uncertain），避免未校准的 Judge 信号污染诊断 —— 这正是 8.1「低置信度不进学习信号」的要求。
- Mimosa 约束：SQL 变量不得中转，execute 首参字面量内联（先例见性能分析慢因聚合）。

## 4. O2 技能进化闭环补全

### 4.1 数据模型

新表 `ai_skill_candidates`（迁移 `2026_10_27_skill_candidates.py`）：

```sql
CREATE TABLE IF NOT EXISTS ai_skill_candidates (
  id              SERIAL PRIMARY KEY,
  def_kind        VARCHAR(30) NOT NULL,        -- 沿用 ai_skill_def_versions.def_kind
  def_name        VARCHAR(200) NOT NULL,
  base_content_hash VARCHAR(64) NOT NULL,      -- 候选所基于的正式版本 hash
  edits           JSONB NOT NULL,              -- [{"old_str","new_str","reason"}] 最小 diff 列表
  edits_hash      VARCHAR(64),                 -- canonical edits JSON 的 sha256（R2 去重键）
  fallback_content TEXT,                       -- 无法表达为 diff 时的整块内容（显式降级，UI 标注）
  source          JSONB NOT NULL,              -- manifest（见 4.2）
  validation      JSONB,                       -- 双集回归结果（见 4.3）
  status          VARCHAR(20) NOT NULL DEFAULT 'pending',
                  -- pending → validated | rejected；validated → promoted | expired
  created_by      VARCHAR(60) NOT NULL DEFAULT 'fit-diagnose',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  decided_at      TIMESTAMPTZ,
  decided_by      VARCHAR(60),
  promoted_version_id INT REFERENCES ai_skill_def_versions(id)
);
CREATE INDEX IF NOT EXISTS idx_skill_cand_lookup ON ai_skill_candidates(def_kind, def_name, status);
```

状态机：`pending`（LLM 建议落候选，未验证）→ `validate` 动作跑双集回归 → `validated`（两项门槛过）/ `rejected`（门槛不过或人工拒绝）→ `promote` 走现有 apply 链路 → `promoted`；`expired` = base_content_hash 已被别人推进（conflict 防护，promote 前校验最新 hash 仍等于 base）。

### 4.2 manifest：可证伪变更契约（对应 8.2.3 的字段要求）

`source` JSONB 固定字段（LLM 诊断 prompt 同步改造产出，skill_fit_ai.py 的诊断 schema 扩展）：

```jsonc
{
  "fit_result_id": 34, "diagnosis_id": "...", "suggestion_id": 12,
  "cause": "missing_validation",            // 沿用五类枚举
  "evidence_traces": [123, 456],            // 支持该诊断的 attempt_id 列表
  "expected_repair": "边界场景 X 应命中步骤 3（旧版缺前置校验）",
  "possibly_harmed": ["正常路径中已可跳过校验的场景可能被强制重跑"],
  "scope_note": "仅影响 def_name=股票分析 的步骤 3 附近"
}
```

- `expected_repair` / `possibly_harmed` 由诊断 LLM 产出（prompt 增两字段），**回归用例从 evidence_traces 派生**（见 4.3），使「预期修复/可能受损」各自有可执行用例 —— 即章内「分别验证两者的用例」。
- `evidence_traces` 直接取产生该 fit_result 的 attempt 及同签名近邻 attempts（同 def_name 同 fit 状态），不做跨技能泛化。

### 4.3 最小 diff 与双集回归

**最小 diff 改造**：诊断 prompt 要求 LLM 以 `[{old_str,new_str,reason}]` 输出（对当前正式步骤正文做精确替换）；服务端用现有 bytes 回读路径（`ai_session_admin.py:1491-1510` 的 apply 逻辑）校验每个 old_str 唯一命中且替换后 hash 变化。无法满足唯一性时整块降级写 `fallback_content` 并在 UI 显式标注「非最小 diff」（8.2.2 允许整块重写存在，但要求可见）。

**双集回归（零 LLM，核心新逻辑 `server/utils/skill_candidate_eval.py`）**：

- 匹配器复用：把 `compute_attempt_fit` 内部的「步骤 ↔ 轨迹 manifest 匹配」逻辑参数化，抽出 `score_steps_against_attempts(def_kind, def_name, steps_content, attempt_ids) -> fit 分布`（当前实现与 manifest hash 耦合在 skill_fit.py:323-335，需小幅重构，不改对外口径）。
- **边界集**：`ai_skill_fit_results` 中该 def_name 状态 ∈ {partial, diverged} 的近期 attempts（≤20 条，按 created_at 降序）—— 即「触发这次诊断的同族失败案例」。
- **保留集**：该 def_name 状态 = aligned 的近期 attempts（≤20 条）—— 即「当前版本已成功、不许被改坏的场景」。
- 门槛（对应 8.2.2 发布四条件中的两条，另两条=diff 非空与来源可追溯在候选结构层强制）：
  - 边界集：候选步骤 fit ≥ 现行步骤 fit（逐条比较，提升条数 ≥ 1 且无恶化条数 > 边界集 20%）；
  - 保留集：候选步骤 fit 无一条从 aligned 跌出（**不退化是硬门槛**）。
- 结果写入 `validation` JSONB：两集各自的 per-attempt {before, after} + 结论 + 集合构成快照（attempt_ids + 拟合口径版本），保证可复核可复算。
- 无法凑齐集合（如边界集 <2 条）→ validation 标记 `insufficient_evidence`，候选停留 pending 并提示积累样本（8.3.1「证据不足的偶发故障不触发学习」）。样本量门槛：边界集 ≥2 且保留集 ≥3 才允许 validated。

### 4.4 发布、回滚与效果

- `POST /skill-candidates`（内部：feedback action=applied 时自动创建候选并返回 candidate_id；现有 feedback 端点 ai_session_admin.py:705-765 增加这一步，不改变原响应结构、只追加字段）。
- `POST /skill-candidates/<id>/validate`（跑 4.3）、`POST /skill-candidates/<id>/promote`（校验 status=validated 且 hash 未漂移 → 组装正文走现有 `POST /skill-def-steps/apply` 内部函数，落 `ai_skill_def_versions` 归档 + register_def_version，回填 promoted_version_id）、`POST /skill-candidates/<id>/reject`。
- 回滚：完全复用现有 `POST /skill-def-versions/<version_id>/rollback`（hash 必等校验已有），rollback 时若存在该 def_name 的 validated 候选，自动置 expired。
- 效果：`GET /skill-suggestions/<id>/effect`（±7d invocations completed 率）不改，promote 时把 candidate_id 写入 feedback.meta 供关联；后续 O4 在此基础上补激活率口径。
- 权限：三个动作端点全部 `admin.ai_chat_admin`（与现有 skill-def-steps/apply 同级）；候选生成不需要权限（内部自动 + 手动触发诊断共用）。

### 4.5 前端（拟合优化页内闭环）

`src/components/admin/skillopt/` 下新增 `SkillCandidates.vue`：

- 列表：状态徽标（pending/validated/rejected/promoted/expired）+ 来源（fit_result 可跳诊断详情）；
- 详情抽屉：diff 视图（old_str→new_str 高亮，fallback_content 标注「整块降级」）+ manifest 四件套（根因/证据轨迹/预期修复/可能受损）+ 回归结果双集对比表；
- 动作按钮：验证 → 发布/拒绝（promote 需二次确认，显示目标版本与回滚入口）。
- 诊断建议卡片上「应用」按钮行为改为「生成候选」（文案与跳转同步调整）。

### 4.6 开源对照修订（2026-10-27，R1–R4）

依《2026-10-27-agent-self-evolution-oss-benchmark.md》的对照结论补强（详细依据见该文 §3/§4）：

- **R1 诊断输入补「被拒修改 + 成功约束」**（Self-Harness 原则，第 8 章 8.2.3）：`diagnose_result` 组装材料时加载该定义最近 ≤5 条 rejected/expired 候选（old_str 清单 + rejectReason）与保留集（aligned）当前步骤清单，注入 prompt——禁止换说法重复提交、明确哪些成功行为不能破坏。
- **R2 候选去重**：`edits_hash` 列（canonical edits JSON 的 sha256）；同 (def_kind, def_name, base_content_hash, edits_hash) 且 status ∈ (pending, validated) 的重复创建返回 409 + 既有 candidateId。
- **R3 apply_edits 结构守卫**：替换后新旧 `fit.steps` 的**步骤 id 集合必须相等**（禁止增删步骤）；任一步骤 expect 条目收窄时要求 source.expected_repair 非空——「作用域+冲突检查」落成确定性规则，不交给 LLM 判。
- **R4 修改分类 mechanical/behavioral 双模回归**（skill-creator「主观输出不强行加断言」启发）：edits 完全不触及 frontmatter `fit.steps` 区（纯正文行为指导）的候选走 behavioral 模式——回归打分对它必然无变化、会被「无改善」误杀，改为 validation 记 `{mode:'behavioral'}` 直接 validated，**人工 promote 仍是唯一发布闸门**；触及 steps 的照旧 mechanical 双集回归。

刻意不采纳并留档的对照项：Langfuse 式 production/staging label 部署解耦（单文件技能模型下回滚已秒级、无流量可切）；GEPA 式候选池自动择优（人工门下收益低，记 P3）；Hermes pending 暂存审批区（promote 即审批，等价）。

## 5. P2 概要（O3 + O4）


- **O3 失败簇 → 门禁策略建议**：离线聚合跨 attempt 的 failed `action_expectations`（同 tool + args_pattern 归一化 + scope），支持门槛 ≥3 次跨 ≥2 个 root session 触发「策略草稿」—— 复用 `gate_policies` 的 `validate_checks` 全量校验（agent_ledger.py:565-748）做草稿合法性预检，产出**建议态草稿**（不入正式表，新列 status=draft 或旁表），进 `GatePolicies.vue` 审批队列；同时把「必须保留的成功行为」（同 tool 的 passed 期望样本）附在草稿上 —— 8.2.3 候选生成器三输入中的两项。依赖 O1 的聚合视图（轻量版：仅用其期望聚合，不依赖全维度）。
- **O4 评估拆分**：`GET /ai/chat/admin/evolution/metrics` 只读视图 —— 候选有效率（candidates promoted / validated，O2 交付后即有）；激活率（def_name 维度：runtime invocations 数 / 技能加载可见次数，二维护度已在 invocations 表）；遵循率（现 fitRate 降级改名）；留出增益（首版：按 def_name 最近 N aligned attempts 中随机冻结 30% 为伪留出集，对比版本切换前后；双向模型替换用 `ai_execution_attempts.effective_model` 分组同口径）。全部纯查询，无写路径。

## 6. P3 路线（O5 + O6，仅方向）

- O5 Curator：def_versions 时间序列 + invocations 最近使用时间 → 陈旧/重复技能报告与归档建议（归档动作复用版本机制，不物理删除）；睡眠学习调度 = 空闲期批量跑 O1 诊断聚合 + 候选验证，产品化「触发门槛」（新增轨迹数/错误率）。
- O6 安全边界明文化：在 `docs/design/ai/` 增补「进化安全边界」章节并落代码常量 —— 不可自改清单（gate_policies 校验、回归门槛计算、发布端点鉴权、ai_execution_events、def_versions 回滚）、证据与指令隔离现状梳理（内部 token、提取器人工确认、MCP read 分组）、LLM 产物准入白名单（只允许写入 ai_skill_candidates / 未来知识候选区）。

## 7. 测试与验收

按用户测试要求：真实链路 Playwright E2E + 可复核证据；新用例先在实现前编写并确认失败。

- **O1**：`e2e/ai-full/run-diagnosis.spec.ts` —— 批任务真链路跑一个带门禁期望的任务（含一条注定 failed 期望），断言 run-diagnoses 行生成、维度状态符合规则表（rule_compliance=fail、skill_fit=uncertain 当无拟合、overall=fail）、证据 id 可回查；交互路径手动触发用例；重跑幂等（同 attempt 两行不同 generator_version 或更新不重复）。
- **O2**：`e2e/ai-full/skill-candidates.spec.ts` —— mock LLM 诊断产出最小 diff（diff 可唯一命中）→ 断言候选 pending 且正式步骤未变；validate 用预置边界/保留集夹具断言门槛判定（含「保留集退化必须 rejected」的否定用例）；promote 后 def_versions 出新版本且内容 hash 一致、rollback 可逆；hash 漂移场景 promote 被 409 拒绝。
- **回归口径**：现有 skill-fit/fit 口径测试（37c7057）必须全绿 —— 匹配器参数化重构不得改变 `compute_attempt_fit` 对外结果。
- **验收对照第 8 章**：高 overall 分不得掩盖 rule_compliance fail（维度独立）；「更新正确但没被加载」不判进化失败（effect 只看激活前后，不作为 promote 门槛）；无证据的 fail 判定被生成器校验拒绝。

## 8. 分期与工作量

| 阶段 | 内容 | 估算 |
|---|---|---|
| P1a | O1 表+生成器+端点+前端分区 | 后端 1.5d，前端 0.5d，E2E 0.5d |
| P1b | O2 候选表+diff 改造+匹配器参数化+双集回归+端点 | 后端 2.5d（匹配器重构单独提交），前端 1d，E2E 1d |
| P1c | effect 关联、过期联动、文档（docs/design/ai/08 增补） | 0.5d |
| P2 | O3 触发通道 + O4 指标视图 | 各 1–2d（依赖 P1a 聚合视图） |

## 9. 风险与开放问题

1. **匹配器参数化**是 O2 唯一动存量口径的重构 —— 必须单独 commit + 全量拟合回归测试护航；若发现匹配逻辑与 manifest 耦合过深，降级方案：候选回归改用「正文 diff 相似度 + 人工确认」，门槛语义不变但自动性下降。
2. 双集回归用历史轨迹打分衡量「未来表现」，存在分布漂移风险 —— 门槛刻意保守（保留集零退化硬门槛、样本量下限、insufficient_evidence 不放行），并在 validation 快照里留口径版本以便事后重算。
3. `agent_tool_calls` 无 result 列限制承诺—行动一致性深度 —— 已在 3.4 登记后续项；若 O3 需要更深证据，优先给账本补 result 摘要列而非解析消息 JSONB。
4. 候选区会积累未决项 —— 列表页默认按状态过滤并显示 pending 账龄，>14 天自动置 expired（伴随 base 漂移检测），避免「候选坟场」。
