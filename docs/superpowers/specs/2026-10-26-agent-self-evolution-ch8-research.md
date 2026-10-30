# Agent 自我进化研究报告 —— 《深入理解 AI Agent》第 8 章对照本系统

- 日期：2026-10-26
- 素材：《深入理解 AI Agent：设计原理与工程实践》李博杰 v1.3（2026-07，314 页）第 8 章「Agent 的持续进化」（PDF p237–251，书页 229–243）
- 对照对象：check-manage AI Harness（SkillOpt / 执行审计与轨迹分析 / 动作门禁与策略库 / 批任务编排）
- 配套文档：设计文档《2026-10-26-agent-self-evolution-design.md》（同目录）
- 本文所有系统侧引用均为 2026-10-26 代码核实（file:line），非记忆转述。

---

## 1. 研究问题

本系统能力面已有大量「进化原料」：技能拟合（SkillOpt）、轨迹分析、动作门禁、性能分析。第 8 章给出的问题是：**这些原料有没有被组织成一个「从运行经验到新版本 Agent」的受治理闭环？** 本章把该闭环拆为四个可独立评估的问题：

1. 学习信号从哪来 —— 评价优先，而非总结（8.1）；
2. 改变写到哪里 —— 知识 / 指令 / 程序 / 参数四种载体按「能力能否被符号表达」选择（8.2）；
3. 如何长期运行 —— 双循环、分层评估、发布与回滚（8.3.1–8.3.2）；
4. 边界在哪 —— 三类安全边界与睡眠学习（8.3.4–8.3.5）。

## 2. 第 8 章框架提炼

### 2.1 评价优先：三层轨迹验证与结构化诊断（8.1）

- 三层验证器：**结果验证器**（测试/金额/订单真值，回答「事情是否真的办成」）→ **过程验证器**（政策、权限、动作序列，回答「是否以允许的方式办成」）→ **质量验证器**（LLM Rubric 逐项给分+引用证据+证据不足允许 uncertain，回答「是否办得合适」）。越靠下越应依赖代码与环境真值。
- 评价结果**不得压缩成标量**，必须是结构化多维诊断：维度 pass/fail/uncertain + **证据位置**（对话轮次、工具状态）+ 置信度。表 8-1 的维度：任务结果 / 规则遵从 / 隐私边界 / 事实可靠性 / **承诺—行动一致性**（声称做了 vs 工具日志，章里点名最适合 Agent 场景）/ 表达质量 / 合规变通。
- 验证器本身需校准（专家标注集、低置信度二次复核、模型变更重校准）；验证器给评价，**修改哪个部件由独立模块决定**，避免既当裁判又改规则。

### 2.2 四种更新载体（8.2）

| 载体 | 适合承载 | 关键工程要求（章内） |
|---|---|---|
| 知识（8.2.1） | 事实、经验规律、例外 | 三层数据（不可变原始轨迹/单次分析/跨轨迹归纳）；候选规律须有「支持/反驳证据表」并达支持门槛才入正式文档；文档写明适用场景、策略、禁区、例外、来源、最近验证时间 |
| 指令 Prompt/Skill（8.2.2） | 可语言化的判断原则 | **最小 diff（old_str→new_str）**、作用域、与现有规则冲突检查；**边界案例集改善 ∧ 保留集不退化**才发布；只到 release_to_canary 不直接覆盖稳定版；候选 manifest 记来源/根因/预期修复/可能受损；Skill 生成先搜近似能力、局部 patch 优先；Skill 内容须含何时加载/前置条件/步骤/已知陷阱/验证方法+来源轨迹 |
| 程序 工具/工作流/Harness（8.2.3） | 确定性流程与强约束 | 工作流生命周期（捕获→参数化→状态检查→候选独立回放→匹配回放→失效重学）；自我修改走**可证伪的变更契约**（失败证据/推断根因/目标组件/候选修改/预期修复/可能受损+各自用例）；候选生成器输入 = 失败证据 + **必须保留的成功行为** + **此前被拒的修改** |
| 参数 SFT/蒸馏/RL（8.2.4） | 高维感知与隐式策略 | 需清洗、去隐私、留独立回归集、查遗忘 —— 数据量与场景不支持，**本系统明确不做**（章亦称四载体协同而非替代） |

8.2.5 补充一条正交轴：优化对象可以是「产物的内容」，也可以是「产生产物的方法」（ACE/MCE/AFlow/Meta-Harness）。原则：**优先可归因、可回滚的局部修改**；搜索空间越大，可信根（评价器/权限/留出测试在可修改范围之外）越重要。

### 2.3 长期运行闭环（8.3）

- **双循环**：在线执行循环只完成任务并记录证据，不改写正式 Agent；离线进化循环聚合轨迹→诊断根因→生成候选→回归与安全验证→灰度/监控/回滚。两者经版本化经验库连接（Voyager 三件套：自动课程+技能库+环境验证缺一不可）。
- **根因先于载体**（8.3.1）：同一表面问题可能落在不同载体（幻觉=缺知识 or Prompt 没要求引用；虚假承诺=指令 or Harness 一致性检查）；先定位根因，再选最小、最易验证回滚的修改对象；证据不足的偶发故障只积累不触发。载体随证据积累可升级（经验文档→知识→Skill→工具代码→参数）。
- **分层评估，四指标必须拆开**（8.3.2，表 8-3）：

| 指标 | 回答的问题 | 主要证据 |
|---|---|---|
| 候选修改有效率 | 更新器是否提出有价值的修改 | 候选在独立验证中的接受率与增益 |
| 产物激活率 | 任务 Agent 是否在正确场景加载了新 Skill/记忆/工具 | 检索、路由与工具调用轨迹 |
| 遵循成功率 | 激活后是否按新规则执行 | 动作序列与过程验证器 |
| 留出任务增益 | 整体是否改善未参与进化的任务 | held-out 成功率、质量与成本 |

  「更新正确但没被加载」不能判为进化失败 —— 因此要区分 **harness-updating**（产生有价值修改的能力）与 **harness-benefit**（任务模型找到并正确使用修改的能力），用**双向模型替换实验**（固定候选换任务模型 / 固定任务模型换更新器）定位瓶颈。
  长期五类指标：回退 / 泛化 / Token 效率 / 安全 / 长期工程质量。
- **边界**（8.3.3）：结果可自动验证的任务上闭环最可靠；开放任务需结论—证据分离、保留负面结果、维护搜索多样性、人在更高层介入。
- **三类安全边界**（8.3.4）：① 证据与指令隔离（原始网页/工具输出是不可信证据，须经 LLM 总结+异源 reviewer 审阅才能写入）；② 候选与正式隔离（新能力先进不可服务真实流量的候选区，过沙盒/权限/供应链/行为测试才转正）；③ **安全机制不可自我修改**（验证器、测试用例、发布门槛、审计日志、稳定版备份不在可修改范围）。
- **睡眠学习**（8.3.5）五步：触发（时间/数量/错误率门槛+确认无高优在线任务）→ 定向（读正式能力与不可修改边界）→ 采集与整合（合并重复、标记冲突、优先生成局部补丁）→ 验证与审批（迁移/保留/安全集评估，高风险人工批）→ 修剪与索引（陈旧能力标过期/归档/删除，保留来源与回滚版本）。Hermes Curator 案例：跟踪 Skill 使用/陈旧/归档状态，空闲期确定性修剪，变更前快照。

## 3. 系统现状盘点（映射与证据）

以下按「第 8 章闭环部件」逐项映射本系统。总评：**在线执行循环与证据记录基本齐备且质量高；评价层有原料无整合；指令载体的进化闭环是半成品；程序载体有现成容器但无触发通道；评估、治理与安全边界未按第 8 章口径显性化。**

### 3.1 在线循环与证据记录（对应 8.3 在线执行循环）—— ✅ 基本齐备

| 章要求 | 系统现状 | 证据 |
|---|---|---|
| 记录动作、结果与反馈 | `agent_tool_calls` 账本（tool/args_text/state/started_at/duration_ms，幂等键 oc_session_id+part_id） | `server/migrations/2026_09_20_agent_action_gate.py:17-36`、`server/utils/agent_ledger.py:192-261,376-453` |
| 每次发送可追溯（模型、请求） | `ai_execution_attempts` 记 requested_model/effective_model/model_resolution | `server/migrations/2026_09_17_execution_audit_tables.py:46-47`、`server/routes/ai_chat.py:960-990` |
| 消息与工具结果 | `ai_chat_messages.content` JSONB 按 part 累积（tool 结果在 part state） | `server/db_schema/core.py:441-453`、`server/utils/chat_persist.py:129-176` |
| 事件审计 | `ai_execution_events` 含 `gate.evaluated` 等审计事件 | `server/utils/batch_engine.py:1636-1649` |

注意：`agent_tool_calls` 只有 `args_text` 无 result 列；工具结果文本藏在消息 part state 里 —— 做跨轨迹评价时需要补结构化出口（见设计文档 §3.4）。

### 3.2 过程验证器（对应 8.1 中层）—— ✅ 已产品化，且有终态兜底

- 门禁三型 `mode ∈ ('post','pre','checkpoint')`：pre=deny（仅 tool 型，命中即 throw 阻断）、checkpoint=过程注入（before 经 pre-check 端点阻断式注入预算提示，after 经 post-check 端点非阻断追加 prompt）、post=终态核对。`server/utils/agent_ledger.py:521,872-941,944-991,1077-1232`；拦截执行体为自动部署的 OC 插件 `baize-trace.js`（`server/utils/skillopt.py:403-441,447-478`）。
- 期望核对语义：派发前把「策略 ∪ 批 ∪ 模板」合并登记为 `action_expectations` 行（按 apply_to 过滤），终态按 PG 正则对账本计数比对，**inconclusive 一律 fail-closed**；tree 作用域覆盖根会话+全部子代理。`server/utils/batch_engine.py:1797-1823`、`agent_ledger.py:785-808,998-1014`、`batch_engine.py:1613-1633`。
- checkpoint 行可带 `assert_spec`（JSONB）参与终态兜底 —— 相当于第 8 章「过程验证器 + 结果兜底」的组合。`2026_10_11_gate_checkpoint_policy.py:27-28`、`agent_ledger.py:1143-1150`。
- 预算两套：checkpoint 注入预算（max_checks/fired_count 原子消耗）+ 资源预算（ai_execution_budgets，warn/drain/abort）。`agent_ledger.py:915-920`、`server/utils/execution_budget.py:131-176`。
- 命中可见性：期望行 last_status/last_evidence + gate.evaluated 事件 + ai-chat.log 留痕 + 403 告警。`agent_ledger.py:1192-1200`、`routes/ai_gate_internal.py:49-62`。

### 3.3 评价层（对应 8.1 结构化诊断）—— ⚠️ 有原料、无整合

已有的单点评价信号：

| 信号 | 内容 | 位置 |
|---|---|---|
| 门禁核对 | 期望行 passed/failed + 证据 | `action_expectations.last_*`（`agent_ledger.py:1192-1200`） |
| 拟合（遵循率雏形） | ai_skill_fit_results：fit 状态 + diagnosis（cause 五类枚举 + suggestions + revised_steps） | `server/utils/skill_fit.py:278,323-388`、`server/utils/skill_fit_ai.py:47-52,309-386` |
| 性能四段+慢因 | wall/tool/subagentWait/model/idle 互斥拆分 + 主导慢因分布 | `server/utils/perf_analysis.py:69-95,547-661` |
| 声称 vs 观察 | trace_auditor 每步 declared_by_agent vs observed，无证据却声称完成记 `completed_claimed` | `server/utils/trace_auditor.py:177-224` |
| 运行时技能激活 | ai_skill_invocations：source(runtime|heuristic) × evidence_level(confirmed|inferred) × outcome | `2026_09_18_skillopt_p2.py:21-44`、`skillopt.py:135-189` |
| 轨迹分析 | 后台分析会话（kind=trace_analysis）+ trace-analyzer 技能 + analyze_trace/query_sessions MCP 工具 | `routes/ai_session_admin.py:159+`、`mcp-server/tools/analyze_trace.py` |

差距（对照 8.1）：
- **无统一「单次运行」的结构化多维诊断** —— 上述信号分散在五处，没有一张「维度 × 状态 × 证据位置」的诊断记录；现有 `ai_execution_diagnoses` 是分析报告文本，不是维度化诊断。
- **承诺—行动一致性已有雏形（trace_auditor）但未产品化**为评价维度（无 uncertain 语义、无证据位置指针、不进批任务摘要）。
- **质量验证器（Rubric）完全缺位** —— 无逐项给分、无证据引用要求、无 uncertain；「合规变通」「表达质量」维度无承载。
- 验证器校准机制无（无标注集、无一致率统计）。

### 3.4 指令载体：Skill 进化闭环（对应 8.2.2）—— ⚠️ 半闭环，缺发布工程

已有（链路完整）：
- 诊断：`POST /skill-fit/<result_id>/diagnose`（LLM → cause/suggestions/revised_steps 写 `ai_skill_fit_results.diagnosis`，进程内签名缓存，重算自愈）。`routes/ai_session_admin.py:1537-1555`、`skill_fit_ai.py:324-386`。
- 反馈：`POST /analyses/<diagnosis_id>/suggestions/<suggestion_id>/feedback`（五态 accepted/rejected/modified/applied/rolled_back，applied 记 applied_at + before_metrics）。`ai_session_admin.py:705-765`。
- 效果：`GET /skill-suggestions/<id>/effect` = applied_at 前后各 7 天 `ai_skill_invocations` completed 率对比。`ai_session_admin.py:768-811`。
- 版本底座：`ai_skill_def_versions`（content_hash 唯一 + content 正文归档 + 回滚端点写回 hash 必等校验 + compare）。`2026_10_01_skill_fit_tables.py:55-64`、`2026_10_05_def_version_content_archive.py:24-27`、`ai_session_admin.py:1277-1314`。

缺（对照 8.2.2 的发布门槛）：
1. **无候选区** —— 建议一经 apply 直接写回正式步骤，无 release_to_canary 语义（8.2.2 明确「通过门槛只得到 release_to_canary，不直接覆盖稳定 Prompt」）。
2. **无双集回归门槛** —— 无「边界案例改善 ∧ 保留集不退化」的自动验证；当前唯一闸门是人工。
3. **非最小 diff** —— revised_steps 是整块重写，无 old_str→new_str、无作用域、无与现有规则冲突检查。
4. **无候选 manifest** —— 诊断有 result_id 来源，但建议缺「预期修复/可能受损/来源轨迹清单」字段（8.2.3 的可证伪变更契约同样缺）。

### 3.5 程序载体：策略库与提炼（对应 8.2.3）—— ⚠️ 容器现成、触发通道缺失

- `gate_policies`（checks JSONB 全量校验：check_type/tool/args_pattern/min_count/require_state/scope/mode/prompt/timing/max_checks/assert_spec/apply_to/effect_spec），绑定批任务即生效 —— 这正是第 8 章「把经验编译为程序」的现成载体。`2026_10_11_gate_checkpoint_policy.py:29-40`、`agent_ledger.py:565-748`、`gate_policy_repo.py:152-186`。
- **确认不存在**「失败轨迹 → 自动建议生成策略」通道：`gate_policies` 唯一写入方是人工 CRUD；`ai_execution_diagnoses`（审计诊断报告）无任何消费方回写策略。已核实全仓无相关路径。
- 唯一相邻机制：`action_check_extractor.extract_action_checks` —— 但输入是**派发前的任务文本+Agent 配置+Skill 文档**，不是失败轨迹，且红线明确「只产出建议，执行侧无增删期望路径」。`server/utils/action_check_extractor.py:88-147`、`routes/ai_chat_batches.py:264-275`。
- 即：8.2.3 要求的「跨轨迹支持门槛达到后建议生成程序补丁」的触发通道为 0，而容器（策略库+校验+审计）与失败证据（期望行 failed + 账本）都已就位 —— 这是**最短路径缺口**。

### 3.6 评估口径（对应 8.3.2 表 8-3）—— ⚠️ 四指标三缺一混

| 表 8-3 指标 | 系统现状 |
|---|---|
| 候选修改有效率 | 无候选区故无此口径；最接近的是 def_versions.avgScore（聚合值非验证接受率） |
| 产物激活率 | **原料齐但没当指标用**：ai_skill_invocations runtime/confirmed 行即「真被加载」证据，与 manifest 声明（inferred）可对比 |
| 遵循成功率 | fitRate 是唯一聚合分（拟合口径 37c7057 起只认 runtime 证据），语义上≈表 8-3 第三行 |
| 留出任务增益 | 无留出集概念；attempts.effective_model/requested_model 已记录，双向替换实验有数据基础未做 |

### 3.7 治理与安全（对应 8.3.4/8.3.5）—— ⚠️ 部分机制存在、边界未明文

- 版本归档/回滚（技能步骤）已有；**知识载体整体缺位** —— 系统无「经验知识文档」这一层，第 8 章 8.2.1 的三层数据（不可变轨迹→单次分析→跨轨迹归纳文档）只实现了前两层（原始消息 + 单次诊断/拟合结果）。
- 无 Curator 式消费：def_versions 时间序列（avgScore/fitRate）没人用来做重复/陈旧/淘汰判断；技能库无修剪（8.3.5 第 5 步缺）。
- 安全边界未明文：证据与指令隔离事实上部分成立（内部 token 隔离、提取器建议需人工确认、fail-closed），但「候选与正式隔离」「安全机制不可自改」没有成文清单，未来接入自动进化流程时无护栏可依。
- 无睡眠学习式离线整合调度（现有定时任务多为 reconcile/治愈性质，不产出候选更新）。

## 4. 结论：优化点清单（O1–O6）

按「第 8 章闭环完整性 × 现有资产复用度」排序：

- **O1 统一运行诊断**（前置，其余一切的地基）：把账本+门禁终态+拟合+性能+声称核对聚合成单次运行的结构化多维诊断（维度 pass/fail/uncertain + 证据位置 + 置信度），承诺—行动一致性升格为正式维度（trace_auditor 现成）。对应 8.1。
- **O2 技能进化闭环补全**（核心）：候选区 + 最小 diff + 来源 manifest（可证伪变更契约）+ 双集回归（边界改善 ∧ 保留不退化，离线复用拟合匹配器打分，零 LLM）+ 发布/回滚复用现有版本归档。对应 8.2.2。
- **O3 失败簇触发程序更新**（最快见效）：跨轨迹聚合 failed 期望（同 tool+args 模式达支持门槛）→ 生成 gate_policies 策略草稿进人工审批 —— 载体、校验、审计全部现成，唯一新建的是触发通道。对应 8.2.3。
- **O4 评估拆分**（纯读、改动最小）：按表 8-3 拆四指标视图；激活率直接用 invocations 二维口径；留出集与双向模型替换用 attempts.model 字段起步。对应 8.3.2。
- **O5 睡眠学习与修剪**：Curator 式离线整合（陈旧检测/重复合并/归档建议），消费 def_versions 时间序列。对应 8.3.5。
- **O6 安全边界明文化**：三类边界成文并落为代码约束（LLM 产物只进候选区；验证器/策略校验/发布门槛/审计日志列入不可修改清单）。对应 8.3.4。

明确不做：参数层（8.2.4）与「更新更新方法」层（8.2.5 的 ACE/MCE/Meta-Harness）—— 数据与评估成本不支持，章内亦主张优先局部可归因修改。

**推荐**：P1 = O1+O2（互为依存：O2 的回归门槛与 manifest 消费 O1 的诊断；O1 单独交付无消费方）。O3 依赖 O1 轻量版可与 P2 并行；O4 可随时插入。详细设计见配套设计文档。
