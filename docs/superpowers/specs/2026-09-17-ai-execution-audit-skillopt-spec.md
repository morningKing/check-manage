# AI 执行合规审计与 SkillOpt 优化闭环 Spec

**版本**：v1.0  
**状态**：待评审  
**日期**：2026-09-17  
**优先级**：P0（执行事实与安全）/ P1（合规审计）/ P2（SkillOpt）  
**范围**：AI Chat 交互会话、批任务子会话、AI 扫描任务、客服会话、Open API 会话、轨迹分析会话  
**本阶段**：定义开发规格，不实施代码修改

---

## 1. 背景

当前平台已经具备基础的 AI 执行轨迹能力：

- `mcp-server/tools/analyze_trace.py` 可以从数据库读取会话、消息、工具调用、子代理、性能和文件变更。
- `mcp-server/tools/query_sessions.py` 可以查询同类历史会话。
- `trace-analyzer` Skill 可以引导 OpenCode Agent 进行异常解释、成功案例对比和优化建议生成。
- `src/utils/todos.ts` 可以从 `todowrite/todoread` 快照中近似还原 Agent 声明的执行步骤。
- `server/utils/chat_persist.py` 和 `server/utils/batch_engine.py` 已经分别承担交互会话与批任务的消息持久化。

但当前能力本质上是：

> 基于落库 typed message 快照，由 OpenCode 内部 Agent 执行的启发式诊断。

它不是完整的执行审计系统，不能可靠证明：

1. 本次执行实际使用了哪个 Agent、Model 和 Skill。
2. Skill 是否被 runtime 加载、读取和调用。
3. Skill/Agent 要求的关键步骤是否全部执行。
4. Agent 是否跳过前置步骤、执行顺序是否错误。
5. 工具失败的真实类型、根因、恢复动作和下游影响。
6. 子代理结果是否被父 Agent 正确消费。
7. 某个 Skill 在历史执行中的成功率、异常率和版本差异。
8. 一条优化建议是否被应用，以及应用后是否有效。

本 Spec 将现有轨迹分析升级为两层能力：

```text
Execution Auditor
  对单次执行做事实级采集、步骤合规检查、工具失败诊断

SkillOpt
  对同一 Skill/Agent/Model 的历史执行聚合、版本对比和改进建议
```

核心原则是：

```text
先采集执行事实，再做规则审计，再做 LLM 根因解释，最后做历史优化聚合。
```

不得通过延长 Prompt 来弥补缺失的执行事实。

---

## 2. 当前实现与主要缺口

### 2.1 当前轨迹分析链路

```text
管理员在 AI 会话管理页点击“轨迹分析”
  → POST /ai/chat/admin/sessions/v2/<session_id>/analyze
  → 创建普通 ai_chat_sessions 分析会话
  → 注入启用的 global skills
  → 发送“使用 trace-analyzer 分析目标会话”的 Prompt
  → OpenCode Agent 调用 analyze_trace/query_sessions
  → LLM 生成普通 assistant 消息或 artifact
```

关键文件：

- `server/routes/ai_session_admin.py`
- `mcp-server/tools/analyze_trace.py`
- `mcp-server/tools/query_sessions.py`
- `skills/trace-analyzer/SKILL.md`
- `server/ai-workspaces/global-skills/trace-analyzer/SKILL.md`
- `src/views/admin/AiSessionAdmin.vue`
- `src/api/aiSessionAdmin.ts`

当前没有独立的：

- 分析任务表；
- `analysis_id → target_session_id` 关系；
- 执行 Attempt 表；
- 原始事件表；
- Skill 调用表；
- 诊断结果表；
- 建议反馈表；
- SkillOpt 聚合 API 和页面。

### 2.2 Skill 来源必须区分

| 来源 | 位置 | 当前语义 |
|---|---|---|
| 仓库源文件 | `skills/<name>/SKILL.md` | 源代码/部署副本，不会自动加载到 workspace |
| 平台 Skill | `AI_WORKSPACE_ROOT/global-skills/<name>/` | 启用后由 `inject_global_skills` 注入 workspace |
| OpenCode runtime Skill | `OPENCODE_GLOBAL_DIR/skill/<name>/` 或 `skills/<name>/` | serve 启动/重启时加载 |
| 会话上传 Skill | `<workspace>/.opencode/skills/<name>/` | 单个会话可见 |
| 项目 Skill | 批任务 provision repo 的 `<workspace>/.opencode/` | 批任务项目级可见 |
| 项目指导 | `<workspace>/AGENTS.md` | 指导文本，不是 Agent 定义 |
| Agent 定义 | `OPENCODE_GLOBAL_DIR/agent/<name>.md` 或项目 `.opencode/agent` | OpenCode Agent 定义 |

必须明确：

```text
source_available ≠ injected ≠ runtime_loaded ≠ selected ≠ invoked ≠ verified
```

仓库根目录 `skills/trace-analyzer/SKILL.md` 的存在不能证明目标会话已经加载或使用该 Skill。

### 2.3 当前数据事实与推断边界

所有诊断结论必须区分三种状态：

| 状态 | 定义 |
|---|---|
| `confirmed` | 有持久化事件、数据库字段或工具结果直接证明 |
| `inferred` | 由消息、Todo、工具序列或 LLM 推断，不能作为事实证明 |
| `unknown_due_to_missing_data` | 当前没有足够数据，禁止猜测 |

示例：

```json
{
  "status": "unknown_due_to_missing_data",
  "confidence": 0,
  "limitations": [
    "没有 Skill execution contract",
    "没有原始 tool event"
  ]
}
```

不得把以下推断当作事实：

- workspace 中存在 Skill ⇒ Skill 已被 Agent 使用；
- Todo 标记完成 ⇒ 工具实际成功；
- `ai_chat_sessions.agent/model` ⇒ 本次交互 Prompt 实际使用的 Agent/Model；
- 数据中没有某个步骤 ⇒ Agent 一定遗漏了步骤；
- 工具 `status=error` ⇒ 根因一定是工具本身。

---

## 3. 建设目标

### 3.1 P0：执行事实可信、可追溯、可隔离

1. 所有执行来源统一生成 `execution_attempt`。
2. 保存请求 Agent/Model 与实际生效 Agent/Model。
3. 保存有效 Prompt 的 hash、增强项和受控内容快照。
4. 保存实际可见的 Agent/Skill/Guidance manifest 及 SHA256。
5. 保存 OpenCode 原始事件或不可变事件摘要。
6. 保存 retry、continue、reexecute、abort 的关系。
7. 修复交互、批任务、扫描、客服、Open API 的记录口径差异。
8. MCP 轨迹查询按用户和角色隔离。
9. 分析失败时清理孤儿分析会话、workspace 和 listener。
10. 让 usage 缺失与真实零值可区分。

### 3.2 P1：执行合规审计

1. 将 Skill/Agent 定义转换为结构化 `Execution Contract`。
2. 将现有 Todo 轨迹定位为 Agent 的 `Declared Plan`。
3. 将工具调用、事件、文件和输出作为 `Observed Evidence`。
4. 检查必需步骤缺失、跳过、顺序错误、前置条件违规。
5. 检查工具失败类型、恢复动作和错误传播。
6. 检查父 Agent 与子 Agent 的委托、结果回传和结果消费。
7. 对每条结论提供证据引用、状态和置信度。
8. 对数据不完整的结论返回明确限制，不生成伪确定结论。

### 3.3 P2：SkillOpt 历史优化闭环

1. 按 Skill 名称、版本、来源、Agent、Model 和来源类型聚合执行历史。
2. 计算完成率、失败率、步骤遗漏率、异常率、时延、Token、成本。
3. 支持 Skill 版本 hash 的前后对比。
4. 生成绑定证据的 Skill/Prompt/Agent/配置改进建议。
5. 支持人工审核、应用记录和应用后效果反馈。
6. 样本不足时返回 `insufficient_data`，不生成高置信建议。

### 3.4 产品目标

管理员最终可以看到：

```text
本次执行使用了什么
  → 期望执行什么
  → Agent 声明计划做什么
  → 实际做了什么
  → 哪一步失败或遗漏
  → 工具为什么失败
  → 子代理是否正确协作
  → 这次执行质量如何
  → 该 Skill 历史表现如何
  → 建议如何改进以及改进后是否有效
```

---

## 4. 非目标

本 Spec 不包含：

- 立即替换 OpenCode runtime 或 Provider。
- 自动修改并覆盖生产环境 `SKILL.md`。
- 用 LLM 伪造缺失的原始事件。
- 将所有完整 Prompt 默认暴露给管理员。
- 用日志文件替代持久化执行事件。
- 重写现有聊天消息展示链路。
- 强制所有旧历史会话补齐不存在的原始事件。
- 在第一阶段实现全自动建议应用。
- 将所有业务领域的结果正确性一次性自动化验证。

---

## 5. 术语

| 术语 | 定义 |
|---|---|
| Execution Attempt | 一次实际尝试执行 Prompt/命令/批子任务的生命周期，包含首次执行、重试、继续和重新执行 |
| Execution Event | OpenCode 或平台采集的不可变事件/事件摘要 |
| Effective Prompt | 经过系统 directive、记忆、附件、@文件、批任务上下文等增强后实际发送给 OpenCode 的 Prompt |
| Raw User Intent | 用户原始输入，不含系统增强内容 |
| Skill Manifest | 本次执行可见或注入的 Skill 名称、来源、路径、hash 和状态快照 |
| Agent Manifest | 本次执行可见或选定的 Agent 定义、来源、路径、hash 和状态快照 |
| Execution Contract | Skill/Agent 对执行步骤、依赖、工具、禁止操作和成功条件的结构化要求 |
| Declared Plan | Agent 通过 Todo 工具声明的计划步骤 |
| Observed Evidence | 实际事件、工具调用、结果、文件、输出和状态证据 |
| Conformance Audit | 将 Contract、Raw Intent、Effective Prompt、Declared Plan、Observed Evidence 进行对比的确定性审计 |
| Failure Diagnosis | 对工具/Agent/Skill/Provider/上下文/业务错误的分类、根因、恢复和影响解释 |
| SkillOpt | 对 Skill 历史执行效果进行聚合、版本比较并生成改进建议的能力 |
| Evidence Ref | 指向 attempt/event/tool/message/subtask/step 的稳定证据引用 |
| Data Completeness | 当前执行数据足以支持诊断的程度，范围 0 到 1 |

---

## 6. 总体架构

### 6.1 目标架构

```text
交互 / 批任务 / 扫描 / 客服 / Open API / 分析会话
  ↓
统一 dispatch boundary
  ↓
创建 execution_attempt
  ↓
保存：Agent/Model、Effective Prompt 摘要、Manifest、Context Snapshot
  ↓
OpenCode 执行
  ├─ chat_persist 订阅交互/客服 SSE
  ├─ batch_engine 采集批任务 REST/attempt/子代理
  └─ runtime/plugin 采集 Skill load/invoke/step（可用时）
  ↓
保存不可变 execution_events
  ↓
生成 normalized trace projection（兼容现有聊天和 MCP）
  ↓
Execution Contract Auditor
  ├─ 步骤审计
  ├─ Todo 对齐
  ├─ 工具失败分类
  ├─ 恢复与错误传播
  └─ 子代理树审计
  ↓
LLM Root Cause Analyzer（只解释已确认证据）
  ↓
结构化 Diagnosis Report
  ↓
SkillOpt Aggregator
  ├─ 历史指标
  ├─ 版本对比
  ├─ 建议生成
  └─ 人工审核/效果反馈
```

### 6.2 事实层与推理层分离

| 层 | 负责内容 | 是否允许 LLM 推断 |
|---|---|---|
| 采集层 | 事件、请求、配置、manifest、Prompt hash、状态 | 否 |
| 归一化层 | typed tool/subtask/todo projection | 否 |
| 审计层 | 步骤是否出现、顺序、依赖、工具状态 | 否，使用确定性规则 |
| 诊断层 | 根因解释、错误传播、建议文案 | 是，但必须引用证据 |
| 聚合层 | 历史统计、版本 delta、样本置信度 | 否，使用确定性聚合 |
| 展示层 | 报告、时间线、详情 | 不改变事实 |

### 6.3 事实来源优先级

```text
runtime event/plugin evidence
  > dispatch boundary record
  > persisted OpenCode event summary
  > normalized message/tool snapshot
  > Todo declared plan
  > LLM inference
```

当高优先级来源缺失时，不能用低优先级来源伪装成高置信事实。

---

## 7. 执行来源覆盖矩阵

所有执行来源必须使用统一 Attempt 模型。

| source_type | 创建/执行边界 | Agent/Model 来源 | Skill/Guidance 来源 |
|---|---|---|---|
| `interactive` | `routes/ai_chat.py` 发送前 | 请求参数、默认配置、OpenCode runtime | 平台 global、会话上传、runtime global、AGENTS.md |
| `batch` | `batch_engine._run_one` | batch 配置、默认配置、项目 Agent | provision repo、平台 global、runtime global |
| `scan` | `ai_scan_engine.run_task` → batch child | scan 配置传播到 batch | 与 batch 相同 |
| `kefu` | `kefu_public.py` 发送前 | `kefu_instances.agent/model` | 客服 AGENTS.md、runtime/global 可见 Skill |
| `open_api` | Open API session dispatch | API key/session 配置 | workspace/runtime 可见 Skill |
| `row_action` | 行操作执行边界 | 行操作配置 | workspace/runtime 可见 Skill |
| `trace_analysis` | `ai_session_admin.py` 分析 dispatch | 显式分析模型、runtime Agent | 只注入 `trace-analyzer` 及必要依赖 |
| `subagent` | parent task 产生子代理时 | task/subagent type、runtime Agent | parent/workspace 可见 Skill |

### 7.1 分析会话特殊要求

轨迹分析不能继续只创建普通会话并依赖标题关联目标会话。必须创建专用分析关系：

```text
analysis_id
analysis_session_id
target_session_id
requested_by
status
requested_model
trace_version
report_id
created_at
completed_at
error_message
```

轨迹分析必须：

1. 预先确认 `trace-analyzer` 实际文件存在。
2. 计算并记录 Skill hash。
3. 只注入必要 Skill，不默认注入所有 global skills。
4. 显式传递分析模型给 `send_prompt_async`。
5. Skill 注入失败时 fail-closed。
6. 发送失败时清理 listener、workspace 和分析关系，或标记可重试失败状态。

---

## 8. 数据模型

### 8.1 `ai_execution_attempts`

建议新增表：

```sql
CREATE TABLE IF NOT EXISTS ai_execution_attempts (
  id                    VARCHAR(100) PRIMARY KEY,
  session_id            VARCHAR(100) NOT NULL
                         REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  source_type           VARCHAR(30) NOT NULL,
  source_id             VARCHAR(100),
  parent_attempt_id     VARCHAR(100)
                         REFERENCES ai_execution_attempts(id) ON DELETE SET NULL,
  attempt_no            INTEGER NOT NULL DEFAULT 1,
  operation             VARCHAR(30) NOT NULL,
  -- send | retry | continue | resume | reexecute | command | analysis
  requested_agent       VARCHAR(200),
  effective_agent       VARCHAR(200),
  requested_model       VARCHAR(300),
  effective_model       VARCHAR(300),
  agent_resolution      VARCHAR(30),
  -- requested | session_default | batch_default | runtime_default | fallback | unknown
  model_resolution      VARCHAR(30),
  raw_prompt_hash       VARCHAR(64),
  effective_prompt_hash VARCHAR(64),
  effective_prompt_len  INTEGER,
  prompt_version        VARCHAR(50),
  workspace_path        TEXT,
  workspace_config_hash VARCHAR(64),
  guidance_hash         VARCHAR(64),
  status                VARCHAR(30) NOT NULL DEFAULT 'accepted',
  -- accepted | running | completed | failed | stopped | orphaned | recovering
  error_code            VARCHAR(100),
  error_message         TEXT,
  started_at            TIMESTAMPTZ,
  finished_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ai_execution_attempt_session_no
  ON ai_execution_attempts(session_id, attempt_no);
CREATE INDEX IF NOT EXISTS ai_execution_attempt_source_idx
  ON ai_execution_attempts(source_type, source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_execution_attempt_status_idx
  ON ai_execution_attempts(status, created_at DESC);
```

约束：

- 同一个 `session_id + attempt_no` 唯一。
- retry/continue/reexecute 必须通过 `parent_attempt_id` 关联原 Attempt。
- `effective_agent`/`effective_model` 为空时必须有 `*_resolution='unknown'`，不能默认填“默认”。
- 终态 Attempt 不允许被普通更新覆盖，仅允许追加审计修正事件。

### 8.2 `ai_execution_prompt_snapshots`

有效 Prompt 可能包含长期记忆、附件全文和敏感业务数据，独立存储并受控访问：

```sql
CREATE TABLE IF NOT EXISTS ai_execution_prompt_snapshots (
  id                    VARCHAR(100) PRIMARY KEY,
  attempt_id            VARCHAR(100) NOT NULL
                         REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  raw_user_content      TEXT,
  effective_prompt      TEXT,
  raw_user_content_hash VARCHAR(64),
  effective_prompt_hash VARCHAR(64) NOT NULL,
  effective_prompt_len  INTEGER NOT NULL DEFAULT 0,
  augmentations        JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- agent_directive, memory, attachments, mentions, batch_hint, continue_prompt...
  context_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- memory_refs, attachment_refs, file_refs, skill_manifest_hash, config hash...
  redaction_status      VARCHAR(30) NOT NULL DEFAULT 'redacted',
  -- redacted | encrypted | plaintext_forbidden
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_prompt_snapshot_attempt_idx
  ON ai_execution_prompt_snapshots(attempt_id);
```

默认策略：

- 报告和普通管理员页面只展示 hash、长度、增强项类型和脱敏摘要。
- 完整有效 Prompt 需要专门权限 `admin.ai_execution_prompt_read`。
- 日志中不得写入完整 Prompt、Token、附件全文或长期记忆内容。

### 8.3 `ai_execution_manifests`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_manifests (
  id                VARCHAR(100) PRIMARY KEY,
  attempt_id        VARCHAR(100) NOT NULL
                    REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  kind              VARCHAR(30) NOT NULL,
  -- skill | agent | guidance | mcp | workspace_config
  name              VARCHAR(300) NOT NULL,
  source            VARCHAR(40) NOT NULL,
  -- runtime_global | platform_global | project | session | generated
  path              TEXT,
  content_hash      VARCHAR(64),
  version_label      VARCHAR(200),
  injected           BOOLEAN NOT NULL DEFAULT FALSE,
  injection_status   VARCHAR(30) NOT NULL DEFAULT 'unknown',
  -- success | failed | skipped | unknown
  runtime_loaded     VARCHAR(30) NOT NULL DEFAULT 'unknown',
  -- confirmed | not_loaded | unknown
  selected           VARCHAR(30) NOT NULL DEFAULT 'unknown',
  -- requested | default | not_selected | unknown
  invoked            VARCHAR(30) NOT NULL DEFAULT 'unknown',
  -- confirmed | inferred | not_observed | unknown
  evidence_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_execution_manifest_name_idx
  ON ai_execution_manifests(name, content_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_execution_manifest_attempt_idx
  ON ai_execution_manifests(attempt_id);
```

### 8.4 `ai_execution_events`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_events (
  id                VARCHAR(150) PRIMARY KEY,
  attempt_id        VARCHAR(100) NOT NULL
                    REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  event_seq         BIGINT NOT NULL,
  event_type        VARCHAR(80) NOT NULL,
  -- message.updated | part.updated | part.delta | session.idle |
  -- session.error | tool.started | tool.completed | subtask.started |
  -- subtask.completed | skill.loaded | skill.invoked | step.changed
  occurred_at       TIMESTAMPTZ,
  received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  session_id        VARCHAR(100),
  parent_session_id VARCHAR(100),
  message_id        VARCHAR(200),
  part_id           VARCHAR(200),
  tool_call_id      VARCHAR(200),
  parent_part_id    VARCHAR(200),
  subtask_id        VARCHAR(200),
  status            VARCHAR(40),
  payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
  redaction_status  VARCHAR(30) NOT NULL DEFAULT 'redacted',
  UNIQUE(attempt_id, event_seq)
);

CREATE INDEX IF NOT EXISTS ai_execution_event_attempt_seq_idx
  ON ai_execution_events(attempt_id, event_seq);
CREATE INDEX IF NOT EXISTS ai_execution_event_tool_idx
  ON ai_execution_events(attempt_id, tool_call_id, event_seq);
CREATE INDEX IF NOT EXISTS ai_execution_event_time_idx
  ON ai_execution_events(occurred_at);
```

事件保留策略：

- 事件按 append-only 写入，不使用最终快照覆盖历史。
- 事件 payload 必须脱敏，敏感字段不能直接写入日志或普通 JSON。
- 无法获得原始 runtime event 时，允许写入 `event_type='normalized_snapshot'`，但必须标记 `evidence_level='inferred'` 或 `source='normalized'`。

### 8.5 `ai_execution_contracts`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_contracts (
  id                VARCHAR(100) PRIMARY KEY,
  name              VARCHAR(300) NOT NULL,
  owner_type        VARCHAR(30) NOT NULL,
  -- skill | agent | task_template | analysis
  owner_id          VARCHAR(300),
  source            VARCHAR(30) NOT NULL,
  -- explicit | inferred | generated
  content_hash      VARCHAR(64),
  schema_version    VARCHAR(30) NOT NULL DEFAULT 'v1',
  contract          JSONB NOT NULL,
  confidence        NUMERIC(4,3),
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_execution_contract_owner_idx
  ON ai_execution_contracts(owner_type, owner_id, active);
```

Contract 最小结构：

```json
{
  "steps": [
    {
      "id": "read_input",
      "name": "读取输入文件",
      "required": true,
      "depends_on": [],
      "expected_tools": ["read", "read_upload"],
      "forbidden_tools": [],
      "success_conditions": ["input_content_available"]
    },
    {
      "id": "validate_schema",
      "name": "校验字段结构",
      "required": true,
      "depends_on": ["read_input"],
      "expected_tools": ["run_python"],
      "success_conditions": ["schema_validated"]
    }
  ],
  "forbidden_tools": [],
  "success_conditions": ["final_response_contains_summary"]
}
```

对于从 Markdown 推断的 Contract，必须标记：

```json
{
  "source": "inferred",
  "confidence": 0.68
}
```

不能把 LLM 推断的 Contract 当成 Skill 明确契约。

### 8.6 `ai_execution_step_results`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_step_results (
  id                 VARCHAR(120) PRIMARY KEY,
  attempt_id         VARCHAR(100) NOT NULL
                     REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  contract_id        VARCHAR(100)
                     REFERENCES ai_execution_contracts(id) ON DELETE SET NULL,
  step_id            VARCHAR(200) NOT NULL,
  expected           BOOLEAN NOT NULL DEFAULT FALSE,
  declared_by_agent  BOOLEAN NOT NULL DEFAULT FALSE,
  observed           BOOLEAN NOT NULL DEFAULT FALSE,
  status             VARCHAR(40) NOT NULL,
  -- completed_confirmed | completed_claimed | missing | skipped |
  -- out_of_order | failed | unknown_due_to_missing_data
  evidence_level     VARCHAR(30) NOT NULL,
  -- confirmed | inferred | unknown
  confidence         NUMERIC(4,3),
  started_at         TIMESTAMPTZ,
  finished_at        TIMESTAMPTZ,
  duration_ms        INTEGER,
  evidence_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
  reason             TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(attempt_id, step_id)
);

CREATE INDEX IF NOT EXISTS ai_execution_step_status_idx
  ON ai_execution_step_results(status, created_at DESC);
```

### 8.7 `ai_execution_diagnoses`

```sql
CREATE TABLE IF NOT EXISTS ai_execution_diagnoses (
  id                  VARCHAR(100) PRIMARY KEY,
  attempt_id          VARCHAR(100)
                      REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  analysis_id         VARCHAR(100),
  status              VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | running | completed | partial | failed
  data_completeness   NUMERIC(4,3),
  report              JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_schema       VARCHAR(30) NOT NULL DEFAULT 'v1',
  root_cause_status   VARCHAR(30) NOT NULL DEFAULT 'unknown',
  -- confirmed | inferred | unknown
  created_by          VARCHAR(100),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at        TIMESTAMPTZ,
  error_message       TEXT
);

CREATE INDEX IF NOT EXISTS ai_execution_diagnosis_attempt_idx
  ON ai_execution_diagnoses(attempt_id, created_at DESC);
```

### 8.8 `ai_skill_metrics` 与反馈表（P2）

P2 可以新增聚合表或物化视图：

```sql
CREATE TABLE IF NOT EXISTS ai_skill_suggestion_feedback (
  id                    VARCHAR(100) PRIMARY KEY,
  diagnosis_id          VARCHAR(100),
  suggestion_id         VARCHAR(200) NOT NULL,
  action                VARCHAR(30) NOT NULL,
  -- accepted | rejected | modified | applied | rolled_back
  applied_value         TEXT,
  applied_by            VARCHAR(100),
  applied_at            TIMESTAMPTZ,
  effect_tracked        BOOLEAN NOT NULL DEFAULT FALSE,
  before_metrics        JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Skill 历史聚合至少支持：

```text
skill_name
skill_hash
source_type
agent
model
period
sample_size
completion_rate
failure_rate
required_step_completion_rate
anomaly_rate
avg_duration_ms
p95_duration_ms
avg_tokens
avg_cost
score_average
score_p50
score_p95
```

---

## 9. 数据采集边界

### 9.1 Dispatch Boundary

必须在 OpenCode 请求发送前记录 Attempt，不能只在 listener 中事后推断。

覆盖：

- `routes/ai_chat.py` 交互发送；
- `server/utils/batch_engine.py:_run_one`；
- `ai_scan_engine.py` 扫描任务生成批任务的边界；
- `kefu_public.py` 客服发送；
- Open API 执行；
- `ai_session_admin.py` 轨迹分析发送；
- command/run 等非普通 Prompt 执行。

采集步骤：

1. 生成 `attempt_id`。
2. 计算 requested/effective Agent 和 Model。
3. 组装 Effective Prompt。
4. 生成 raw/effective prompt hash。
5. 收集 Agent/Skill/Guidance manifest。
6. 写 Attempt、Prompt Snapshot、Manifest。
7. 再调用 `send_prompt_async` 或等价 OpenCode API。
8. 将 dispatch 成功/失败写为事件。

### 9.2 Interactive/客服 Listener

`chat_persist` 必须在保留 normalized content 的同时，追加不可变 event：

- `message.updated`；
- `message.part.updated`；
- `message.part.delta`；
- `session.idle`；
- `session.error`；
- question 事件；
- tool/task/subtask 元数据。

未知 part 不得静默丢弃：

- 原始事件写入 `ai_execution_events`；
- normalized projection 可以返回 `null`；
- 报告标记 `unsupported_event_type`。

### 9.3 Batch/Scan

`batch_engine` 在每个子会话的：

- claim；
- OpenCode session 创建；
- dispatch；
- retry；
- continue/resume；
- abort；
- REST snapshot；
- persist conversation；
- finished/failed；

都必须写 Attempt/Event。

扫描任务必须保留：

```text
scan_task_id
batch_id
source_record_id
scan prompt/template hash
agent/model snapshot
```

不能只把扫描 Agent 传播到 batch 后丢失来源。

### 9.4 子代理

创建子代理时写入：

- `parent_attempt_id`；
- `parent_session_id`；
- `parent_part_id`；
- `parent_subtask_id`；
- `depth`；
- 子代理 Prompt hash；
- Agent/Model snapshot。

子代理消息 upsert 必须写 `meta_from_info(info)`，并区分：

```text
usage_available=true
usage_available=false
```

### 9.5 Skill Runtime/Plugin

如果 OpenCode runtime/plugin 可以提供 Skill load/invoke/step 事件，优先采集：

- Skill loaded；
- Skill invoked；
- Skill step started；
- Skill step completed；
- Skill step failed；
- Skill step skipped。

如果 runtime 无法提供这些事件，平台只能记录：

- injected manifest；
- prompt heuristic；
- tool/evidence heuristic；

并必须在报告中声明：

```text
runtime_loaded = unknown
invoked = inferred
```

不能声称已确认 Skill 实际被调用。

---

## 10. Effective Prompt 审计

### 10.1 Prompt 分层

每个 Attempt 必须区分：

```text
raw_user_content
system_or_agent_directive
effective_prompt
prompt_augmentations
context_snapshot
```

### 10.2 增强项结构

`prompt_augmentations` 至少支持：

```json
{
  "agent_directive": true,
  "memory_injected": true,
  "memory_refs": ["memory_xxx"],
  "attachments": [
    {
      "name": "data.csv",
      "path": "uploads/data.csv",
      "content_inlined": true,
      "content_hash": "sha256..."
    }
  ],
  "mentioned_files": ["src/schema.ts"],
  "batch_input_hint": true,
  "continue_prompt": false,
  "export_fallback": false,
  "question_context": false
}
```

### 10.3 Prompt 安全

- 完整 Prompt 默认不在普通报告中显示。
- 完整 Prompt 需要 `admin.ai_execution_prompt_read` 权限。
- 文件全文、长期记忆和外部返回内容必须脱敏或加密。
- 日志只能写 hash、长度、引用类型和脱敏摘要。
- Prompt hash 采用 SHA256，便于 retry/continue 前后对比。

---

## 11. Execution Contract

### 11.1 Contract 来源

优先级：

1. Skill/Agent 明确的结构化 contract；
2. Skill frontmatter 扩展的 `execution_contract`；
3. 平台任务模板定义；
4. 从 Markdown 生成的 inferred contract；
5. 无 contract 时返回 unknown，不强行审计步骤遗漏。

### 11.2 Skill frontmatter 扩展示例

```yaml
---
name: data-import
description: 导入数据并生成结果
contract_version: v1
execution_contract:
  steps:
    - id: read_input
      name: 读取输入文件
      required: true
      expected_tools: [read, read_upload]
    - id: validate_schema
      name: 校验字段结构
      required: true
      depends_on: [read_input]
      expected_tools: [run_python]
    - id: save_result
      name: 保存结果文件
      required: true
      depends_on: [validate_schema]
      expected_tools: [save_artifact]
  success_conditions:
    - output_file_exists
    - final_response_contains_summary
---
```

### 11.3 步骤状态

| 状态 | 含义 |
|---|---|
| `completed_confirmed` | 有明确工具/事件成功证据 |
| `completed_claimed` | Agent/Todo 声明完成，但没有足够观察证据 |
| `missing` | Contract 要求且没有声明或观察证据 |
| `skipped` | 有前置/执行证据，但必需步骤被跳过 |
| `out_of_order` | 依赖步骤未完成就执行当前步骤 |
| `failed` | 有失败工具/事件证据 |
| `unknown_due_to_missing_data` | 采集数据不足，不能判断 |

### 11.4 审计规则

确定性审计至少包括：

1. 必需步骤是否出现。
2. 步骤依赖是否满足。
3. 工具是否符合 `expected_tools`。
4. 是否使用 `forbidden_tools`。
5. 工具结果是否成功。
6. Todo 状态是否与工具证据一致。
7. 是否满足输出成功条件。
8. 子代理是否完成并回传结果。
9. 父 Agent 是否在后续消息中使用子代理结果。
10. 执行是否在会话终态前完整收敛。

---

## 12. 工具失败与根因分析

### 12.1 失败类型

第一版至少支持：

```text
invalid_input
permission_denied
resource_not_found
timeout
network_error
provider_error
tool_internal_error
upstream_dependency_error
context_overflow
cancelled
unknown
```

### 12.2 确定性分类信号

| 失败类型 | 优先证据 |
|---|---|
| `invalid_input` | 参数校验错误、schema 错误、工具返回参数提示 |
| `permission_denied` | 401/403、权限错误码、访问拒绝文本 |
| `resource_not_found` | 404、文件/集合/记录不存在 |
| `timeout` | duration 超阈值、超时异常、OpenCode timeout |
| `network_error` | connection reset、DNS、HTTP transport error |
| `provider_error` | provider/API 错误码、模型服务错误 |
| `context_overflow` | context length、token limit、prompt too long |
| `tool_internal_error` | MCP/tool 500 或内部异常 |
| `unknown` | 没有足够结构化错误证据 |

### 12.3 恢复分析

每个失败事件需要关联后续 Attempt/Event，判断：

```json
{
  "attempted": true,
  "strategy_changed": false,
  "same_input_retry": true,
  "alternative_tool_used": false,
  "recovered": false
}
```

### 12.4 下游影响

审计器需要沿事件和步骤依赖计算：

- 哪些后续步骤依赖失败结果；
- 是否仍生成最终结果；
- 结果是否不完整；
- 子代理失败是否导致主任务失败；
- 是否发生错误传播。

LLM 只能解释已确认的失败链，不能自行生成不存在的事件。

---

## 13. 结构化诊断报告

### 13.1 报告顶层结构

```json
{
  "schema_version": "v1",
  "diagnosis_id": "diag_xxx",
  "attempt_id": "attempt_xxx",
  "target_session_id": "sess_xxx",
  "status": "completed",
  "data_completeness": {
    "score": 0.82,
    "missing": ["runtime_skill_invoke_event"],
    "limitations": ["Skill invoked 状态只能推断"]
  },
  "execution": {},
  "contract": {},
  "declared_plan": {},
  "observed_evidence": {},
  "tool_failures": [],
  "subagent_audit": {},
  "scores": {},
  "root_cause": {},
  "suggestions": [],
  "evidence_index": []
}
```

### 13.2 Execution

```json
{
  "session_id": "sess_xxx",
  "attempt_id": "attempt_xxx",
  "source_type": "batch",
  "source_id": "batch_xxx",
  "agent": {
    "requested": "data-agent",
    "effective": "data-agent",
    "status": "confirmed"
  },
  "model": {
    "requested": "provider/model",
    "effective": "provider/model",
    "status": "confirmed"
  },
  "skills": [
    {
      "name": "data-import",
      "source": "platform_global",
      "hash": "sha256...",
      "injected": true,
      "runtime_loaded": "unknown",
      "invoked": "inferred",
      "evidence_refs": ["event_3"]
    }
  ]
}
```

### 13.3 证据引用

每个结论必须可以引用：

```json
{
  "id": "event_21",
  "kind": "execution_event",
  "attempt_id": "attempt_xxx",
  "event_seq": 21,
  "label": "query_collection returned invalid input",
  "redacted_preview": "..."
}
```

### 13.4 评分

如果六维不完整：

```json
{
  "status": "partial",
  "total": null,
  "grade": null,
  "computed_dimensions": 4,
  "pending_dimensions": [
    "instruction_adherence",
    "reasoning_quality"
  ]
}
```

不能将四维归一化结果伪装成完整六维总分。

### 13.5 建议

```json
{
  "id": "suggestion_xxx",
  "type": "skill_update",
  "target": "skill:data-import:SKILL.md",
  "description": "增加字段校验步骤，并将其设为查询数据的前置条件",
  "suggested_value": "...",
  "effort": "low",
  "impact": "high",
  "confidence": 0.89,
  "auto_applicable": false,
  "evidence_refs": ["step:validate_schema", "event_21"],
  "expected_metrics": ["failure_rate", "completion_rate"]
}
```

第一版禁止自动覆盖 Skill 文件。

---

## 14. SkillOpt 设计

### 14.1 输入

```text
skill_name
skill_hash（可选）
source_type（可选）
agent（可选）
model（可选）
created_after/created_before
session_ids（可选）
```

如果没有 Skill invocation 数据，第一版只允许：

- 显式传入 session_ids；或
- 明确标记为 heuristic 样本。

不能把 Prompt 中出现 Skill 名称当成可靠调用记录。

### 14.2 聚合指标

```text
invocations
sample_size
completion_rate
failure_rate
required_step_completion_rate
claimed_but_unverified_rate
anomaly_rate
avg_duration_ms
p95_duration_ms
avg_tokens
avg_cost
score_average
score_p50
score_p95
failure_type_distribution
```

### 14.3 版本对比

至少对比：

```text
completion_rate delta
failure_rate delta
step_completion_rate delta
anomaly_rate delta
p95_duration delta
tokens delta
cost delta
six_dimension_score delta
```

无 baseline 或样本数不足时：

```json
{
  "status": "insufficient_data",
  "confidence": 0.22,
  "suggestions": []
}
```

### 14.4 建议生成原则

建议必须：

1. 绑定真实证据；
2. 指明目标 Skill/Agent/Prompt/配置；
3. 给出预期改善指标；
4. 给出置信度；
5. 区分自动适用与人工审核；
6. 不得因为一个失败样本直接建议修改 Skill。

### 14.5 建议反馈

用户可以：

- 接受；
- 拒绝；
- 修改；
- 应用；
- 回滚。

应用后按相同指标跟踪：

- 成功率；
- 步骤完成率；
- 异常率；
- P95 耗时；
- Token/成本。

---

## 15. API 设计

### 15.1 保留现有触发接口

```text
POST /ai/chat/admin/sessions/v2/<session_id>/analyze
```

兼容返回：

```json
{
  "analysisId": "analysis_xxx",
  "analysisSessionId": "sess_xxx",
  "status": "accepted"
}
```

原有 `analysisSessionId` 保留，新增 `analysisId`。

### 15.2 分析状态

```text
GET /ai/chat/admin/analyses/<analysis_id>
```

返回：

```json
{
  "analysisId": "analysis_xxx",
  "targetSessionId": "sess_target",
  "analysisSessionId": "sess_analysis",
  "status": "pending|running|completed|partial|failed",
  "diagnosisId": "diag_xxx",
  "createdAt": "...",
  "completedAt": "...",
  "error": null
}
```

### 15.3 结构化报告

```text
GET /ai/chat/admin/analyses/<analysis_id>/report
```

支持：

- `includeEvents=false` 默认不返回原始事件；
- `includeEvidence=true` 返回脱敏证据索引；
- `includePrompt=false` 默认不返回完整 Prompt；
- `includePrompt=true` 需要 `admin.ai_execution_prompt_read`。

### 15.4 执行时间线

```text
GET /ai/chat/admin/sessions/<session_id>/execution-timeline
```

参数：

```text
attemptId
fromSeq
toSeq
includePayload
```

### 15.5 SkillOpt 聚合

```text
GET /ai/chat/admin/skill-analytics
GET /ai/chat/admin/skill-analytics/<skill_name>
POST /ai/chat/admin/skill-analytics/<skill_name>/suggestions
POST /ai/chat/admin/skill-suggestions/<suggestion_id>/feedback
```

权限：

- 查询：`admin.ai_execution_audit_read`
- 查看完整 Prompt/敏感证据：`admin.ai_execution_prompt_read`
- 生成建议：`admin.ai_skill_opt_write`
- 应用建议：`admin.ai_skill_opt_apply`
- 跨用户分析：`admin.ai_chat_admin`

---

## 16. 权限与安全

### 16.1 MCP 数据隔离

`analyze_trace` 和 `query_sessions` 必须使用 `ToolContext`：

```text
普通用户：session.user_id == ctx.user_id
客服：只允许授权会话/客服实例
管理员：允许跨用户，但必须有 admin.ai_chat_admin
内部分析会话：使用 target session 的授权范围，不继承普通用户任意访问
```

违反权限返回结构化错误：

```json
{
  "code": "TRACE_FORBIDDEN",
  "message": "无权读取该会话轨迹",
  "retryable": false
}
```

### 16.2 敏感数据

不得在日志中记录：

- JWT/session token；
- 完整 Prompt；
- 附件全文；
- 长期记忆全文；
- API key、password、cookie、authorization；
- 未脱敏业务数据。

Evidence/Prompt 详情需要：

- 字段级脱敏；
- 按权限返回；
- 访问审计；
- 可配置保留期。

### 16.3 数据保留

建议默认：

- execution attempts：长期保留；
- normalized events：180 天；
- raw payload：30 天或加密归档；
- Prompt 完整正文：默认不保留或加密保留 30 天；
- 诊断报告：与会话审计策略一致；
- SkillOpt 聚合：长期保留，保留版本 hash。

---

## 17. 迁移与兼容策略

### 17.1 新旧数据共存

旧会话没有完整执行事实，不做伪造回填。

旧会话分析报告必须包含：

```json
{
  "data_completeness": {
    "score": 0.35,
    "limitations": [
      "无原始事件",
      "无 Attempt",
      "无 Skill manifest",
      "无 Effective Prompt"
    ]
  }
}
```

### 17.2 旧数据投影

可以从旧表生成低置信度 normalized projection：

- 工具快照；
- Todo 声明计划；
- 子代理概要；
- 主会话 usage；
- 文件状态。

必须标记：

```text
source=legacy_normalized
confidence=inferred
```

### 17.3 数据库迁移

迁移顺序：

1. 新建 attempts/events/manifests/prompt/contract/step/diagnosis 表；
2. 加索引和权限；
3. 添加 feature flag；
4. 先对新执行写入，旧执行只读；
5. 验证性能和数据完整性；
6. 再开启审计器和 SkillOpt；
7. 最后启用结构化报告页。

所有 DDL 必须幂等，可重复执行。

---

## 18. 性能与可靠性

### 18.1 采集性能

- execution event 写入不能阻塞 OpenCode dispatch。
- 允许异步批量写入，但 Attempt 状态必须先写 `accepted`。
- 事件写入失败不能静默丢失，必须有 retry/dead-letter 状态。
- 单个 Attempt 原始 payload 总大小上限可配置，超限保存摘要和 hash。

### 18.2 分析性能

`analyze_trace` 必须支持：

- 使用 `seq` 分页；
- 子代理按需加载；
- event payload 总上限；
- 主轨迹与详情二阶段查询；
- 单次报告 token 预算；
- 超大结果返回 `truncated=true` 和详情 API。

### 18.3 幂等

- `attempt_id` 由平台生成并作为幂等键。
- 相同 `attempt_id + event_seq` 不重复写入。
- 同一分析任务不可并行生成多个 active report，除非明确 `force=true`。
- SkillOpt 聚合按时间窗口和 hash 可重复计算。

---

## 19. 可观测性

每个执行和分析操作至少记录：

```text
sessionId
attemptId
analysisId
diagnosisId
sourceType
agent
model
skillHash
requestId
```

必须记录：

- Attempt 创建、开始、完成、失败；
- dispatch 成功/失败；
- Skill manifest 注入结果；
- runtime load/invoke 结果；
- event 写入失败；
- 审计器完成/部分完成；
- LLM 根因分析失败；
- 报告 JSON 校验失败；
- 建议生成、接受、应用、回滚；
- SkillOpt 聚合样本不足。

日志中不得记录完整 Prompt 和敏感 payload。

---

## 20. 实施阶段

### P0.1：可信度与安全修复

- MCP trace/query 权限隔离；
- 分析显式传 model；
- Skill 注入 fail-closed；
- 分析关系表；
- 分析失败清理；
- `seq` 排序；
- source/api key/kefu 修复；
- 子代理 prompt/meta/parent linkage；
- 四维评分 `partial` 语义；
- usage 缺失与 0 区分。

### P0.2：统一 Attempt 与 Prompt/Manifest

- 创建 `ai_execution_attempts`；
- 接入交互、批、扫描、客服、Open API、分析；
- 保存 Effective Prompt hash/augmentations；
- 保存 Agent/Skill/Guidance manifest；
- 保存 retry/continue/reexecute 关系。

### P1.1：事件采集

- `chat_persist` 保存交互/客服 event envelope；
- `batch_engine` 保存批 REST/attempt/子代理事件；
- 记录工具、message、part、parent、时间、错误；
- 事件脱敏、异步写入、幂等和重试。

### P1.2：Execution Contract 与审计器

- Contract schema 和 Skill frontmatter 扩展；
- Markdown inferred contract；
- Todo declared plan projection；
- step matching、dependency、order、tool、output 审计；
- tool failure taxonomy、recovery、downstream impact；
- 子代理树和结果消费审计。

### P1.3：结构化诊断报告

- analysis job/status/report API；
- JSON Schema 校验；
- evidence refs；
- data completeness；
- partial score；
- 专用报告页面；
- 保留分析聊天作为解释入口。

### P2.1：SkillOpt 聚合

- Skill invocation heuristics/runtime events；
- hash/version 聚合；
- 成功率、步骤完成率、异常率、资源指标；
- 样本置信度；
- 版本 delta。

### P2.2：建议和反馈

- 建议 schema；
- evidence refs；
- 人工审核；
- 接受/拒绝/修改/应用/回滚；
- 应用前后指标跟踪；
- 禁止第一版自动覆盖生产 Skill。

---

## 21. 测试要求

### 21.1 数据采集单元测试

覆盖：

- 每种 source_type 创建 Attempt；
- requested/effective Agent/Model；
- default/fallback resolution；
- Prompt hash 和增强项；
- Skill/Agent manifest hash；
- retry/continue/reexecute 关系；
- 敏感字段脱敏；
- event_seq 幂等；
- event payload 超限。

### 21.2 Listener/Batch 测试

覆盖：

- `message.updated`、`part.updated`、`part.delta`、idle、error；
- tool call start/completed/error；
- subtask parent linkage；
- child meta duration/token/cost；
- 失败重试和继续；
- 扫描任务到 batch 到 child 的 source 传播；
- 客服 Agent/Model snapshot。

### 21.3 审计器测试

固定 fixture 覆盖：

- required step completed；
- missing step；
- skipped prerequisite；
- out-of-order；
- Todo claimed but no evidence；
- tool failed；
- invalid retry；
- changed strategy recovery；
- forbidden tool；
- output condition failed；
- child result not consumed；
- unknown due to missing data。

### 21.4 诊断和评分测试

覆盖：

- confirmed/inferred/unknown 三类状态；
- evidence_refs 完整；
- data completeness 正确；
- 四维 partial 不输出伪六维 total；
- 完整六维 total/grade；
- LLM JSON schema 失败降级；
- 样本不足不生成高置信建议。

### 21.5 SkillOpt 测试

覆盖：

- 同一 Skill 多次调用聚合；
- 不同 hash 版本对比；
- source/agent/model 分组；
- P95、成本、Token、步骤完成率；
- 样本不足；
- 建议证据引用；
- 接受/拒绝/应用/回滚和效果指标。

### 21.6 Playwright E2E

必须验证：

1. 管理员触发分析并得到 `analysisId`。
2. 分析页面显示 target session 关系。
3. 轨迹时间线展示 Attempt 和事件。
4. Agent/Model/Skill manifest 正确显示。
5. Effective Prompt 默认脱敏，授权后可查看。
6. Todo declared plan 与 observed evidence 对比正确。
7. 缺失步骤显示 `missing` 或 `unknown_due_to_missing_data`，不误报 confirmed。
8. 工具失败显示类型、根因、恢复和证据。
9. 子代理树显示父子关系和 usage 缺失状态。
10. 四维评分显示 partial，不伪装完整六维。
11. 完整评分显示 total/grade 和证据。
12. SkillOpt 样本不足显示“数据不足”。
13. 建议必须显示 evidence refs 和人工审核状态。
14. 无权限用户不能读取他人轨迹或完整 Prompt。
15. 分析发送失败不会遗留孤儿会话和 workspace。

---

## 22. 验收标准

### P0 执行事实

1. 交互、批、扫描、客服、Open API、分析会话均能创建统一 Attempt。
2. 每个 Attempt 能查询 requested/effective Agent/Model，未知时明确标记 unknown。
3. 每个 Attempt 能查询 Prompt hash 和增强项。
4. 每个 Attempt 能查询 Agent/Skill/Guidance manifest。
5. retry/continue/reexecute 与父 Attempt 有明确关系。
6. 原始事件或明确标记为 normalized projection 的事件可查询。
7. MCP 工具不能越权读取其他用户轨迹。
8. 分析失败会收敛状态，不产生无主资源。

### P1 执行合规

1. Skill/Agent contract 能定义 required steps、依赖、工具和成功条件。
2. Todo 作为 declared plan 与 observed evidence 分开存储。
3. 审计器能区分 completed_confirmed、completed_claimed、missing、skipped、out_of_order、failed、unknown。
4. 工具失败能输出类型、证据、恢复动作和下游影响。
5. 子代理父子关系、Prompt、usage 和结果消费状态可审计。
6. 每条诊断结论都有 evidence refs。
7. 数据不足时不会输出确定性结论。
8. 四维评分明确为 partial，不伪装六维完整分数。

### P2 SkillOpt

1. 可按 Skill hash、来源、Agent、Model 聚合历史执行。
2. 可查看成功率、异常率、步骤完成率、耗时、Token、成本。
3. 可比较不同 Skill 版本。
4. 样本不足时不生成高置信建议。
5. 每条建议绑定证据和预期指标。
6. 建议支持人工接受、拒绝、修改、应用和回滚记录。
7. 应用建议后的指标可以与应用前对比。
8. 第一版不会自动覆盖生产 `SKILL.md`。

---

## 23. 相关现有文件

### 现有轨迹入口和分析

- `server/routes/ai_session_admin.py`
- `src/views/admin/AiSessionAdmin.vue`
- `src/api/aiSessionAdmin.ts`
- `mcp-server/tools/analyze_trace.py`
- `mcp-server/tools/query_sessions.py`
- `mcp-server/tools/__init__.py`
- `skills/trace-analyzer/SKILL.md`
- `server/ai-workspaces/global-skills/trace-analyzer/SKILL.md`

### 执行与持久化边界

- `server/routes/ai_chat.py`
- `server/utils/opencode_client.py`
- `server/utils/chat_persist.py`
- `server/utils/opencode_parts.py`
- `server/utils/ai_message_meta.py`
- `server/utils/batch_engine.py`
- `server/utils/batch_repo.py`
- `server/utils/ai_scan_engine.py`
- `server/routes/kefu_public.py`
- `server/utils/kefu_repo.py`
- `server/utils/workspace.py`
- `server/utils/global_skills.py`
- `server/utils/opencode_global.py`

### 现有数据库与前端步骤

- `server/init_db.py`
- `src/utils/todos.ts`
- `src/api/aiChat.ts`
- `src/stores/aiChat.ts`
- `src/stores/aiChatBatches.ts`
- `src/views/ai-chat/AiChatView.vue`

### 现有测试

- `mcp-server/tests/test_analyze_trace.py`
- `server/tests/test_ai_session_admin_analyze.py`
- `server/tests/test_chat_persist.py`
- `server/tests/test_batch_engine.py`
- `server/tests/test_subtask_repo.py`
- `e2e/test-analyze-button.spec.ts`
- `e2e/ai-chat-subtask-trace.spec.ts`

---

## 24. 评审问题

以下问题需要在实现前确认默认决策：

1. 是否允许保存加密后的完整 Effective Prompt，还是只保存 hash/增强项摘要？
2. OpenCode runtime 是否可以增加 Skill load/invoke/step 事件插件？如果不能，是否接受 Skill invoked 只能标记为 inferred？
3. 原始事件保留期是否采用 30/180 天分层策略？
4. Skill Contract 是人工维护优先，还是允许 LLM 自动推断后人工确认？
5. SkillOpt 建议是否只允许人工审核，还是允许低风险配置建议自动应用？
6. 管理员是否允许跨用户查看完整 Prompt 和原始事件？
7. 是否需要将客服会话纳入同一套跨来源质量看板？

推荐默认值：

```text
完整 Prompt：加密保存，默认只返回 hash/摘要
Skill invoked：无 runtime 事件时只能 inferred
原始事件：30 天明文/脱敏，180 天摘要
Contract：显式维护优先，LLM 推断必须人工确认
SkillOpt：第一版全部人工审核
跨用户完整 Prompt：默认禁止，按专用权限开放
客服会话：纳入统一 Attempt，但单独权限隔离
```
