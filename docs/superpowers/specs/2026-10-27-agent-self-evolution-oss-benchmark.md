# Agent 自我进化：开源成熟方案对照研究与 P1 优化决议

- 日期：2026-10-27
- 目的：在实施 P1（`docs/superpowers/plans/2026-10-27-agent-self-evolution-p1.md`）前，对照市面开源/成熟的 agent 自迭代方案，检验 P1 设计（`2026-10-26-agent-self-evolution-design.md`）是否还有可优化点。
- 结论先行：**P1 的骨架（候选区 + 双集回归 + 人工发布 + 版本归档）与业界成熟实践一致，方向不改**；但对照发现 **4 处可低成本采纳的补强**（已回灌设计文档与计划）和一批 P2/P3 设计输入（记录在 §4/§5）。

## 1. 研究对象与方法

选取标准：开源可查证、机制与「技能/提示词/记忆的持续迭代」直接相关、有生产或准生产采用。深读源文档的有：Hermes Agent（Nous Research，Curator/Skills/Memory 三篇官方文档）、Anthropic skill-creator（anthropics/skills 仓库 SKILL.md 全文）；机制核实的有：ACE（斯坦福，增量上下文演化）、GEPA（gepa-ai/gepa，反思式提示进化）、Mem0（记忆四态决策）、Zep/Graphiti（时序知识图失效）、Langfuse（提示词版本化发布先例）。Voyager/Reflexion/DSPy 等第 8 章已覆盖的不重复展开。

## 2. 方案剖析

### 2.1 Hermes Agent（Nous Research）—— 与本系统最接近的完整实现

**Curator（技能生命周期后台维护）**，机制细节：
- **触发**：非 cron 而是空闲门控（距上次 ≥7 天 且 agent 空闲 ≥2 小时；会话启动/网关 housekeeping/serve 每小时计时器检查）；首次运行延迟一个完整周期让用户先 review；支持 dry-run。
- **两阶段**：①确定性转换（无 LLM，默认开）：14 天未用→stale，30 天未用→移入 `.archive/`；被 pin 或被任何 cron 引用的跳过；**宽限下限——use_count==0 的技能必须等到 stale_after_days 年龄才可归档（「零使用是证据缺失，不是使用证据」）**。②LLM 合并（默认关，显式 `consolidate: true`）：单次 aux-model pass（约 50–100 次调用）逐个决定 keep/patch/合并为 umbrella/归档；候选只含可写技能，bundled 与禁用技能永不出现在候选中；合并视技能为完整包（references/templates/scripts/assets 要么整体保留、要么整体迁移重写路径、要么整包归档）。
- **管辖范围判定**：仅管 `created_by: "agent"` 的技能；前台创建与手写的不受管；`curator adopt` 手动移交。
- **数据结构**：`.usage.json` sidecar（use_count/view_count/patch_count/last_used_at/state/pinned）；**append-only 审计台账 JSONL**（actor/action/evidence/前后 {path, sha256}，内容按哈希去重存 blobs，超 5MB 先去重重写、最新条目永存；只做遥测不做门禁）；每次 run 产 run.json + REPORT.md。
- **安全**：**永不清除**（最坏是归档，可 restore；restore 拒绝重名上游技能）；consolidation 前自动 tar.gz 快照、rollback 前再打 pre-rollback 快照（可正向撤销撤销）；**单条目回滚**（按 ledger entry-id 从 blob 精确恢复，先写安全条目失败即 fail-closed）；pin 同时挡 curator/LLM review/agent 删除；archive TTL purge 需显式命令且先入台账；hub/bundled 技能永不触碰；并发竞争是已知限制。

**Skills 系统**：
- 渐进式披露三级加载（列表≈3k token → 正文 → 引用文件按需），**保护 prompt 缓存**：会话中途安装的技能只发短通知，系统提示词下次压缩才更新。
- **后台复盘创建技能的触发条件**（写进系统提示词）：完成值得复用的多步工作流／走弯路报错后找到可行路径／被用户纠正。「记录经验而非流水账」：陷阱写成通用规则+一句机制说明，禁 PR/issue 编号、日期、聊天引用；linter 三形态警告（incident-log-shape / references-sprawl >60 文件 / oversized-body >24k 字符），只警告不阻断。
- **写入审批门控 `write_approval`**：默认关；开启后所有 skill 写操作暂存到 pending 目录，`/skills diff <id>` 看统一 diff、approve/reject 处理。
- 版本管理：hub 安装记来源哈希（lock.json），本地改过的技能上游更新自动跳过防覆盖。

**Memory**：
- 双文件有界记忆（MEMORY.md 2200 字符 / USER.md 1375 字符），**会话启动注入的是冻结快照，会话中写盘不进当前 prompt（保护缓存）**；达到上限时工具报错并要求同一批次「删旧+加新」，只查最终结果；**精确重复条目自动拒绝**。
- `session_search` 走 SQLite/FTS5 返回**原始消息**（无 LLM 摘要，检索与生成不混成不可审计步骤），约 20ms 零 LLM 成本。
- **排障要点：模型口头说「已记住」不算数，只有真调用工具落盘才算**——小模型常见「确认但没写」。
- 记忆写入同样有 `write_approval` 暂存审批。

### 2.2 Anthropic skill-creator（anthropics/skills）—— 候选评估循环的参照系

- **起草→测试→评价→修订**连续序列：访谈捕捉意图 → 判断技能属于**客观可验证**（文件转换/数据提取，需要测试）还是**主观**（写作风格，不强行加断言）→ 起草 → 测试 → viewer 评审 → 改进。
- **测试的对照设计**：每个用例并行跑「带技能」vs「基线」——新技能用无技能基线，**改进旧技能则先 snapshot 旧版本作基线**；评分字段强制 `{text, passed, evidence}`；聚合出 mean±stddev 的 benchmark；**分析师检查专门找两类垃圾：不区分技能的断言、高方差 flaky 用例**。
- **改进原则**：从反馈泛化（技能会被海量 prompt 使用，只对几个测试样例有效=失败）；保持精简；解释 why 而非堆 MUST；发现重复工作就打包成脚本。
- **触发描述优化**：生成 20 条真实查询（should/shouldn't trigger 各半，负面用例须是 near-miss），**60/40 训练/测试分割、按测试分数选优防过拟合**。

### 2.3 ACE（Agentic Context Engineering，斯坦福）—— 增量演化的教科书

Generator（执行）/ Reflector（评价提炼教训）/ Curator（**增量 delta 更新**）三角色流水线；上下文维护成**带稳定标识符的条目集合**，逐条增改而非整块重写——避免「context collapse」和「brevity bias」（多轮重写越改越短、丢细节）；确定性逻辑做合并去重。开源实现 ace-agent/ace。

### 2.4 GEPA（gepa-ai/gepa）—— 候选池与选择策略

反思式进化：维护**候选池**，按**每个验证项各自的最优**构成 Pareto 前沿，反复选一个候选去改进/变异；比 GRPO 省 35× rollouts。DSPy 已内置为优化器。要点：不是「单候选过关制」，而是「多候选共存、逐项比较、择优保留」。

### 2.5 Mem0 与 Zep/Graphiti —— 冲突处理的两种范式（知识载体 P2 输入）

- **Mem0**：抽取候选事实 → 相似检索 → LLM 四态决策 **ADD/UPDATE/DELETE/NOOP** → 落库；另有单遍 ADD-only 便宜模式（只增不改，靠陈旧化容忍）。
- **Zep/Graphiti**：事实=带有效期区间的时序边；新事实入图时检索矛盾边，**失效旧边而非删除**（双时序，历史可查「T 时刻什么是真的」）。
- **共同警示**：冲突判定本身是 LLM 调用，弱模型会「静默漏失效」（Graphiti issue #1666：stale facts survive their own contradiction）——**能用确定性规则判的冲突不要交给 LLM**。

### 2.6 Langfuse —— 版本化发布的工程先例

版本 + **label（production/staging）**解耦「内容版本」与「部署版本」：晋升=把 production label 移到新版本，运行时按 label 取用；数据集实验（experiments）作为晋升前的评估门禁；版本变更支持 webhook 通知。开源（MIT 核心）可自托管。

## 3. 对照矩阵：P1 设计 vs 业界实践

| 能力面 | 业界做法 | P1 现状 | 判定 |
|---|---|---|---|
| 候选与正式隔离 | Hermes pending 暂存审批；Langfuse staging label | 候选表 + 人工 promote，LLM 产物永不直接写文件 | ✅ 等价（promote 即审批，无需暂存区） |
| 最小 diff | ACE 增量 delta + 稳定标识符；第 8 章 old_str→new_str | edits=[{old_str,new_str,reason}] + 唯一命中校验 | ✅ 一致 |
| 评估基线 | skill-creator 用旧版本 snapshot 作基线 | 同一历史轨迹上新旧步骤对打分（before/after） | ✅ 更强（确定性、零重放成本） |
| 发布门槛 | skill-creator 双集（边界改善 ∧ 基线不退）；第 8 章 release_to_canary | 双集回归零 LLM + 保留集零退化硬门槛 + 样本量下限 | ✅ 一致 |
| 版本与回滚 | Hermes ledger+快照+单条目回滚；def_versions 归档 | ai_skill_def_versions 归档 + rollback hash 必等 | ✅ 等价（审计走 ai_execution_events + log_operation） |
| 使用追踪 | Hermes .usage.json 计数 | **ai_skill_invocations 更强**（可查、带 attempt 与 outcome） | ✅ 已覆盖 |
| 触发/激活管理 | skill-creator 描述优化 60/40 分割防过拟合；Hermes 渐进披露 | 无（O4 激活率在 P2） | ⏸ 记录为 P2 输入 |
| 陈旧/修剪 | Hermes Curator 两阶段 + 宽限下限 | O5（P3） | ⏸ 记录设计输入 |
| **被拒修改回灌** | 第 8 章 Self-Harness：候选生成器输入含「此前被拒的修改」 | **缺** —— 诊断 prompt 不知道历史被拒候选 | ❌ **P1 采纳（修订 R1）** |
| **候选去重** | ACE 确定性去重；Hermes 记忆精确重复自动拒绝 | **缺** —— 同一 diff 可重复建候选 | ❌ **P1 采纳（修订 R2）** |
| **结构守卫/作用域** | ACE 条目标识防跨条目污染；第 8 章作用域+冲突检查 | **缺** —— edit 可静默增删步骤 id 或收窄断言 | ❌ **P1 采纳（修订 R3）** |
| **主观/客观修改分治** | skill-creator：主观输出不强行加断言 | **缺** —— 纯正文（行为指导类）修改会因 fit 无变化被误判「无改善」而拒绝 | ❌ **P1 采纳（修订 R4）** |
| 候选池择优 | GEPA Pareto 前沿 | 单候选串行 + 人工选择 | ⏸ 刻意不采纳（人工门下收益低），记录 P3 |
| 冲突失效 | Mem0 四态 / Graphiti 双时序失效 | 无知识载体 | ⏸ P2 知识载体设计输入 |
| label 解耦部署 | Langfuse production/staging label | promote 即写文件（文件=生产） | ⏸ 刻意不采纳：单文件技能模型下回滚已秒级、无流量可切，label 间接层只增复杂度；已由 rollback+版本归档覆盖 |

## 4. P1 优化决议（已回灌设计文档与实施计划）

- **R1 诊断输入补「被拒修改 + 成功约束」（Self-Harness 原则）**：`diagnose_result` 组装材料时加载该定义最近 ≤5 条 rejected/expired 候选（old_str 清单 + rejectReason）与保留集当前 fit 步骤清单，注入 prompt——禁止换说法重复提交、明确哪些行为不能破坏。落点：计划 Task 8。
- **R2 候选去重**：`ai_skill_candidates` 增 `edits_hash` 列（canonical edits JSON 的 sha256）；同 (def, base_hash, edits_hash) 且状态 ∈ pending/validated 的重复创建 → 409 返回既有 id。落点：计划 Task 6（迁移加列）、Task 9（创建校验）。
- **R3 apply_edits 结构守卫**：替换后新旧 `fit.steps` 的**步骤 id 集合必须相等**（禁止增删步骤）；任一步骤 expect 条目被收窄时要求 source.expected_repair 非空（manifest 必须声明预期修复，否则 400）——把第 8 章「作用域+冲突检查」落成确定性规则。落点：计划 Task 7。
- **R4 修改分类：mechanical / behavioral 双模回归（skill-creator 启发，本次研究最重要的发现）**：edits 完全不触及 frontmatter `fit.steps` 区（纯正文行为指导）的候选走 `behavioral` 模式——回归打分对它无意义（分数必然无变化，会被「无改善」误杀），改为 validation 记 `{mode: behavioral, note}` 并直接 validated，**人工 promote 仍是唯一发布闸门**；触及 steps 的照旧走 mechanical 双集回归，分数零变化照旧拒绝。落点：计划 Task 7。

四项全部是小改动（合计约 +0.5 天），且 R2 赶在 Task 6 迁移未执行前加列零成本。修订后的计划仍是 12 任务，各任务内步骤更新。

## 5. P2/P3 设计输入（不进 P1，记录备用）

1. **O5 Curator 照抄 Hermes 两阶段**：确定性 stale/归档先行（宽限下限：零调用不作为陈旧证据，须满 年龄门槛；pin/builtin/被批任务引用的豁免），LLM 合并默认关、显式开启、aux 便宜模型；变更前快照、永不清除只归档、单条目回滚。我们的 pin 对应物可以是「被现行批模板/编排节点引用的定义不参与修剪」。
2. **O4 激活率落地后接 skill-creator 的触发描述优化**：低激活定义（有注入、少 runtime 调用）生成 should/near-miss 查询对，60/40 分割防过拟合地优化 description。
3. **知识载体（8.2.1）冲突语义**：Mem0 四态决策 + Graphiti「失效不删除」；**冲突判定优先确定性规则**（同主体+同谓词检索比对），LLM 判定仅兜底且须记录置信度——Graphiti #1666 的教训。
4. **GEPA 式候选池**：同一定义多候选共存已支持（无唯一约束），P3 可做「按逐任务 Pareto 前沿自动推荐最优候选」。
5. **会话边界与缓存友好**：Hermes「冻结快照注入、中途变更下批生效」与我们 inject_global_skills 的会话创建时点同步天然一致；睡眠学习触发（O5）应复用「空闲门控」而非 cron。

## 6. 来源

- Hermes Agent 官方文档：[Curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator) / [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) / [Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [anthropics/skills · skill-creator SKILL.md](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)
- ACE：[论文](https://www.researchgate.net/publication/396250582_Agentic_Context_Engineering_Evolving_Contexts_for_Self-Improving_Language_Models)、[ace-agent/ace](https://github.com/ace-agent/ace)、[aws-samples/sample-context-engineering](https://github.com/aws-samples/sample-context-engineering)
- GEPA：[gepa-ai/gepa](https://github.com/gepa-ai/gepa)、[arXiv:2507.19457](https://arxiv.org/abs/2507.19457)、[DSPy GEPA](https://dspy.ai/current/api/optimizers/GEPA/overview)
- Mem0：[arXiv:2504.19413](https://arxiv.org/html/2504.19413v1)、[How It Works](https://docs.mem0.ai/core-concepts/how-it-works)
- Zep/Graphiti：[arXiv:2501.13956](https://www.arxivisual.org/abs/2501.13956)、[graphiti#1666（弱判官静默漏失效）](https://github.com/getzep/graphiti/issues/1666)
- Langfuse：[Prompt Management](https://langfuse.com/docs/prompts)（production/staging label 与实验门禁）、[langfuse-workshop](https://github.com/langfuse/langfuse-workshop/blob/main/docs/instructor/03-prompt-management.md)
