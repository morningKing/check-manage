# AI 执行审计能力说明（Execution Audit）

**版本**：v1.0
**日期**：2026-09-18
**范围**：AI 执行审计的审计对象、采集范围、确定性判定规则、能力边界、输出与查看方式
**关联**：规格 [../superpowers/specs/2026-09-17-ai-execution-audit-skillopt-spec.md](../superpowers/specs/2026-09-17-ai-execution-audit-skillopt-spec.md) · 轨迹分析设计 [11-AI执行轨迹分析.md](./11-AI执行轨迹分析.md) · 架构图 [功能架构图.svg](./功能架构图.svg)

---

## 1. 定位与核心原则

执行审计回答的问题是：

> **一次 AI 执行的全生命周期里，谁、用什么配置、带着什么上下文、实际调用了哪些工具、哪里失败、失败后如何恢复、契约要求的步骤完成到什么程度。**

三条核心原则：

1. **事实层与推理层分离**——事件、配置、Prompt hash、工具状态由代码确定性采集与判定；"为什么失败"的语义解释才交给 LLM（`trace-analyzer` Skill），且必须引用已确认证据。
2. **三级结论，不伪造**——每条结论必须标注：
   | 状态 | 含义 |
   |---|---|
   | `confirmed` | 有事件/数据库字段/工具结果直接证明 |
   | `inferred` | 由消息/Todo/工具序列/LLM 推断，不能作为事实证明 |
   | `unknown_due_to_missing_data` | 无数据，禁止猜测（配 `limitations` 说明） |
3. **审计不阻塞执行**——所有采集 best-effort（失败仅告警），`EXECUTION_AUDIT_STRICT=1` 时才转为抛错（供测试）。

---

## 2. 审计对象与采集范围（事实层）

### 2.1 覆盖的执行来源

| source_type | 采集边界 | 说明 |
|---|---|---|
| `interactive` | `routes/ai_chat.py` 发送前 | 交互会话 |
| `batch` | `batch_engine._run_one` | 批任务子会话 |
| `scan` | 同上（带 scan_task_id） | AI 定时任务（经批通道） |
| `open_api` | 同上（带 api_key_id） | Open API 独立会话 |
| `kefu` | `kefu_public.py` 发送前 | 智能客服 |
| `trace_analysis` | `ai_session_admin.py` 分析派发 | 轨迹分析自身也被审计 |
| `row_action` | （预留，未接线） | 行操作 |

### 2.2 采集项明细

#### ① Execution Attempt（`ai_execution_attempts`）

一次"尝试执行"的完整生命周期（`attempt_no` 会话内递增，retry/continue 通过 `parent_attempt_id` 关联）：

| 字段组 | 内容 |
|---|---|
| 配置口径 | `requested_agent/effective_agent/agent_resolution`、`requested_model/effective_model/model_resolution`——resolution ∈ requested / session_default / batch_default / runtime_default / fallback / **unknown**（平台不知道就如实标，不伪造默认值） |
| Prompt 口径 | `raw_prompt_hash` / `effective_prompt_hash` / `effective_prompt_len`（SHA256） |
| 环境 | `workspace_path` / `workspace_config_hash` / `guidance_hash`（AGENTS.md） |
| 生命周期 | status：accepted/running/completed/failed/stopped/orphaned；起止时间；error_code/message |

#### ② Prompt 快照（`ai_execution_prompt_snapshots`）

- **默认只存** hash、长度与增强项清单（脱敏）；完整明文仅当 `EXECUTION_AUDIT_PROMPT_PLAINTEXT=1`；
- 增强项 `augmentations`：agent_directive、memory_injected、attachments（名称/路径/是否内联/内容 hash）、mentioned_agents、export_fallback、batch_directive、continue_prompt、provision_repo/ref；
- 明文查看需专用权限 `admin.ai_execution_prompt_read` 并记操作审计。

#### ③ 定义 Manifest（`ai_execution_manifests`）

本次执行 workspace **实际可见**的定义清单及 SHA256：

| kind | 来源 | 内容 |
|---|---|---|
| skill | session / platform_global / project / runtime_global | `.opencode/skills/<name>/SKILL.md` |
| agent | project / runtime_global / batch_config | `.opencode/agent/<name>.md` 或请求选择 |
| guidance | generated | `AGENTS.md` 项目指导 |

状态字段诚实分级：`injected`（已注入 workspace）≠ `runtime_loaded`（runtime 确认加载，当前默认 unknown）≠ `selected`（本次选择）≠ `invoked`（执行中观察到使用，当前默认 unknown）。**注入 ≠ 使用，不冒认。**

#### ④ 不可变事件（`ai_execution_events`）

按 attempt 追加、`event_seq` 递增（幂等唯一）：`tool.state`（每个工具调用的状态推进）、`message.updated`、`dispatch.ok / dispatch.failed / dispatch.recovered`、`session.idle / session.error`。携带 message_id / part_id / tool 关联与发生时间。

#### ⑤ 归一化轨迹（复用既有表）

`ai_chat_messages`（消息/工具调用 name-status-input-result-durationMs）、`ai_chat_subtasks(+messages)`、`ai_chat_session_files`、`ai_chat_messages.meta`（耗时/Token/成本）。

---

## 3. 确定性审计判定（规则层）

### 3.1 契约步骤审计（Execution Contract）

契约来源：SKILL.md frontmatter 的 `execution_contract:`（YAML，`explicit`，自动注册）或管理接口注册。步骤定义包含 `id / name / required / depends_on / expected_tools / success_conditions`。

对每个步骤，用「**契约要求 ↔ Agent Todo 声明 ↔ 实际工具证据**」三方比对，输出七态：

| 状态 | 判定条件 |
|---|---|
| `completed_confirmed` | expected_tools 中存在**成功**的工具调用证据（`message:<id>#tool<N>`） |
| `completed_claimed` | Todo 声明完成，但无对应工具证据（confidence 0.4） |
| `missing` | 必需步骤，Todo 未声明且无工具证据 |
| `out_of_order` | 步骤有证据，但其 `depends_on` 步骤的证据位置在其之后 |
| `failed` | 步骤工具调用为 error |
| `unknown_due_to_missing_data` | 可选步骤无证据 / 数据不足 |

Todo 侧由 `todo_trace.build_declared_plan` 从 `todowrite/todoread` 快照重建 `declared_plan`——**Agent 的自我声明，永远不作为执行事实**；Todo 声称完成但工具失败/无证据时，判 `completed_claimed` 并标注矛盾。

无契约的 Skill：步骤审计整体输出 `no_contract` → `unknown_due_to_missing_data`，**绝不把"没要求"误判成"遗漏"**。

### 3.2 工具失败审计（分类 + 恢复 + 影响）

对每个 `status=error` 的工具调用：

**失败类型**（按错误文本信号 + 耗时判定，十类）：

```text
invalid_input · permission_denied · resource_not_found · timeout(>30s)
network_error · provider_error · tool_internal_error
upstream_dependency_error · context_overflow · unknown
```

**恢复分析**：失败后下一个同工具调用——是否重试（`attempted`）、是否**同参数重试**（`same_input_retry`，无效重试信号）、是否换策略（`strategy_changed`）、是否恢复（`recovered`）。

**输出**：`{tool, failure_type, input_preview, result_preview, duration_ms, recovery, evidence_refs:["message:<id>#tool<N>"], evidence_level:"confirmed"}`。

### 3.3 子代理递归异常

`include_subtask_details=true` 时，对每个子代理内部的工具序列运行与主会话相同的检测器（循环/同参重试/超时/空输出），异常标注 `scope=subtask` + `subtask_id/agent`，并入总异常列表——子代理失败不再依赖 Skill"记得去看"。

### 3.4 基础异常与评分

- 基础异常：`tool_loop`（三连同参）、`invalid_retry`、`timeout`、`empty_output`、`unexpected_termination`、`subtask_failed`；
- 评分：四维确定性（任务完成/工具效率/资源效率/错误恢复），输出 **`status:"partial"`** + `pending_dimensions:["instruction_adherence","reasoning_quality"]`——**不用四维归一化伪装完整六维总分**；指令遵循/推理质量的语义判断留给 LLM 补全（inferred）。

### 3.5 数据完整性

七项 checklist（attempt 存在 / manifests / events / effective_model / prompt hash / todo 快照 / 契约）取占比得 `data_completeness.score`，附 `limitations` 清单（如"Skill 未定义契约，无法审计步骤遗漏"）——**数据不足时输出 unknown，不生成确定性结论**。

---

## 4. 能力边界（当前不审计什么）

| 边界 | 原因 | 当前表现 |
|---|---|---|
| Skill 被 runtime **真正加载/调用** | OpenCode 无原生 Skill 生命周期事件 | manifest 记 injected；`runtime_loaded/invoked` 标 unknown/inferred |
| 无契约 Skill 的"步骤遗漏" | 缺少"应该做什么"的依据 | 输出 `no_contract`，不做违规判定 |
| 审计上线前的旧会话 | 原始事件/Attempt 未采集 | 只读归一化快照，completeness 低分 + limitations |
| 推理质量/指令遵循的语义打分 | 需要语义理解 | 留给 LLM（inferred），服务端只给 `partial` 框架 |
| row_action 来源 | 尚未接线 | source_type 预留 |
| 业务结果正确性（回写字段值对不对） | 属业务语义 | 需 Skill/校验脚本二次确认 |

---

## 5. 输出与查看

### 5.1 诊断报告（结构化 JSON，`ai_execution_diagnoses.report`）

```json
{
  "execution": { "attempt_id": "...", "agent": {"requested":null,"effective":"data-agent","resolution":"requested"},
                 "model": {...}, "prompt": {"raw_hash":"...","effective_hash":"...","effective_len":1423},
                 "skills": [{"name":"demo-skill","contentHash":"...","injected":true}] },
  "contract": { "status": "complete",
                "steps": [{"step_id":"validate_schema","status":"missing","reason":"契约必需步骤，无声明也无工具证据"}],
                "violations": [{"type":"missing","step_id":"validate_schema","severity":"high"}] },
  "declared_plan": { "declared_steps": [...], "evidence_level": "declared_plan" },
  "tool_failures": [ { "tool":"query_collection", "failure_type":"resource_not_found",
                       "recovery": {"attempted":true,"same_input_retry":true,"recovered":false},
                       "evidence_refs": ["message:m1#tool0"] } ],
  "step_completion": { "required_total": 3, "required_completed_confirmed": 1, "rate": 0.33 },
  "data_completeness": { "score": 0.86, "limitations": [] }
}
```

### 5.2 查看入口

| 入口 | 内容 |
|---|---|
| 管理页「执行审计」抽屉 | Attempt 列表（Agent/Model/分辨率/Prompt 快照链接）、Manifest、契约步骤三色表、工具失败卡、Todo 声明计划、数据完整性、**轨迹分析历史**（analysis_session_id 直开） |
| `GET /ai/chat/admin/sessions/<sid>/execution-audit` | 抽屉数据源（attempts/manifests/events/report，按需构建诊断） |
| `GET .../execution-events?attemptId=` | 不可变事件时间线 |
| `GET .../execution-prompt?attemptId=` | Prompt 快照（hash/增强项；明文需 `admin.ai_execution_prompt_read` + 审计） |
| `GET /ai/chat/admin/analyses/<id>(/report)` | 分析任务状态与报告 |
| `GET /ai/chat/admin/skill-analytics` | Skill 维度聚合（invocations/completion_rate/failure_rate/avg_duration + 步骤统计）——SkillOpt 数据底座 |

### 5.3 存储与代码位置

| 层 | 位置 |
|---|---|
| 表 | `ai_execution_attempts / prompt_snapshots / manifests / events / contracts / step_results / diagnoses`（幂等迁移 `migrations/2026_09_17_execution_audit_tables.py`，随 `init_db` 与 **app 启动**自动执行） |
| 采集 | `server/utils/execution_audit.py`（create_attempt / record_event / save_prompt_snapshot / save_manifests / finish_latest_running） |
| 判定 | `server/utils/trace_auditor.py`（步骤七态 / 失败分类 / 恢复 / 报告构建）、`server/utils/todo_trace.py`（declared_plan）、`server/utils/execution_contract.py`（契约解析与存储） |
| 接线 | `routes/ai_chat.py`（interactive）、`batch_engine._run_one`（batch/scan/open_api）、`kefu_public.py`（kefu）、`routes/ai_session_admin.py`（trace_analysis）、`chat_persist._run_listener`（事件 + 回合收敛） |
| 测试 | `server/tests/test_execution_audit.py`（11 例）、`mcp-server/tests/test_analyze_trace.py`（41 例，含隔离/todo/partial） |

---

## 6. 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `EXECUTION_AUDIT_ENABLED` | `1` | 总开关（关闭后不采集，主链路不受影响） |
| `EXECUTION_AUDIT_STRICT` | `0` | 测试用：采集失败转为抛错 |
| `EXECUTION_AUDIT_PROMPT_PLAINTEXT` | `0` | 是否存储 Effective Prompt 明文（默认仅 hash/增强项） |

---

## 7. 演进方向

1. **runtime 事件**：OpenCode 插件提供 Skill load/invoke/step 事件，把 `invoked` 从 inferred 升级为 confirmed；
2. **SkillOpt 闭环**：基于 attempts/manifests 聚合的版本对比、建议应用与效果追踪（当前 `/skill-analytics` 为 v0 聚合）；
3. **诊断结果 LLM 补全**：instruction_adherence / reasoning_quality 由 trace-analyzer 会话补全并做 JSON Schema 校验；
4. **保留策略**：事件明文 30 天 / 摘要 180 天分层，Prompt 明文加密短保留；
5. **row_action 接线**与业务结果校验器（回写字段正确性确定性覆盖）。
