# AI 设计文档与用户指导重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec 将 AI 设计文档重组为 `design/ai/` 00 总览+12 分册、`09-AI智能助手.md` 变薄为入口，并校正+补齐 `user-guide/` 的 AI 指导，全程对码核实、零产品代码改动。

**Architecture:** 纯文档任务。事实源=当前代码（16 路由文件 / 27 MCP 工具 / ~40 表 / 前端入口，盘点清单见各任务「覆盖清单」）；`00-总览` 的功能矩阵是核实载体，随分册逐册生长，末任务做覆盖验收；旧文档归档+横幅沿用 `design/archive/` 惯例。

**Tech Stack:** Markdown + git（`git mv` 保历史）；验证用 bash grep 自检，无测试框架。

**Spec:** `docs/superpowers/specs/2026-10-04-ai-docs-restructure-design.md`（已提交 07b663a）

## Global Constraints

- 零产品代码改动：`git diff --stat main` 最终只允许 `docs/` 路径。
- 每册遵循 `design/` 域文档模板：模块职责 / 模块组成 / 数据模型 / 核心流程 / 关键接口 / 依赖与协作关系 / 设计决策，末尾加「代码索引」一节。
- 机制描述以当前代码为准；旧文档内容仅代码证实后吸收；与代码冲突处在「设计决策」记一句。
- 端点逐条对路由装饰器、表逐条对 `server/db_schema/` 与迁移文件，证据写 `文件:行` 或符号名。
- 归档横幅格式（统一）：
  ```markdown
  > ⚠️ **已归档**（2026-10-04）：本文内容已由 [`<新分册文件名>`](../ai/<新分册文件名>) 取代，仅保留历史背景与追溯价值，不再是权威版本。
  ```
- 提交信息风格沿用仓库惯例：`docs(design): …` / `docs(user-guide): …`；每个任务一次提交。
- 新文档中文行文、相对链接；`docs/superpowers/` 下新文件 `git add` 需 `-f`（.gitignore 误伤，历史文件均如此）。

## 覆盖基线（全任务共用事实源）

**16 个后端路由文件**（`server/routes/`）：`ai.py`、`ai_chat.py`、`ai_chat_batches.py`、`ai_batch_admin.py`、`ai_chat_prompt_templates.py`、`ai_approvals.py`、`ai_orchestrations.py`、`ai_session_admin.py`（双蓝图）、`ai_scan_tasks.py`、`ai_skills.py`、`ai_opencode_admin.py`、`ai_memory_internal.py`、`ai_gate_internal.py`、`ai_subagent_internal.py`、`ai_data_internal.py`、`open_api_ai_sessions.py`（另 `open_api_*.py` 家族归分册 12）。

**27 个 MCP 工具**（`mcp-server/tools/`）：`list_collections`、`query_collection`、`read_data_file`、`read_upload`、`download_field_files`、`graph_neighbors`、`graph_traverse`、`query_sessions`、`analyze_trace`、`batch_children_status`、`batch_children_search`、`batch_child_changes`、`batch_tool_audit`、`list_export_scripts`、`run_export_script`、`export_collection_excel`、`save_artifact`、`run_python`、`memory_search`、`memory_add`、`memory_delete`、`register_action_check`、`ai_create_data_page`、`data_create_records`、`data_update_record`、`data_delete_record`、`data_attach_menu`。

**写操作路径约定**：MCP 写工具统一经 `tools/_data_api.py` → `POST /ai/data-internal/execute`（X-Internal-Token）转发真实应用层路由；memory 三工具经 `/ai/memory/internal/*`。

---

### Task 1: 00-总览与功能清单（骨架+矩阵初版）

**Files:**
- Create: `docs/design/ai/00-总览与功能清单.md`

**Interfaces:**
- Produces: 功能矩阵表（列：功能点｜代码证据｜分册｜状态），后续每个分册任务向其补充本册功能点行；矩阵分组锚点 `## 矩阵·01` ~ `## 矩阵·12` 供各任务定位。

- [ ] **Step 1: 写总览骨架**

内容四节：
1. **本库说明**——一句话定位 + 分册导航表（00–12 各一行：编号｜文件名｜一句话范围）。
2. **阅读指南**——按角色：使用者→user-guide/ai/；管理员→10/06/11 册；集成者→12 册+user-guide/integration/；开发者→全库+design/09 入口。
3. **功能矩阵**——初版含：①16 个路由文件覆盖行（每行=文件名+蓝图前缀+所属分册，证据 `app.py:116-130`、`app.py:513-522`、各文件头）；②27 个 MCP 工具覆盖行（每行=工具名+读/写类别+所属分册，证据 `mcp-server/tools/__init__.py` 注册表；写操作标注经 `/ai/data-internal/execute` 转发）；③AI 表族覆盖行（`ai_chat_sessions/messages/subtasks/subtask_messages/session_files/session_groups/turns/prompt_templates/batches/batch_events/batch_worker_leases/scan_tasks`、`agent_tool_calls`、`action_expectations`、`ai_execution_attempts/events/manifests/prompt_snapshots/contracts/step_results/diagnoses/checkpoints/effects/commands/budgets/usage`、`ai_delivery_outbox`、`ai_orchestration_definitions/runs/steps`、`ai_approval_requests`、`ai_subagent_pins`、`artifacts/artifact_refs`、`ai_runtime_manifests`、`ai_skill_invocations/suggestion_feedback/skill_fit_results/skill_def_versions`、`global_skills`、`ai_settings`、`ai_mcp_servers`，证据 `server/db_schema/core.py:155,186,422-488` 与迁移文件名）。
4. **状态标记约定**——已实现/部分/规划 的判定口径（有路由+前端入口=已实现；仅后端=部分；仅 spec=规划）。

- [ ] **Step 2: 自验**

```bash
grep -c "^| \`" docs/design/ai/00-总览与功能清单.md   # 矩阵行数应 ≥ 16+27+38
ls server/routes/ai*.py | wc -l                       # 对照=16（另 open_api_ai_sessions.py 单列）
```
并人工核对矩阵中 16 路由文件名与 `ls server/routes/ai*.py` 一致、27 工具名与 `mcp-server/tools/__init__.py` 的 `_TOOLS` 一致。

- [ ] **Step 3: Commit**

```bash
git add docs/design/ai/00-总览与功能清单.md
git commit -m "docs(design): AI 分册 00-总览与功能清单——分册导航+功能矩阵初版(路由/工具/表覆盖行)"
```

---

### Task 2: 01-总体架构与运行时

**Files:**
- Create: `docs/design/ai/01-总体架构与运行时.md`
- Modify: `docs/design/ai/AI功能架构图.svg`（仅当与下述事实明显不符时做最小修正）

**覆盖清单（本册必须出现的代码模块）**：`server/utils/runtime/{__init__,base,opencode_local,stub}.py`、`opencode_client.py`、`opencode_global.py`、`opencode_launch.py`、`opencode_ownership.py`、`oc_watchdog.py`、`workspace.py`、`workspace_changes.py`、`workspace_outputs.py`、`session_token.py`、`session_history.py`、`chat_persist.py`、`opencode_parts.py`、`tool_timeout_plugin.py`、`subagent_reuse_plugin.py`、`ai_memory_internal.py`(runtime-events)、`ai_gate_internal.py`、`ai_data_internal.py`、`ai_subagent_internal.py`、`mcp-server/main.py`。

**Interfaces:**
- Produces: 三进程架构图景与「内部契约端点」清单（`/ai/gate/internal/pre-check`、`/ai/data-internal/execute`、`/ai/subagent-internal/{reuse,resolve-agent,pins}`、`/ai/memory/internal/*`、`/ai/memory/internal/runtime-events`），分册 03/04/05/09 引用，不得改口径。

- [ ] **Step 1: 对码读关键文件**——`runtime/base.py`（7 抽象方法）、`runtime/__init__.py`（`AI_AGENT_RUNTIME` 切换）、`workspace.py`（per-session `opencode.json` 写 MCP url `?token=`）、`opencode_global.py`（无 CRUD API/无热重载、写即触发重启编排）、`opencode_ownership.py`（platform/external/unknown，外部托管禁按端口杀）、`ai_data_internal.py:34-38`（写转发白名单）。

- [ ] **Step 2: 写分册**——章节与必须落实的事实：
  1. **模块职责**：浏览器只与 Flask 通信；Flask=网关+SSE 代理；OpenCode=Agent 运行时；MCP Server=平台工具面，token→DB 校验得用户/RBAC。
  2. **进程与端口**：Flask（`FLASK_PORT`，默认 3002）、`opencode serve`（4096）、MCP Server（3003）；启动单源 `opencode_launch.py`。
  3. **AgentRuntime 抽象**：7 方法契约（capabilities/create_session/dispatch/list_messages/get_messages/abort/health）+ capability 语义（进程树可整体 kill、网络默认拒绝、secret 不落 workspace）；`AI_AGENT_RUNTIME` 当前仅 `opencode_local`（压测另有 `stub`）；StubRuntime 定位=压测容量层（详见分册 11）。
  4. **workspace 与会话身份**：每会话独立 workspace+路径穿越防御；`opencode.json` 是 per-session MCP 身份唯一机制；session_token 生成/续期/吊销（`session_token.py`）；身份链路五步（Flask 令牌→opencode.json→OpenCode 连 MCP→查表→RBAC）。
  5. **插件部署**：baize-tool-timeout（内置工具超时 abort 整回合）、baize-subagent-reuse（task 工具 before 注入钉住/after 登记）、baize-trace（skill load/invoke 事件上报 `/ai/memory/internal/runtime-events`）。
  6. **内部契约端点**（X-Internal-Token=MCP_INTERNAL_TOKEN，非浏览器）：gate pre-check（fail-open）、data-internal 写转发（白名单=动态数据单条写+数据菜单创建，`ai_data_internal.py:34-38`）、subagent-internal（含 before 钩子意图表 10min TTL 进程内存）、memory internal（Flask 独占向量库单写者）。
  7. **serve 生命周期**：归属注册表、看门狗连续 threshold 次不可达自动 restart、外部托管模式禁杀；`opencode_global.py` v1.15.1 事实=无 CRUD API/无热重载/重启是唯一生效途径。
  8. **代码索引**。

- [ ] **Step 3: 自验**——覆盖清单 20 项逐一在文中出现；SVG 若被修改则用浏览器/XML 解析器验证可解析；文内相对链接目标存在。

- [ ] **Step 4: Commit**

```bash
git add docs/design/ai/01-总体架构与运行时.md docs/design/ai/AI功能架构图.svg
git commit -m "docs(design): AI 分册 01-总体架构与运行时——三进程/AgentRuntime/workspace/插件/内部契约端点"
```

---

### Task 3: 02-对话助手

**Files:**
- Create: `docs/design/ai/02-对话助手.md`

**覆盖清单**：`ai_chat.py` 全部端点族（sessions CRUD/search、models/agents、session-groups/pin、messages、compact、subtasks 族、tool-calls、skill-fit、events SSE、lsp-formatter、files/file-records/download、changes/diff/preview、mcp/commands、abort/runtime-state、pending-question/questions/pending-permission、run/close/reopen/clear、admin sessions/archive）、`ai.py /query`（NL→Mongo 翻译不执行）、`ai_schema_designer.py`、`mention_files.py`、`ai_message_meta.py`、前端 `AiChatView.vue`（侧栏三折叠区、Ctrl+K、@补全、斜杠命令、水位线、轨迹分析会话分组）、`components/ai-chat/` 关键组件（ToolCallBubble/SubtaskBubble/QuestionCard/PermissionCard/ArtifactCard/FileDiffView/CommandPalette/MemoryManager/PromptTemplateManager）、`stores/aiChat.ts`、kefu 消费（`KEFU_TOOL_ALLOWLIST`）。

**Interfaces:**
- Consumes: 分册 01 的身份链路与 SSE 代理口径。
- Produces: 会话状态与 close/reopen/clear 语义、消息 parts 形状（text/file/tool_use/run_result）——分册 03/06 引用。

- [ ] **Step 1: 对码**——`ai_chat.py` 逐端点过装饰器与方法名（39 端点，见 `ai_chat.py` 各 L 号）；`stores/aiChat.ts` 的 part upsert 状态机。
- [ ] **Step 2: 写分册**（模板七节+代码索引；数据模型含 `ai_chat_sessions/messages/subtasks/session_files/session_groups/turns`；核心流程=建会话/发消息/流式落库/恢复重注入/问答闭环/截断重发；设计决策记：上下文水位线、轨迹分析会话复用对话视图、kefu 白名单只读）。
- [ ] **Step 3: 自验**——39 端点全部入「关键接口」表；矩阵 `## 矩阵·02` 补本册功能点行（每端点族≥1 行，带 `文件:行`）。
- [ ] **Step 4: Commit**——`git add docs/design/ai/02-对话助手.md docs/design/ai/00-总览与功能清单.md && git commit -m "docs(design): AI 分册 02-对话助手——39端点/消息parts/制品/问答卡/kefu消费"`。

---

### Task 4: 03-批任务与执行引擎

**Files:**
- Create: `docs/design/ai/03-批任务与执行引擎.md`

**覆盖清单**：`ai_chat_batches.py`（staging/upload、创建含 action_checks/subagent_reuse/gate_retry、cancel/pause/resume/retry-failed/force-stop、append、commands Idempotency-Key、events 单批与多批 SSE、attempts、gate/dry-run、action-checks extract/attach）、`ai_batch_admin.py`（跨用户列表/详情、messages、retry、reexecute、软删、files/preview/download/import、tool-calls、attempt-timeline `ai_batch_admin.py:305`、changes/diff、deliveries/replay）、`open_api_batches.py`（`/v1/ai-batches`）、`batch_engine.py`（claim heartbeat_at/execution_generation、`transition_child` CAS、fencing_token、workspace provision、gate_retry 上限）、`batch_repo.py`、`batch_events.py`（advisory lock seq）、`execution_lease/commands/checkpoint/effect/budget.py`、`delivery_outbox.py`、前端 CreateBatchDialog/EditBatchConfigDialog/AppendFilesDialog/BatchGroup/BatchConversationView、stores aiChatBatches/aiBatchAdmin。

**Interfaces:**
- Consumes: 分册 01 租约与 runtime 口径、分册 05 门禁三入口（本册只写批侧入口引用，机制细节留 05）。
- Produces: 批状态机（pending/running/paused/cancelled/partial_failed/…终态集合 `BATCH_TERMINAL_STATUSES`）、预算 on_exceed=warn/drain/abort、outbox 退避序列 1s/5s/30s/5m/30m×8→dead_letter——分册 11/12 引用。

- [ ] **Step 1: 对码**——`batch_repo.py:85-140`（CAS 条件 UPDATE）、`batch_engine.py:1116-1172`（claim）、`execution_budget.py`（两判定点：claim 前+子任务终态）、`delivery_outbox.py`（租约单投递者、HMAC 复用 webhook_engine）。
- [ ] **Step 2: 写分册**（模板七节+代码索引；核心流程=创建→暂存→worker 认领→执行→终态→交付；数据模型含批次表族+execution_* 六表+outbox；设计决策记：管理员蓝图因前缀钉死拆出 `ai_batch_admin.py:4`、命令平面幂等、预算两判定点）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·03` 补行；端点表与两路由文件逐一对应。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 03-批任务与执行引擎——claim/CAS/fencing/预算/命令平面/交付outbox`。

---

### Task 5: 04-编排与子代理

**Files:**
- Create: `docs/design/ai/04-编排与子代理.md`

**覆盖清单**：`ai_orchestrations.py`（definitions CRUD+发布 admin、runs 创建任意登录用户、events/graph/suspend/mode/advance 单步调试）、`open_api_orchestrations.py`、`orchestration_defs.py`（版本发布不可变、环检测/悬空边/join 语义）、`orchestration_engine.py`（Run←Step←子会话状态派生、agent step=ai_chat_sessions 子会话、scheduler `lease_kind='scheduler'`）、`approval_repo.py`（结构节点）、`subtask_repo.py`、`ai_subagent_internal.py`、`subagent_reuse_plugin.py`、前端 OrchDagEditor/OrchRunGraph/OrchStepDetail/NodePalette/NodePropertiesPanel、`utils/orchGraph.ts`（dagre）、表 `ai_orchestration_definitions/runs/steps`、`ai_subagent_pins`。

- [ ] **Step 1: 对码**——`orchestration_engine.py` 的 step→子会话认领复用链（ownership/CAS/generation/lease/checkpoint 如何复用批引擎）。
- [ ] **Step 2: 写分册**（模板七节+代码索引；核心流程=定义发布→run 创建→scheduler 推进→agent step 派发→join/approval→suspend/手动推进→补偿；设计决策记：P3 单步调试 execution_mode、effect 自动补偿、子代理独立取消、复用钉住 (root,agent) 唯一）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·04` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 04-编排与子代理——版本化定义/三层引擎/DAG/单步调试/复用钉住`。

---

### Task 6: 05-动作门禁与审批

**Files:**
- Create: `docs/design/ai/05-动作门禁与审批.md`

**覆盖清单**：门禁三入口（批创建 action_checks 参数 `ai_chat_batches.py:92`、模板携带 `ai_chat_prompt_templates.py:43`、交互会话 `register_action_check` MCP 工具——模型不能增删已登记期望）；pre 阻断（`ai_gate_internal.py:68` pre-check，deny list 命中即阻断、fail-open，OC session id 经 `ai_chat_sessions.opencode_session_id`/`ai_chat_subtasks` 映射 scope，迁移 `2026_09_26_action_gate_mode`）；终态核对（`agent_ledger.py` validate_checks/正则可编译性把关）；账本（`agent_tool_calls` 幂键 (oc_session_id,part_id)、条件更新防写放大、失败→inconclusive）；verifier（`verifier.py:513` 隐藏 OC 会话跑 baize-verifier、JSON verdict、用完即弃不落消息表、全树组/子代理组分组核对 `2026-09-30`）；dry-run 试算（`ai_chat_batches.py:219` 含 verifier 试算）；LLM 提炼建议（`action_check_extractor.py`，红线=只出建议，执行侧无增删期望路径，attach 仅 admin `ai_chat_batches.py:268`）；效果账本衔接（`execution_effect.py` record→执行→settle、`unknown` 禁自动重放→needs_review）；审批（`ai_approvals.py` `/v1/ai-approvals`、decision_hash、approve 可带 edits `ai_approvals.py:49`、uniq pending per step、决策写 log_operation、前端 WorkflowInbox.vue:86 AI 页签）。

- [ ] **Step 1: 对码**——`agent_ledger.py` 三职责边界；`verifier.py` 材料组装与 rubric；`ai_gate_internal.py` fail-open 语义。
- [ ] **Step 2: 写分册**（模板七节+代码索引；核心流程=登记→pre 阻断→执行落账→终态核对→verifier→needs_review/审批；设计决策记：三入口职责分离、fail-open 取舍、verifier 会话不落库）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·05` 补行；表 `agent_tool_calls/action_expectations/ai_approval_requests` 均入数据模型节。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 05-动作门禁与审批——三入口/pre阻断/账本/verifier/审批`。

---

### Task 7: 06-执行审计与轨迹分析

**Files:**
- Create: `docs/design/ai/06-执行审计与轨迹分析.md`

**覆盖清单**：`execution_audit.py`（六来源统一 attempt 形状、明文 prompt 默认不落库、`EXECUTION_AUDIT_STRICT=1`）、`execution_contract.py`（explicit=SKILL.md frontmatter/inferred 置信度<1 待审/unknown 不臆造违规）、`todo_trace.py`（服务端孪生 `src/utils/todos.ts`、永不当证据）、`trace_auditor.py`（契约×自声明计划×观测证据三方对比，LLM 只解释不制造事实）、`ai_session_admin.py` 双蓝图（v2 会话列表 source_type 四源 regular/batch/api_batch/scan、analyze→轨迹分析会话、analyses 列表/报告、suggestion-feedbacks、execution-audit 三件套端点 `ai_session_admin.py:524-550`）、`skillopt.py`（调用采集 heuristic→confirmed、分层保留）、MCP `query_sessions`/`analyze_trace`/`batch_tool_audit`、前端 AiSessionAdmin/ExecutionAuditDrawer/SkillFitBadge、trace-analysis 会话分组（`AiChatView.vue:423,537`）、表 `ai_execution_attempts/events/manifests/prompt_snapshots/contracts/step_results/diagnoses`、`ai_skill_invocations/ai_suggestion_feedback`。

- [ ] **Step 1: 对码**——`trace_auditor.py` 三方对比输入输出；`session_admin_repo.py` 四源 UNION 口径。
- [ ] **Step 2: 写分册**（模板七节+代码索引；核心流程=采集→契约建立→审计诊断→管理员发起分析会话→报告→建议反馈闭环；设计决策记：best-effort 无异常采集、分析引擎跑在 OpenCode 内部（继承旧 11 册结论，代码证实后吸收））。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·06` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 06-执行审计与轨迹分析——事实族/契约/确定性审计/分析闭环`。

---

### Task 8: 07-定时扫描 + 09-长期记忆（两小册）

**Files:**
- Create: `docs/design/ai/07-定时扫描.md`
- Create: `docs/design/ai/09-长期记忆.md`

**07 覆盖清单**：`ai_scan_tasks.py`（CRUD/run-now/import-outputs `ai_scan_tasks.py:72`，全 `admin.ai_scan`）、`ai_scan_engine.py`（extract_json、`_write_back` 参数化 `jsonb_set`、`record_effect('scan_writeback')`、`_import_child_outputs_to_record`）、`ai_scan_repo.py`、`ai_scan_scheduler.py`（APScheduler 1min tick、`_is_due`、启动孤儿清扫）、`scan_writeback_validator.py`、批完成钩子 `batch_engine._run_one`→`on_child_finished`、表 `ai_scan_tasks`、前端 AiScanTaskManager.vue+侧栏「AI定时任务」区。**09 覆盖清单**：`memory.py`（mem0+Chroma+DashScope、按 user_id 分区固定 collection、单线程 executor 钉亲和——onnxruntime 原生线程绑定 SIGSEGV 约束）、`ai.py /memories`（GET/POST/DELETE、verbatim 原样保存、mem0 未配置 POST 409）、`ai_memory_internal.py`（search/add/delete，Flask 单写者）、MCP memory 三工具、注入/被动提炼（仅真人会话）/主动补写、备份 `MEM0_STORE_ROOT` 归 `vector_store/`。

- [ ] **Step 1: 对码**——`ai_scan_engine.py` 回写与导入链；`memory.py` 线程亲和实现。
- [ ] **Step 2: 写两册**（各模板七节+代码索引；07 设计决策记「跨表副作用交给 skill 经 MCP、仅同行结构化回写内建」；09 设计决策记「记忆层全程降级 no-op 不阻断聊天」）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·07`、`## 矩阵·09` 各补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 07-定时扫描+09-长期记忆——调度回写导入/mem0单写者`。

---

### Task 9: 08-Skill与SkillOpt

**Files:**
- Create: `docs/design/ai/08-Skill与SkillOpt.md`

**覆盖清单**：`ai_skills.py`（全局技能库 CRUD+files，`admin.ai_settings`）、`global_skills.py`（盘上 `<AI_WORKSPACE_ROOT>/global-skills/<name>/`+DB 元数据、批子会话 `_inject_global_skills` 注入）、`skill_upload.py`（zip 校验、SKILL.md frontmatter 契约）、`skill_fit.py`（贪心顺序匹配→`ai_skill_fit_results`）、`skill_fit_ai.py`（generate/apply/preview/diagnose 四能力、共用 AI 设置通道、失败 502）、execution_admin 蓝图 skill 治理族（`ai_session_admin.py:661-1302`：skill-analytics/versions、skill-fit 家族、recompute、diagnose、skill-def-versions PATCH、skill-def-patterns、skill-def-steps generate/apply/preview）、`ai_opencode_admin.py /skills` 族（发布 publish `ai_opencode_admin.py:151`）、前端 AiSkillManager/AiSkillOpt+skillopt/ 六组件、表 `global_skills/ai_skill_fit_results/ai_skill_def_versions/ai_skill_invocations`。

- [ ] **Step 1: 对码**——拟合算法（frontmatter `fit.steps` × 账本轨迹贪心匹配）；发布链（全局技能库→运行时 skills→重启生效，衔接分册 01）。
- [ ] **Step 2: 写分册**（模板七节+代码索引；设计决策记：会话技能 vs 全局技能 vs 运行时技能三层、拟合结果仅为证据不作强约束）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·08` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 08-Skill与SkillOpt——三层技能/拟合/AI治理`。

---

### Task 10: 10-管理配置与外部MCP

**Files:**
- Create: `docs/design/ai/10-管理配置与外部MCP.md`

**覆盖清单**：`ai.py`（GET/PUT settings Key 打码、`ai_mcp_servers` CRUD+`/mcp-servers/internal/health`+PUT internal 开关，`ai.py:200-281`）、`mcp_servers.py`（外部 MCP 贡献给 opencode.json 的 mcp 配置）、`settingsCatalog.ts`（AI 四域入口页签化：①AI 配置=AiSettings+AiOpencodeRuntime；②AI 定时巡检；③AI 技能=AiSkillManager+AiSkillOpt；④AI 执行中心=AiBatchAdmin+AiSessionAdmin+AiOrchestrationManager）、权限拆分（迁移 `2026_09_15_split_ai_runtime_permissions`：`admin.ai_runtime_read/ai_runtime_force` 等，逐一对 `rbac`/迁移文件核实）、旧路径重定向表（settings-hub 路由消费）、`ai_query.py`/`ai_schema_designer.py` 的配置通道、前端 McpServersCard.vue、表 `ai_settings/ai_mcp_servers`。

- [ ] **Step 1: 对码**——settingsCatalog.ts 逐条目权限；外部 MCP 健康检查端点与 internal 开关语义。
- [ ] **Step 2: 写分册**（模板七节+代码索引；设计决策记：Key 打码存储、内建 MCP 开关与外部注册分离、四域入口合并的菜单演进）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·10` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 10-管理配置与外部MCP——settings/外部MCP注册/hub四域/权限拆分`。

---

### Task 11: 11-容灾稳定性与压测

**Files:**
- Create: `docs/design/ai/11-容灾稳定性与压测.md`

**覆盖清单**：`oc_watchdog.py`（连续 threshold 次不可达自动 restart）；重启对账 `_reconcile_stale_running`（`batch_engine.py:856`，lease/checkpoint/OC 会话状态三路分流）；会话恢复 `_recover_session`+`session_history.py` 上下文重注入（`batch_engine.py:1514`）；重试分类 `_is_retryable`+MAX_AUTO_RETRY=2（`batch_engine.py:3161-3211`）+gate_retry 上限（`batch_engine.py:1858`）；checkpoint（`execution_checkpoint.py`，`is_latest` 部分唯一索引）；租约（`execution_lease.py`，worker/scheduler/delivery 三类、fencing_token 单调预留）；投递重放（`ai_batch_admin.py:457` deliveries replay、dead_letter）；预算兜底（warn/drain/abort，衔接分册 03）；压测栈（专属 DB `casemanage_stress`、`FLASK_PORT=3092`、`AI_AGENT_RUNTIME=stub`、`AI_STUB_PROFILE` 行为面、混沌层专属 serve 4097+后端#2 真运行时、采样器 2s 抓 `/metrics` 7 gauge+DB 不变量+RSS；事实源=spec `2026-10-03-ai-stress-testing-design.md` 与 `server/tests/stress/conftest.py` 现状）。

- [ ] **Step 1: 对码**——`_reconcile_stale_running` 三分流判定；`server/tests/stress/conftest.py` 当前实现进度（spec 是设计、代码落地到哪一步如实写，状态列标「部分」）。
- [ ] **Step 2: 写分册**（模板七节+代码索引；设计决策记：压测与 dev 零共享、StubRuntime 不出网零 token、混沌层小规模验证进程语义）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·11` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 11-容灾稳定性与压测——看门狗/对账/恢复/重试/租约/压测栈`。

---

### Task 12: 12-开放API

**Files:**
- Create: `docs/design/ai/12-开放API.md`

**覆盖清单**：`open_api_batches.py`（`/v1/ai-batches` 家族）、`open_api_ai_sessions.py`（`/v1/ai-sessions` 创建/查状态/cancel，复用 batch_engine `batch_id IS NULL` 分支，文件上传复用 `/v1/ai-batches/uploads`，`open_api_ai_sessions.py:53-121`）、`open_api_orchestrations.py`、`open_api_approvals.py`、`open_api_scan_tasks.py`、`open_api_memories.py`、`open_api_prompt_templates.py`（后五个文件名以 `ls server/routes/open_api_*.py` 实际为准，逐一对码）、`app.py:99-105` 注册、`api_key_required`+`require_bound_key` 鉴权链、与 webhook/HMAC 的关系（outbox 交付复用 webhook_engine，衔接分册 03/11）。

- [ ] **Step 1: 对码**——`ls server/routes/open_api_*.py` 得实际文件清单，逐文件过端点与鉴权装饰器（spec 盘点只确证了 ai-batches/ai-sessions/orchestrations/approvals 四族；scan/memories/prompt-templates 三族必须在写册时核实存在性，不存在则矩阵状态列记「规划」并注明）。
- [ ] **Step 2: 写分册**（模板七节+代码索引；核心流程=api_key→绑定键→端点→幂等；设计决策记：单会话 API 刻意只有两个半端点）。
- [ ] **Step 3: 自验**——矩阵 `## 矩阵·12` 补行。
- [ ] **Step 4: Commit**——`docs(design): AI 分册 12-开放API——/v1家族/绑定键/幂等`。

---

### Task 13: 09 入口变薄 + 两处 README 索引

**Files:**
- Modify: `docs/design/09-AI智能助手.md`（重写为薄入口）
- Modify: `docs/design/README.md`（域⑨行+横切关注点表改链分册）
- Modify: `docs/README.md`（开发人员导航同步）

**Interfaces:**
- Consumes: 分册 00–12 全部文件名（链接目标必须已存在）。

- [ ] **Step 1: 重写 09**——保留：模块职责一段（域⑨定位）、三进程架构一句话、**分册导航表**（12 册各一行：编号｜链接｜一句话）、跨域依赖（域①会话/RBAC、域③扫描读写、域②字段配置、域⑧备份 vector_store/data_files）、模板对应说明；删除与分册重复的数据模型/核心流程/接口表细节。
- [ ] **Step 2: 更新两 README**——`design/README.md`：域⑨行改「文档：09（入口）+ ai/00–12 分册」；横切关注点表中现有 ai/ 链接改为对应分册；`docs/README.md` 开发人员导航第 3 条同步。
- [ ] **Step 3: 自验**——`grep -oP '\]\((\.\/ai\/|\./)[^)]+\)' docs/design/09-AI智能助手.md | 目标逐一存在`；两 README 无指向将归档文件的活链接。
- [ ] **Step 4: Commit**——`docs(design): 09-AI智能助手 变薄为域⑨入口+两处README索引指向分册`。

---

### Task 14: user-guide 校正（10+3 篇）

**Files:**
- Modify: `docs/user-guide/ai/` 下 assistant.md、batch-tasks.md、execution-audit.md、export-via-chat.md、long-term-memory.md、orchestration.md、scan-tasks.md、session-groups.md、smart-customer-service.md、trace-analysis.md
- Modify: `docs/user-guide/integration/` 下 ai-architecture.md、ai-batch-api.md、ai-session-api.md
- Modify: `docs/user-guide/README.md`（如目录描述需同步）

- [ ] **Step 1: 逐篇对码校正**——重点：菜单路径对齐 settings-hub 四域入口（`settingsCatalog.ts` 为准，路由/权限名逐一对）；`batch-tasks.md` 补预算入口与 attempt-timeline 查看位置（管理员）；`execution-audit.md` 补管理员 analyses 列表/报告/建议反馈闭环；`scan-tasks.md` 补产出文件导入记录字段；`ai-batch-api.md`/`ai-session-api.md` 端点与鉴权对 `open_api_*.py` 校正；`ai-architecture.md` 架构图景对分册 01 口径。
- [ ] **Step 2: 自验**——文中每个菜单路径、权限名、端点在代码中可检索到（抽查 grep）；无指向不存在页面的链接。
- [ ] **Step 3: Commit**——`docs(user-guide): AI 十篇对码校正——hub菜单路径/权限名/补管理员闭环与产出导入`。

---

### Task 15: user-guide 新增 5 篇 + 收编根目录门禁指南

**Files:**
- Create: `docs/user-guide/ai/action-gate.md`、`skills.md`、`runtime.md`、`mcp-servers.md`、`budgets.md`
- Delete: `docs/user-guide/ai-batch-gate-guide.md`（内容并入 action-gate.md）
- Modify: 引用根目录 guide 的页面改链新页（`grep -rn "ai-batch-gate-guide" docs/ src/ e2e/` 定位）

**Interfaces:**
- Consumes: 分册 05/03/10/08 的口径（用户指导不重复设计细节，只写操作路径）。

- [ ] **Step 1: 写 5 篇**——每篇结构：这是什么/入口（菜单路径+权限）/怎么用（步骤）/常见问题。
  - `action-gate.md`：三入口（批创建参数、模板携带、会话内 register_action_check）、pre 阻断 vs 终态核对、dry-run 试算位置（EditBatchConfigDialog）、门禁重试上限、审批收件箱（工作流「我的待办」AI 页签）；原根目录 guide 的有效内容并入。
  - `skills.md`：技能广场（AiSkillManager）、zip 上传、发布到运行时（publish+重启生效）、SkillOpt 拟合（AiSkillOpt：版本/诊断/重算）。
  - `runtime.md`：OpenCode 运行时页签（AiOpencodeRuntime）：总览/生效状态/apply/重启（force 需 `admin.ai_runtime_force`）。
  - `mcp-servers.md`：外部 MCP 注册（AiSettings 内 McpServersCard）：CRUD/健康检查/internal 开关。
  - `budgets.md`：预算维度（批/用户/API Key）、on_exceed=warn/drain/abort 行为、用量查看。
- [ ] **Step 2: 截图**——有运行环境则按现有惯例截图入库；无环境则在对应位置留 `<!-- TODO(screenshot): … -->` 占位注释（不伪造图片）。
- [ ] **Step 3: 自验**——`docs/user-guide/README.md` 目录补 5 篇条目；根目录 guide 已删且全树无活引用。
- [ ] **Step 4: Commit**——`docs(user-guide): 新增门禁/技能/运行时/外部MCP/预算五篇+收编根目录门禁指南`。

---

### Task 16: 归档处置 + 09-20 spec 取代横幅 + 全树链接修复

**Files:**
- Move→Archive（`git mv` 到 `docs/design/archive/`，顶部加 Global Constraints 横幅）：

| 文件 | 横幅指向 |
|------|----------|
| `ai/11-AI-trace-analyzer-skill-spec.md` | `../ai/06-执行审计与轨迹分析.md` |
| `ai/11-AI执行轨迹分析.md` | `../ai/06-执行审计与轨迹分析.md` |
| `ai/12-AI-Chat优化需求文档.md` | `../ai/02-对话助手.md` |
| `ai/13-AI批任务实时进度与编排设计.md` | `../ai/03-批任务与执行引擎.md` |
| `ai/AI动作门禁verifier判官核对设计.md` | `../ai/05-动作门禁与审批.md` |
| `ai/AI子任务动作账本与到位门禁设计.md` | `../ai/05-动作门禁与审批.md` |
| `ai/AI执行审计能力说明.md` | `../ai/06-执行审计与轨迹分析.md` |
| `ai/AI批任务容灾与恢复机制.md` | `../ai/11-容灾稳定性与压测.md` |
| `ai/AI批任务能力总览与创新点.md` | `../ai/03-批任务与执行引擎.md` |
| `ai/AI能力总览与长任务稳定性.md` | `../ai/11-容灾稳定性与压测.md` |
| `ai/OpenCode运行时依赖与部署.md` | `../ai/01-总体架构与运行时.md` |
| `ai/SkillOpt任务拟合设计.md` | `../ai/08-Skill与SkillOpt.md` |
| `ai/SkillOpt任务拟合页面重设计.md` | `../ai/08-Skill与SkillOpt.md` |

（`ai/AI功能架构图.svg` **不归档**，留在 ai/。）

- Modify: `docs/superpowers/specs/2026-09-20-ai-design-document-consolidation.md` 顶部加：`> ⚠️ **已被取代**（2026-10-04）：本方案从未执行，「全部并入 09 单文档」已被 [2026-10-04-ai-docs-restructure-design](./2026-10-04-ai-docs-restructure-design.md) 的总览+分册方案取代。`

- [ ] **Step 1: 逐文件 `git mv` + 加横幅**（13 文件）；移动后检查原相对链接内的图片路径仍有效（归档文件内引用 `../assets/` 的要改成 `../../` 层级正确）。
- [ ] **Step 2: 全树链接修复**——
```bash
grep -rn "design/ai/11-\|design/ai/12-\|design/ai/13-\|design/ai/AI\|design/ai/OpenCode\|design/ai/SkillOpt" docs/ --include="*.md" | grep -v archive
```
命中处改链对应分册；`design/README.md` 若 Task 13 后仍有残留链接一并清理。
- [ ] **Step 3: 自验**——`ls docs/design/ai/` 仅剩 00–12 + SVG；archive 新增 13 文件且首行为横幅；09-20 spec 横幅就位。
- [ ] **Step 4: Commit**——`docs(design): AI 旧专题文档 13 篇归档+横幅；09-20 整合 spec 标记取代`。

---

### Task 17: 验收自检与收口

**Files:**
- Modify: 仅当自检发现问题时修对应文档。

- [ ] **Step 1: 矩阵覆盖核验**

```bash
# 16 路由文件全部出现在 00 矩阵
for f in $(ls server/routes/ | grep -E '^ai(_.+)?\.py$|^open_api_ai_sessions\.py$'); do grep -q "$f" docs/design/ai/00-总览与功能清单.md || echo "MISSING: $f"; done
# 27 MCP 工具全部出现（工具名清单见「覆盖基线」）
grep -c "query_collection\|analyze_trace\|register_action_check" docs/design/ai/00-总览与功能清单.md
# 分册文件齐全
ls docs/design/ai/*.md | wc -l   # = 13
```

- [ ] **Step 2: 链接与横幅核验**——全树无指向已归档文件的活链接；13 个归档文件横幅在位；09-20 spec 横幅在位。
- [ ] **Step 3: 零代码核验**

```bash
git diff --stat 07b663a..HEAD -- . ':!docs' | wc -l   # 期望 0
```

- [ ] **Step 4: 问题修复**——自检命中即修对应文档并重跑该步。
- [ ] **Step 5: 收口 Commit**——`docs(design): AI 文档重构收口——验收自检通过`（如有修复则合并入本提交）。

---

## Self-Review 记录

- **Spec coverage**：spec §3 十三册→Task 1–12+13；§4 矩阵→Task 1+各册补行+Task 17 核验；§5 用户指导→Task 14/15；§6 处置→Task 16；§7 验收六条→Task 17。无缺口。
- **Placeholder scan**：Task 12 对 `/v1` 未确证三族的处置写明（核实存在性，不存在记「规划」）；截图占位规则写明。无 TBD。
- **类型一致性**：分册文件名在 Task 1 导航表、Task 13/16 链接、Task 17 核验中一致（`01-总体架构与运行时` … `12-开放API`）。
