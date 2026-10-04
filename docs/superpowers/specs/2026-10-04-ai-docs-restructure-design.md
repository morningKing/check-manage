# AI 设计文档与用户指导重构 设计

> 日期：2026-10-04 ｜ 状态：待评审
> 已确认决策（2026-10-04 头脑风暴）：①骨架=总览+编号分册；②核实深度=全量对码核实；③旧文档处置=归档+横幅；④用户指导=校正+补齐缺口。
> 取代：`2026-09-20-ai-design-document-consolidation.md`（「全部并入 09 单文档」方案，从未执行，单一大文档不可维护）。

## 1. 背景与问题

对代码的全量盘点（2026-10-04，证据见 §4）显示 AI 子系统现状：**16 个后端路由文件、27 个 MCP 工具、约 40 张数据表、前端 4 个 settings-hub 域入口 + 全页助手**。而设计文档三处分散且落后：

1. `design/09-AI智能助手.md`（域⑨主文档）模块表未覆盖 harness P0/P1/P2、动作门禁、编排、SkillOpt、执行审计、管理页、压测等 9 月中下旬落地的大块能力；
2. `design/ai/` 下 14 个文件命名混乱（11/12/13 编号与中文名混排），部分内容已被后续演进超越；
3. 最新事实大量沉淀在 `superpowers/specs/` 的约 30 份 AI spec 里，没有面向「读者」的整合视图；
4. `user-guide/ai/` 10 篇存在错位（动作门禁指南放在 user-guide 根目录）与缺口（门禁 pre 模式、外部 MCP 管理、预算、运行时配置、技能/SkillOpt 管理等无用户指导）。

2026-09-20 曾规划「全部并入 09 单文档」，spec 从未执行——单一大文档已被证伪。本设计改用**总览+编号分册**。

## 2. 目标与非目标

**目标**

- G1 全量功能清单：一份带代码证据的 AI 功能矩阵，覆盖全部路由文件、MCP 工具、数据表与前端入口，作为核实的载体。
- G2 设计文档重构：`design/ai/` 重组为 00 总览 + 12 编号分册；`09-AI智能助手.md` 变薄为域⑨入口；两处 README 索引同步。
- G3 用户指导重构：`user-guide/ai/` 10 篇对码校正 + 新增 5 篇补齐缺口；`integration/` 3 篇 AI API 篇对码校正。
- G4 旧文档处置：被取代者归档+横幅；specs 原地不动（仅 09-20 整合 spec 加取代横幅）。

**非目标（明确不做）**

- 不改任何产品代码。文档核对中发现代码与文档不一致时以代码为准修文档；发现疑似 bug 只记录不修。
- 不整理 `docs/ai-testing/` 测试报告体系（保持历史原样）。
- 不把 `integration/` 的 API 指导合并入 `user-guide/ai/`（仅校正）。
- 首版不新画分册级 SVG：`AI功能架构图.svg` 保留在 `design/ai/` 并按代码做必要修正；各分册图表后续按需补充。
- `superpowers/specs/` 与 `superpowers/plans/` 不搬迁、不合并（它们是过程记录，不是权威设计）。

## 3. 分册结构（docs/design/ai/）

每册遵循 `design/` 域文档统一模板：模块职责 / 模块组成 / 数据模型 / 核心流程 / 关键接口 / 依赖与协作关系 / 设计决策，末尾加 **「代码索引」** 一节（本册主要模块与文件清单）。所有机制描述以当前代码为准。

| # | 文件名 | 覆盖内容 | 主要代码面 |
|---|--------|----------|-----------|
| 00 | `00-总览与功能清单.md` | 索引、阅读指南（按角色：使用者/管理员/集成者/开发者）、**全量功能矩阵**（§4）、文档缺口清单 | 全部 |
| 01 | `01-总体架构与运行时.md` | 三进程架构（Flask/OpenCode/MCP）；AgentRuntime 抽象与 `opencode_local`/`stub` 两实现；workspace 与 per-session opencode.json、session_token；baize-tool-timeout / baize-subagent-reuse / baize-trace 三个插件部署；opencode serve 启动/归属/看门狗 | `opencode_client/global/launch/ownership.py`、`workspace.py`、`runtime/`、插件部署器 |
| 02 | `02-对话助手.md` | 会话 CRUD/全文搜索/分组/置顶/压缩/归档；SSE 流式与消息 parts；制品卡；@文件 mention 与工作区文件/diff/预览；斜杠命令；AskUserQuestion/权限问答卡；上下文水位线；NL→Mongo 查询翻译与 NL 建页草案；kefu 消费方 | `ai_chat.py`（39 端点）、`ai.py /query`、`chat_persist.py`、`AiChatView.vue` + `components/ai-chat/` |
| 03 | `03-批任务与执行引擎.md` | 批任务创建/控制（cancel/pause/resume/retry/append/force-stop）；执行引擎 claim（heartbeat/generation）、CAS 终态唯一入口、fencing_token；命令平面（Idempotency-Key）；预算（token/cost/wall-clock，warn/drain/abort）；批次事件流；批管理后台（跨用户、文件、attempt-timeline）；交付 outbox | `ai_chat_batches.py`、`ai_batch_admin.py`、`batch_engine/repo/events.py`、`execution_lease/commands/checkpoint/effect/budget.py`、`delivery_outbox.py` |
| 04 | `04-编排与子代理.md` | 编排定义版本化发布（发布后不可变、环检测）；Run/Step 三层引擎（状态派生自事实、scheduler 租约）；join/approval/compensation 节点；单步调试（execution_mode/advance）；DAG 编辑器与运行图；子代理轨迹、独立取消、会话复用钉住 | `ai_orchestrations.py`、`orchestration_defs/engine.py`、`subtask_repo.py`、`subagent_reuse_plugin.py`、`OrchDagEditor.vue` 族 |
| 05 | `05-动作门禁与审批.md` | 动作门禁三入口（批创建参数/模板携带/交互会话 register_action_check）；deny 阻断（pre 模式，fail-open）与终态核对；工具账本 `agent_tool_calls`；verifier 判官（隐藏会话、用完即弃、分组核对）；dry-run 试算；执行前审批（decision_hash、edits、幂等） | `agent_ledger.py`、`action_check_extractor.py`、`verifier.py`、`ai_gate_internal.py`、`ai_approvals.py`、`approval_repo.py` |
| 06 | `06-执行审计与轨迹分析.md` | 审计事实族（attempts/events/manifests/prompt_snapshots）；执行契约（explicit/inferred/unknown）；todo 计划孪生；确定性审计器（契约×计划×证据）；轨迹分析会话（管理员 analyze→分析会话→报告→建议反馈闭环）；attempt-timeline；Skill 调用采集 | `execution_audit/contract.py`、`todo_trace.py`、`trace_auditor.py`、`ai_session_admin.py` 双蓝图、`skillopt.py` |
| 07 | `07-定时扫描.md` | 任务 CRUD 与 run-now；APScheduler 调度与认领；结构化回写（extract_json、`jsonb_set` 参数化）与写回校验；产出文件导入记录字段 | `ai_scan_tasks.py`、`ai_scan_engine/repo/scheduler.py`、`scan_writeback_validator.py` |
| 08 | `08-Skill与SkillOpt.md` | 全局技能库（盘+DB、zip 上传、发布到运行时）；会话技能上传（SKILL.md frontmatter 契约）；拟合规则层（frontmatter fit.steps × 账本轨迹）；AI 层四能力（generate/apply/preview/diagnose）；治理面（def versions/patterns/feedback） | `ai_skills.py`、`global_skills.py`、`skill_upload.py`、`skill_fit*.py`、execution_admin 蓝图 skill 族 |
| 09 | `09-长期记忆.md` | mem0（Chroma+DashScope）按 user_id 分区；原生线程单亲和；Flask 单写者与内部端点；注入/被动提炼/主动补写；MCP 记忆三工具；备份归属 | `memory.py`、`ai_memory_internal.py`、`ai.py /memories` |
| 10 | `10-管理配置与外部MCP.md` | `ai_settings` 全局配置；**外部 MCP 服务器注册**（CRUD+健康检查+贡献给 opencode.json）；settings-hub 四域入口结构与页签化；权限拆分（`admin.ai_runtime_read/ai_runtime_force` 等）；用户记忆自助端点 | `ai.py`、`mcp_servers.py`、`settingsCatalog.ts`、`AiConfigHub.vue` |
| 11 | `11-容灾稳定性与压测.md` | serve 看门狗与重启编排；重启对账（`_reconcile_stale_running` 分流）；会话恢复重注入（`session_history`）；重试分类与上限；checkpoint；租约（worker/scheduler/delivery）；outbox 重放与 dead_letter；压测栈（专属 DB/StubRuntime 容量层/真 serve 混沌层/读写压载） | `oc_watchdog.py`、`batch_engine` 对账恢复族、`execution_lease.py`、`runtime/stub.py`、压测 spec（2026-10-03） |
| 12 | `12-开放API.md` | `/v1` 七族端点（ai-batches/ai-sessions/ai-orchestrations/ai-approvals/ai-scan-tasks/ai-memories/prompt-templates）；api_key 鉴权与绑定键；幂等语义；事件订阅 | `open_api_*.py` 六文件 + 注册处 `app.py` |

**`design/09-AI智能助手.md` 变薄**：保留「模块职责」一段定位 + 三进程架构一句图 + 分册导航表（12 册各一行）+ 跨域依赖（域①③②⑧）+ 与统一模板的对应说明。删去与分册重复的细节（数据模型、核心流程、接口表移入各分册）。

**索引更新**：`design/README.md`（域⑨行改为指向 09 入口 + design/ai/ 分册表）、`docs/README.md`（开发人员导航同步）。

## 4. 全量功能矩阵与核实方法

### 4.1 矩阵 schema（00-总览内）

| 列 | 说明 |
|----|------|
| 功能点 | 面向能力的一句话（如「批任务暂停/恢复」） |
| 代码证据 | `文件:行` 或符号名（如 `ai_chat_batches.py:412`） |
| 分册 | 00–12 之一 |
| 状态 | 已实现 / 部分 / 规划 |

矩阵按分册分组；每分册组尾附该册路由文件与 MCP 工具清单核对行，确保**16 个路由文件、27 个 MCP 工具、AI 表族、前端入口无遗漏**（遗漏即验收不通过）。

### 4.2 核实规则

1. 正文机制描述从当前代码写起，代码为唯一事实源；旧文档内容仅在代码证实后吸收。
2. 端点表逐条对路由装饰器；表结构逐条对 `db_schema/` 与迁移文件。
3. 旧文档与代码冲突时：以代码为准，冲突点在分册「设计决策」记录一句（避免后人再被旧文档误导）。
4. 盘点时已发现、本设计须消化的**文档缺口**（代码有、文档无）：

| # | 缺口 | 落点 |
|---|------|------|
| 1 | 外部 MCP 服务器管理（CRUD/健康/贡献配置） | 分册 10 + user-guide `mcp-servers.md` |
| 2 | 批子任务尝试时间线 `attempt-timeline` | 分册 03、06 |
| 3 | `/v1` 审批/编排/记忆/模板/扫描对外端点无参考汇总 | 分册 12 |
| 4 | `/ai/data-internal/execute` 写转发安全边界 | 分册 01（架构）+ 03 |
| 5 | 门禁 pre 阻断模式与 dry-run 语义 | 分册 05 + user-guide `action-gate.md` |
| 6 | `ai_execution_budgets` 预算配置 | 分册 03 + user-guide `budgets.md` |
| 7 | 管理员轨迹分析闭环（analyses 列表/报告/建议反馈） | 分册 06 + user-guide `execution-audit.md` 补节 |
| 8 | settings-hub 四域入口与运行时权限拆分 | 分册 10 + user-guide 相应篇菜单路径校正 |
| 9 | 扫描产出文件导入记录字段 | 分册 07 + user-guide `scan-tasks.md` 补节 |

## 5. 用户指导重构（user-guide/）

**校正（对码逐篇过）**：`ai/` 现有 10 篇（assistant/batch-tasks/execution-audit/export-via-chat/long-term-memory/orchestration/scan-tasks/session-groups/smart-customer-service/trace-analysis）——菜单路径对齐 settings-hub 新结构、按钮/权限名与前端一致；`integration/ai-architecture.md`、`ai-batch-api.md`、`ai-session-api.md` 校正端点与鉴权描述。

**新增 5 篇**：

| 文件 | 内容 |
|------|------|
| `ai/action-gate.md` | 动作门禁与审批：三入口、pre 阻断 vs 终态核对、dry-run、重试上限、审批收件箱（工作流「我的待办」AI 页签）。**收编**根目录 `ai-batch-gate-guide.md`（内容并入后删除原文件，引用处改链新页） |
| `ai/skills.md` | 技能广场/全局技能库/zip 上传/发布到运行时 + SkillOpt 拟合优化（版本、诊断） |
| `ai/runtime.md` | OpenCode 运行时配置：总览/生效状态/apply/重启（force 权限） |
| `ai/mcp-servers.md` | 外部 MCP 服务器注册与健康检查 |
| `ai/budgets.md` | 资源预算：批/用户/API Key 维度、on_exceed 行为 |

**篇内补节**：`scan-tasks.md` 补产出导入；`execution-audit.md` 补管理员分析闭环；`batch-tasks.md` 补预算入口与 attempt-timeline 查看位置。

新页面截图按现有惯例补（无法运行环境时标注占位，不伪造）。

## 6. 旧文档处置（归档+横幅）

| design/ai/ 现有文件 | 去向 | 横幅指向 |
|--------------------|------|----------|
| `11-AI-trace-analyzer-skill-spec.md` | archive | 06 |
| `11-AI执行轨迹分析.md` | archive | 06 |
| `12-AI-Chat优化需求文档.md` | archive | 02 |
| `13-AI批任务实时进度与编排设计.md` | archive | 03 |
| `AI动作门禁verifier判官核对设计.md` | archive | 05 |
| `AI子任务动作账本与到位门禁设计.md` | archive | 05 |
| `AI执行审计能力说明.md` | archive | 06 |
| `AI批任务容灾与恢复机制.md` | archive | 11 |
| `AI批任务能力总览与创新点.md` | archive | 03 |
| `AI能力总览与长任务稳定性.md` | archive | 11（次要 02） |
| `OpenCode运行时依赖与部署.md` | archive | 01 |
| `SkillOpt任务拟合设计.md` | archive | 08 |
| `SkillOpt任务拟合页面重设计.md` | archive | 08 |
| `AI功能架构图.svg` | **留在 ai/** | 由 01 引用；按代码做必要修正 |

- 横幅格式沿用 `design/archive/` 现行惯例：顶部引用块「已归档，本文内容已由 … 取代」。
- `superpowers/specs/2026-09-20-ai-design-document-consolidation.md` 顶部加「已被本 spec 取代」横幅；其余 specs 不动。
- `docs/ai-testing/` 不动。

## 7. 验收标准

1. 00 功能矩阵覆盖全部 16 个路由文件、27 个 MCP 工具、AI 表族、前端入口，每行有代码证据；无遗漏行。
2. 13 个新文档（00–12）齐全且遵循统一模板；正文机制与代码一致；分册末「代码索引」就位。
3. `09-AI智能助手.md` 变薄为入口；`design/README.md`、`docs/README.md` 索引一致；全树无指向归档文件的活链接（横幅反向链接除外）。
4. user-guide 10 篇校正 + 5 篇新增 + 3 处篇内补节落地；根目录 `ai-batch-gate-guide.md` 已收编删除。
5. §6 处置表全部执行：归档文件带横幅、09-20 spec 带取代横幅。
6. 全程零产品代码改动（`git diff --stat` 仅 docs/）。

## 8. 执行方式

按分册拆任务串行执行（01→12→02–11 顺序：先架构总纲与索引面，再铺功能分册），每册完成即自验（对照矩阵核对行）；user-guide 与处置在分册全部落地后收口。实施计划见 writing-plans 阶段产出。
