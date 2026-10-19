# AI Harness P2 编排与 Runtime 演进 Spec

**版本**：v1.0
**状态**：待评审
**日期**：2026-09-23
**优先级**：P2
**前置依赖**：`2026-09-23-ai-harness-p0-execution-safety-spec.md`（ownership / CAS / generation）、`2026-09-23-ai-harness-p1-durable-execution-spec.md`（lease / checkpoint / effect / events / outbox / commands / budget）
**范围**：AI 任务编排（Step/DAG）、人工审批、Artifact Store、Runtime Adapter 与执行隔离、调度与配额可观测
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 背景

P0/P1 之后，系统具备“单执行者、可租约接管、有 checkpoint 与 effect 幂等、有事件流与可靠投递”的持久化执行内核。但执行模型仍然是**一文件一子会话一回合**：

```text
批任务：一个输入文件 → 一个 ai_chat_session → 一个 OpenCode turn → 一个终态
```

对于“多步骤、跨小时、需要人工把关”的任务，这套模型有三个结构性缺口：

| # | 缺口 | 现状证据 |
|---|---|---|
| P2-1 | **没有服务端步骤模型**：步骤只存在于模型自己的 Todo 里，而 Todo 是模型声明、不是事实 | `docs/design/ai/AI执行审计能力说明.md:100`（Todo 只作 declared plan）；`server/utils/todo_trace.py` |
| P2-2 | **没有执行前审批**：动作门禁是事后核验，批任务还会自动拒绝 question，因此无法“先暂停等人批” | `server/utils/agent_ledger.py:484-591`（终态核对）；`server/utils/batch_engine.py:2034-2053`（自动拒绝 question） |
| P2-3 | **产物与执行环境耦合**：产物只存在于 workspace，workspace 不进备份，删除即丢失 | `mcp-server/tools/save_artifact.py:1-75`（只写 `outputs/`）；`server/utils/backup.py:24-26`（注释明确 workspace 是临时数据、不备份） |
| P2-4 | **没有执行隔离**：workspace 是逻辑目录，`run_python` 与 OpenCode 内置工具共享主机权限 | `mcp-server/tools/run_python.py:1-10`（自述非强沙箱）；`server/utils/py_runner.py:1-12` |
| P2-5 | **没有 runtime 抽象**：执行路径硬编码 OpenCode HTTP 客户端 | `server/utils/batch_engine.py:77-232`（`_OpenCodeFacade`） |

同时，仓库里**已经存在**一套业务工作流引擎，具备 DAG、条件边、并行分支、角色门禁与收件箱：

- 定义与实例：`workflow_definitions` / `workflow_instances`（`server/init_db.py:564-599`、`server/migrations/2026_06_14_workflow_tables.py:8-23`）；
- 编排实现：条件路由、并行扇出、回退、下游记录 spawn（`server/utils/workflow_engine.py:102-299`）；
- 待办收件箱：`GET /workflow/inbox`（`server/routes/workflows.py:85-115`）与前端 `src/views/workflow/WorkflowInbox.vue`。

**关键判断**：这套引擎编排的是“动态数据记录 + 状态字段”，不是 Agent 执行。它没有 step attempt、lease、retry policy、input/output ref、checkpoint、resource budget、approval pause。P2 **复用其概念与 UI 入口，但不复用其状态语义**（见 §3）。

---

## 2. 目标与非目标

### 2.1 目标

1. **AI 编排定义**：可版本化的 `definition`（节点、边、重试/超时/预算/审批策略），发布后不可变。
2. **Run / Step / Attempt 三层模型**：run 冻结定义版本、输入与 runtime manifest；step 有依赖、状态与 checkpoint；attempt 复用 P1 的 lease/effect。
3. **DAG 执行语义**：线性、条件分支、并行 fan-out/fan-in 初版；join 语义明确；状态完全可从 DB 重建。
4. **人工审批**：`waiting_approval` 状态、审批请求、approve/reject/edit、超时升级；与既有 inbox 集成。
5. **Artifact Store**：内容寻址产物、稳定 id、hash、引用关系、保留策略；workspace 可回收但产物不丢。
6. **Runtime Adapter**：统一接口 + OpenCode 本地实现（默认）+ 容器/Job Object/K8s 实现（按环境启用）；资源与网络隔离。
7. **调度与配额**：step 优先级、租户公平、provider 限流、队列等待/ETA/成本可观测。
8. **结果 contract**：step 输出按 schema/文件/DB/外部响应校验，失败进入明确状态而非静默完成。

### 2.2 非目标

- 不重写业务工作流引擎（`workflow_engine.py`）的既有语义与数据。
- 不实现会签、任意循环、动态表单、跨组织 SLA（列为后续扩展）。
- 不替换 OpenCode 作为默认 Agent runtime。
- 不引入 Kubernetes 作为必需依赖；K8s 仅为可选适配器。
- 不承诺 LLM 层 exactly-once（沿用 P1 的“at-least-once 执行 + exactly-once effect”）。
- 不改变 P0/P1 已发布的对外契约字段（只做增量）。

---

## 3. 复用边界（必须遵守）

| 概念 | 复用来源 | P2 做法 |
|---|---|---|
| 阶段/条件/并行/角色门禁的思想 | `server/utils/workflow_engine.py:102-179`（`_effective_edges`/`_advance_targets`）、`:223-224`（角色校验） | 借鉴语义，**另建** `ai_orchestration_*` 表，不复用 `workflow_instances` 行 |
| 待办收件箱 UI | `GET /workflow/inbox`（`server/routes/workflows.py:85-115`）、`src/views/workflow/WorkflowInbox.vue` | 通过 adapter 把 AI 审批请求投影到同一 inbox（新增 `kind='ai_approval'` 项），不改业务工作流表结构 |
| 执行契约与效果断言 | `execution_contract`、`action_expectations`、`effect_spec`（`server/utils/agent_ledger.py:202-295,484-591`） | 作为 step 结果 contract 的**动作级**组成部分复用 |
| Attempt / 事件 / 命令 | P1 的 `ai_execution_attempts` / `ai_batch_events` / `ai_execution_commands` / `ai_delivery_outbox` | step attempt 直接使用同一套表与语义 |
| 产物文件能力 | `save_artifact`、`workspace_outputs.list_session_files`、`ai_chat_session_files`、`data_files` 导入 | 作为 Artifact Store 的**输入来源**，保留兼容 |

**禁止**：把 `workflow_instances.status/current_stage_id` 直接当作 Agent attempt 状态；两套状态通过 `orchestration_run_id` 关联，避免互相覆盖。

---

## 4. 总体设计

```text
ai_orchestration_definitions（版本化、不可变）
        │  publish
        ▼
ai_orchestration_runs ── 冻结：definition version / run input / runtime manifest / budget
        │
        ├── ai_orchestration_steps（DAG 节点 + 依赖 + 状态 + checkpoint 引用）
        │        │  claim（复用 P1 lease）
        │        ▼
        │   ai_execution_attempts（P1）──► Runtime Adapter ──► OpenCode / Container / Job Object
        │        │
        │        ├── ai_execution_effects（P1，幂等副作用）
        │        └── artifacts / artifact_refs（P2，产物）
        │
        ├── ai_approval_requests（人工审批）
        └── ai_batch_events（P1，事件流；外部/UI 观测）
```

核心原则：

1. **状态派生自事实**：run 状态由 step 事实派生；step 状态由 attempt/effect/contract 事实派生；模型 Todo 永远不能推进 DAG。
2. **定义不可变**：run 引用 definition 的具体版本；编辑定义不影响运行中的 run。
3. **环境即定义的一部分**：runtime manifest（Agent/Skill/MCP 版本与 hash、资源档、网络策略）随 run 冻结并进入审计。
4. **不可逆动作必须过审批或具备 effect key**。

---

## 5. 数据模型

### 5.1 `ai_orchestration_definitions`

```sql
CREATE TABLE IF NOT EXISTS ai_orchestration_definitions (
  id          VARCHAR(100) NOT NULL,
  version     INT NOT NULL DEFAULT 1,
  name        VARCHAR(200) NOT NULL,
  description TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  owner_user_id VARCHAR(100),
  nodes       JSONB NOT NULL DEFAULT '[]'::jsonb,
  edges       JSONB NOT NULL DEFAULT '[]'::jsonb,
  retry_policy      JSONB NOT NULL DEFAULT '{}'::jsonb,
  timeout_policy    JSONB NOT NULL DEFAULT '{}'::jsonb,
  budget_policy     JSONB NOT NULL DEFAULT '{}'::jsonb,
  approval_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
  compensation_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  PRIMARY KEY (id, version)
);
```

`nodes[]` 单节点结构：

```jsonc
{
  "id": "extract",
  "kind": "agent",                  // agent | tool | approval | join | artifact | script
  "name": "抽取结构化事实",
  "agent": "data-agent",
  "model": "anthropic/claude-sonnet-4",
  "skills": ["pdf-extract"],
  "prompt_template": "……{{inputs.files}}……",
  "input_refs": ["run.inputs.files", "steps.split.outputs.parts"],
  "output_contract": {
    "type": "json_schema",
    "schema": { "type": "object", "required": ["facts"], "properties": {"facts": {"type": "array"}} }
  },
  "runtime": { "profile": "standard", "network": "none", "timeoutSec": 900 },
  "retry": { "max": 2, "backoff": "exp", "retryable": ["timeout", "provider_error"] },
  "budget": { "maxTokens": 200000, "maxCost": 3.0 }
}
```

`edges[]` 结构（借鉴业务工作流的 `kind` 与 `condition` 概念）：

```jsonc
{ "source": "split", "target": "extract", "kind": "advance", "condition": {"field": "size", "op": ">", "value": 100} }
{ "source": "split", "target": "small_path", "kind": "advance" }
{ "source": "extract", "target": "merge", "kind": "join" }
{ "source": "publish", "target": "notify", "kind": "compensation" }
```

### 5.2 `ai_orchestration_runs`

```sql
CREATE TABLE IF NOT EXISTS ai_orchestration_runs (
  id            VARCHAR(100) PRIMARY KEY,
  definition_id VARCHAR(100) NOT NULL,
  definition_version INT NOT NULL,
  batch_id      VARCHAR(100),
  status        VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | running | waiting_approval | paused | recovering
  -- | partial | completed | failed | cancelled | needs_review
  current_nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
  run_input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  runtime_manifest_id VARCHAR(100),
  budget_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  requested_by  VARCHAR(100),
  requested_by_kind VARCHAR(20) NOT NULL DEFAULT 'user',
  error_code    VARCHAR(100),
  error_message TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orch_runs_status ON ai_orchestration_runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orch_runs_batch ON ai_orchestration_runs(batch_id);
```

### 5.3 `ai_orchestration_steps`

```sql
CREATE TABLE IF NOT EXISTS ai_orchestration_steps (
  id            VARCHAR(150) PRIMARY KEY,        -- run_id + ':' + node_id
  run_id        VARCHAR(100) NOT NULL REFERENCES ai_orchestration_runs(id) ON DELETE CASCADE,
  node_id       VARCHAR(100) NOT NULL,
  kind          VARCHAR(30) NOT NULL,
  status        VARCHAR(30) NOT NULL DEFAULT 'blocked',
  -- blocked | runnable | running | waiting_approval | succeeded | failed
  -- | skipped | compensated | needs_review
  depends_on    JSONB NOT NULL DEFAULT '[]'::jsonb,
  attempt_id    VARCHAR(100),
  checkpoint_id VARCHAR(100),
  input_refs    JSONB NOT NULL DEFAULT '[]'::jsonb,
  output_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
  result_contract JSONB,
  retry_budget  INT,
  compensation_step_id VARCHAR(150),
  error_code    VARCHAR(100),
  error_message TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orch_steps_run ON ai_orchestration_steps(run_id, node_id);
CREATE INDEX IF NOT EXISTS idx_orch_steps_status ON ai_orchestration_steps(status);
```

不变量：

- `depends_on` 中的节点必须全部 `succeeded`（或按 join 语义满足）才能进入 `runnable`；
- 同一 step 同时最多一个 active attempt（由 P1 的 attempt 部分唯一索引保证，按 `session_id`/`step_id` 维度扩展）；
- step 状态转移全部 CAS，禁止无条件覆盖。

### 5.4 `ai_approval_requests`

```sql
CREATE TABLE IF NOT EXISTS ai_approval_requests (
  id            VARCHAR(100) PRIMARY KEY,
  run_id        VARCHAR(100) NOT NULL REFERENCES ai_orchestration_runs(id) ON DELETE CASCADE,
  step_id       VARCHAR(150),
  risk_level    VARCHAR(20) NOT NULL DEFAULT 'medium',   -- low | medium | high
  tool          VARCHAR(100),
  args_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
  effect_summary TEXT,
  checkpoint_id VARCHAR(100),
  status        VARCHAR(30) NOT NULL DEFAULT 'pending',
  -- pending | approved | rejected | expired | cancelled
  requested_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
  requested_users JSONB NOT NULL DEFAULT '[]'::jsonb,
  decided_by    VARCHAR(100),
  decision_comment TEXT,
  decision_hash VARCHAR(64),
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at    TIMESTAMPTZ,
  resolved_at   TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_approval_pending_step
  ON ai_approval_requests(step_id) WHERE status = 'pending';
```

### 5.5 `artifacts` / `artifact_refs`

```sql
CREATE TABLE IF NOT EXISTS artifacts (
  id          VARCHAR(100) PRIMARY KEY,
  owner_user_id VARCHAR(100),
  name        VARCHAR(300) NOT NULL,
  media_type  VARCHAR(150),
  size_bytes  BIGINT NOT NULL DEFAULT 0,
  sha256      VARCHAR(64) NOT NULL,
  storage_key TEXT NOT NULL,          -- 相对 AI_WORKSPACE_ROOT 的 content-addressed 路径
  status      VARCHAR(30) NOT NULL DEFAULT 'active',  -- active | quarantined | deleted
  retention_days INT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_artifact_sha ON artifacts(sha256, name);

CREATE TABLE IF NOT EXISTS artifact_refs (
  id          VARCHAR(100) PRIMARY KEY,
  artifact_id VARCHAR(100) NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  run_id      VARCHAR(100),
  step_id     VARCHAR(150),
  attempt_id  VARCHAR(100),
  session_id  VARCHAR(100),
  batch_id    VARCHAR(100),
  relation    VARCHAR(30) NOT NULL,   -- input | output | checkpoint | report
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifact_refs_run ON artifact_refs(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_artifact_refs_artifact ON artifact_refs(artifact_id);
```

### 5.6 `ai_runtime_manifests`

```sql
CREATE TABLE IF NOT EXISTS ai_runtime_manifests (
  id            VARCHAR(100) PRIMARY KEY,
  runtime_kind  VARCHAR(30) NOT NULL,        -- opencode_local | docker | windows_job | k8s_job
  runtime_version VARCHAR(100),
  model         VARCHAR(300),
  agent_hashes  JSONB NOT NULL DEFAULT '{}'::jsonb,
  skill_hashes  JSONB NOT NULL DEFAULT '{}'::jsonb,
  plugin_hashes JSONB NOT NULL DEFAULT '{}'::jsonb,
  mcp_config_hash VARCHAR(64),
  guidance_hash VARCHAR(64),
  workspace_config_hash VARCHAR(64),
  resource_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  network_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

run 创建时冻结 manifest；审计（`ai_execution_manifests`）引用该 id，保证“同一 run 的定义/技能/插件版本可复现”。

---

## 6. Step/DAG 执行语义

### 6.1 生命周期

```text
1. 定义发布（version 递增，旧版本保留）
2. run 创建：冻结 definition_version / run_input_snapshot / runtime_manifest / budget
3. scheduler 计算 runnable steps（依赖满足 + 条件边命中）
4. step claim → 建 P1 attempt（lease/heartbeat/fencing）
5. Agent 只处理当前 step，输出写入 Artifact Store 或结构化结果
6. result contract 校验：
     pass  → step=succeeded，写事件，解锁下游
     fail  → 按 retry policy 重试；预算耗尽 → failed / needs_review
7. join 节点：全部上游 succeeded（或按 join 策略）才 runnable
8. 全部终态 → run 状态派生（completed | partial | failed | needs_review）
9. compensation：仅针对 status='committed' 的 effect 执行补偿步骤；unknown 一律 needs_review
```

### 6.2 校验规则（定义发布时强制）

- DAG 无环（拓扑排序校验，环直接拒绝）；
- 每个 node id 唯一；edge 引用的 node 必须存在；
- `join` 节点必须有多条入边；`agent` 节点必须有 prompt_template 或 input_refs；
- 条件边必须引用上游已声明输出字段；
- 审批节点必须有 `requested_roles` 或 `requested_users`；
- 补偿边只允许指向 `kind='compensation'` 或 `kind='agent'` 的节点。

### 6.3 与批任务的映射

- 现有批任务 = 隐式单 step run（`kind='agent'`，node id 固定 `__batch_child__`）；
- P2 上线后，批任务仍可独立使用（不强制升级为 DAG）；一个批次可以整体作为一个 run 的输入集合（`batch_id` 关联）；
- 对外契约：`/v1/ai-batches` 保持不变；新增 `/v1/ai-orchestrations/*` 独立契约。

### 6.4 模型不得推进 DAG

- Agent 的 Todo 只作为 `declared_plan` 记录（沿用 `server/utils/todo_trace.py` 语义）；
- step 状态只能由服务端依据 attempt/effect/contract 事实推进；
- 若 Agent 声称完成但 contract 未通过 → step 不 `succeeded`（与动作门禁的 `completed_claimed` 判定同源，`docs/design/ai/AI执行审计能力说明.md:93-100`）。

---

## 7. 审批与人工介入

### 7.1 触发方式

| 方式 | 说明 |
|---|---|
| 节点声明 | `kind='approval'` 的节点：到达即 `waiting_approval` |
| 策略拦截 | `approval_policy` 声明“哪些 tool/effect 必须先批”（如 `data_delete_record`、外部 HTTP、发布类动作） |
| 人工介入 | 运行中管理员可主动挂起某个 step 进入审批（等价于 P1 的 `pause` + approval request） |

### 7.2 流程

```text
step 到达审批点
  → INSERT ai_approval_requests(status='pending')
  → step.status='waiting_approval'，run.status='waiting_approval'
  → 投影到 inbox（kind='ai_approval'，复用 /workflow/inbox 入口与前端页面）
  → approve / reject / edit（修改参数后批准）
      → 写 ai_execution_commands（P1）+ 事件 + outbox
      → step 从 checkpoint + generation 继续（不重跑已完成部分）
  → 超时（expires_at）→ expired，按策略升级（通知上级角色）或 fail
```

约束：

- 审批是**执行前**门禁，与 P0 的事后动作门禁互补，不互相替代；
- 批任务场景不得自动拒绝平台审批（现有“自动拒绝 question”仅针对模型提问工具，`server/utils/batch_engine.py:2034-2053`）；
- 重复 approve/reject 幂等（`uniq_approval_pending_step` + command 幂等键）；
- 审批决策写 `decision_hash`，可审计不可篡改。

### 7.3 权限

- 审批权限：`admin.ai_approval`（新增能力键）或 step 声明的角色；
- 非授权用户操作 → 403；
- 所有审批动作写 `log_operation`（含 before/after 与理由）。

---

## 8. Runtime Adapter 与执行隔离

### 8.1 接口

```python
class AgentRuntime:
    """执行运行时的统一抽象。上层执行引擎不得直接依赖 OpenCode 客户端。"""
    def capabilities(self) -> dict: ...          # 支持 checkpoint / pause / network_isolation ...
    def start(self, run, step, manifest) -> str: ...   # 返回 runtime handle
    def dispatch(self, attempt, prompt) -> None: ...
    def state(self, attempt) -> dict: ...
    def heartbeat(self, attempt) -> None: ...
    def abort(self, attempt, reason: str) -> None: ...
    def checkpoint(self, attempt) -> dict: ...
    def collect_artifacts(self, attempt) -> list: ...
    def cleanup(self, attempt) -> None: ...
    def health(self) -> dict: ...
```

### 8.2 适配器

| 适配器 | 说明 | 优先级 |
|---|---|---|
| `OpenCodeLocalRuntime` | 包装现有 `opencode_client`（`server/utils/opencode_client.py`）与 `_OpenCodeFacade` 语义，作为默认实现 | P2 首发 |
| `DockerRuntime` | 每 run/step 独立容器；CPU/内存上限、只读根、workspace 挂载、网络策略（none/allowlist）、secret 临时挂载 | P2 可选 |
| `WindowsJobRuntime` | 用 Job Object 管理进程树与资源上限（Windows 部署） | P2 可选 |
| `KubernetesJobRuntime` | 规模化部署扩展 | 后续 |

隔离要求（Docker/Job 实现）：

- 进程树可被整体 kill（不留孤儿）；
- 网络默认拒绝（`none`），需要时按 allowlist 放开；
- secret 以临时文件/环境注入，不落 workspace；
- 磁盘写入限制在挂载目录内，超出配额即失败；
- 退出后 attempt 必须收敛（不允许“容器还在跑、平台已放弃”）。

### 8.3 与既有 OpenCode 进程治理的关系

- 平台已经能识别自己拉起的 `opencode serve` 进程并拒绝误杀外部进程（`server/utils/opencode_launch.py:1-113`、`server/routes/ai_opencode_admin.py:325-404`）；
- P2 的 adapter 复用该 ownership 机制，但把“重启治理”与“单次执行生命周期”分离：adapter 只管单次 attempt 的 start/abort/cleanup，serve 级重启仍由既有管理页负责；
- runtime 重启前必须完成 P1 的 lease drain，避免“重启 serve 打断运行中 attempt 却不留痕迹”。

---

## 9. Artifact Store 与结果 Contract

### 9.1 写入路径

```text
Agent 产出（save_artifact / workspace outputs / run_python 输出）
  → step 收敛前由 adapter.collect_artifacts() 收集
  → 计算 sha256 → 写入 content-addressed 存储（storage_key = sha256 前缀分片）
  → INSERT artifacts + artifact_refs(relation='output')
  → step.output_artifact_refs 记录
```

- 大文件不进入事件 payload（事件只带 `artifactId`/`sha256`/`size`）；
- 同内容重复写入按 `(sha256, name)` 去重或建新版本（策略可配，默认去重）；
- 下载/预览一律按 `artifactId` 鉴权（owner/admin），不再依赖 workspace 路径；
- 兼容：既有 `/sessions/<childId>/files` 与 `data_files` 导入保留，其内容在 P2 后可追溯到 artifact。

### 9.2 结果 contract

| 类型 | 校验 |
|---|---|
| `json_schema` | 结构化输出符合 schema |
| `file` | 存在 + 大小/hash 匹配 |
| `db_record` | 目标记录存在且字段满足断言（复用 `effect_spec`：`server/utils/agent_ledger.py:202-228`） |
| `external_response` | 外部调用返回码/响应体断言 |
| `action` | 复用动作门禁期望（工具 + 参数正则 + 状态） |

契约失败 → step 不 `succeeded`；按 `retry_policy` 重试，超过预算 → `failed`/`needs_review`；对外结果保持“未通过校验的输出不外泄”（沿用 `server/utils/batch_repo.py:699-720` 的 output 门控原则）。

### 9.3 保留与备份

- artifacts 与 manifest 纳入备份范围（`server/utils/backup.py:24-26` 的排除约定需同步调整）；
- workspace 可过期回收（P1 §9.3），artifact 独立保留 `retention_days`；
- 过期清理前检查引用（`artifact_refs` 存在则跳过并告警）。

---

## 10. 调度、配额与可观测

| 能力 | 说明 |
|---|---|
| step 优先级 | definition 的 `nodes[].priority` + run 优先级，进入 P1 claim 排序 |
| 租户公平 | 按 `owner_user_id`/API Key 的并发与配额（P1 预算表扩展 scope） |
| provider 限流 | 按 model/provider 的并发上限与退避；超限 step 回 `runnable` 等待 |
| 队列可观测 | `queueWaitMs`、`runningMs`、`estimatedRemainingMs`（基于历史同 node 耗时中位数） |
| 成本预测 | 基于 `ai_execution_usage` 历史 + 剩余 step 估算 |
| 背压 | 队列深度超阈值时拒绝新 run 创建（返回 429 + 明确 code） |
| run graph | 管理面可视化 DAG（节点状态、耗时、artifact、审批、effect） |
| 失败聚合 | 按 definition/version 统计成功率、重试率、审批拒绝率、平均成本 |

指标以 P1 的事件表为事实源，不新增独立统计库。

---

## 11. 迁移策略（Phase A–F）

| Phase | 内容 | 兼容性 |
|---|---|---|
| **A** | 为现有批任务建立隐式单 step run（只读投影，不改变批任务执行路径） | 完全兼容 |
| **B** | 审批适配：把 `ai_approval_requests` 投影到既有 inbox（新增 kind），先只支持手工挂起 | 不影响业务工作流 |
| **C** | 上线 `ai_orchestration_definitions/runs/steps`，支持线性 + 条件 + 并行 | 新能力，feature flag |
| **D** | Artifact Store + `OpenCodeLocalRuntime` 接入（保持现有 OpenCode 行为） | 既有 API 保留 |
| **E** | 按环境启用 Docker / Windows Job / K8s adapter | 部署可选 |
| **F** | 调度、配额、ETA、成本、run graph 接入管理面 | 只读为主 |

旧数据：无 definition 的批任务继续走批引擎；run 创建失败/不支持时降级为批任务并在事件中标注 `degraded_to_batch`。

---

## 12. 测试与验收

### 12.1 后端

- definition 校验：环、悬空边、join 语义、条件字段引用；
- definition version immutable：运行中 run 不受后续编辑影响；
- 线性 3 step：依赖推进、中间 checkpoint 恢复、不重复 committed effect；
- 条件分支：命中/默认/无出边三种；
- 并行 fan-out/fan-in：分支独立成败、join 语义、部分失败 → run `partial`；
- step retry：不重跑已成功依赖；`unknown` effect → `needs_review`；
- 审批：暂停 → approve/reject/edit → 从 checkpoint 继续；超时升级；重复操作幂等；
- artifact：sha256 一致、去重策略、workspace 删除后仍可下载、过期清理不删被引用对象；
- runtime adapter：OpenCode 适配器回归既有 E2E；容器适配器资源/网络/进程树隔离（无孤儿进程）；
- 多租户公平与 provider 限流可观测。

### 12.2 故障注入

- 在每个 step 的 dispatch / tool / effect / approval 前后 kill executor；
- 审批等待期间重启服务、断浏览器；
- artifact 上传中断后重试不产生重复对象；
- 容器/Job 内子进程泄漏检测（退出后无残留）；
- DAG 并发推进、join 竞态、补偿与人工接管。

### 12.3 E2E

- `e2e/ai-full/ai-harness-p2-orchestration.spec.ts`：定义一个 3 节点 DAG（抽取 → 审批 → 汇总），跑通并留截图；
- 审批拒绝后 run 终态正确、无副作用残留；
- run graph 从事件 cursor 重建与 UI 一致。

### 12.4 验收标准

- [ ] definition 有版本且不可变；run 可完整重建状态；
- [ ] DAG 支持线性/条件/并行；join 与部分失败语义明确；
- [ ] step 级 retry 不重复已提交 effect；unknown effect 不自动重放；
- [ ] 高风险 step 必须经审批才能执行，审批可从准确 checkpoint 继续；
- [ ] 产物有稳定 id/hash/引用，workspace 回收不影响产物与审计；
- [ ] 上层执行引擎不直接依赖 OpenCode 客户端；adapter 能力可查询；
- [ ] 容器/Job 执行具备时间、CPU、内存、网络、进程树约束；
- [ ] 调度与成本/ETA 可观测，背压与租户公平生效；
- [ ] P0/P1 的既有契约与测试全部保持通过。

---

## 13. 涉及文件

**新增**

- `server/migrations/2026_09_23_harness_p2_orchestration.py`
- `server/utils/orchestration_defs.py`（定义校验/版本发布）
- `server/utils/orchestration_engine.py`（run/step 推进、join、补偿）
- `server/utils/orchestration_scheduler.py`（runnable 计算、优先级、限流）
- `server/utils/approval_repo.py` + `server/routes/ai_approvals.py`
- `server/utils/artifact_store.py` + `server/routes/artifacts.py`
- `server/utils/runtime/__init__.py`、`base.py`、`opencode_local.py`、`docker_runtime.py`、`windows_job_runtime.py`
- `server/tests/test_orchestration_*.py`、`server/tests/test_approvals.py`、`server/tests/test_artifact_store.py`
- `e2e/ai-full/ai-harness-p2-orchestration.spec.ts`
- `src/views/admin/AiOrchestrationManager.vue`、`src/components/admin/RunGraphView.vue`、`src/api/orchestration.ts`

**修改**

- `server/utils/batch_engine.py`（执行路径改经 runtime adapter；批任务作为单 step run 投影）
- `server/utils/execution_audit.py`（step/manifest/approval 关联）
- `server/routes/workflows.py`（inbox 增加 `kind='ai_approval'`）
- `src/views/workflow/WorkflowInbox.vue`（渲染 AI 审批项）
- `server/utils/backup.py`（artifacts/manifest 纳入备份）
- `server/app.py`（新蓝图注册、迁移钩子）
- `docs/user-guide/integration/` 新增编排与审批文档；`docs/design/ai/AI功能架构图.svg` 同步

---

## 14. 拆 PR 建议

| PR | 内容 | 依赖 |
|---|---|---|
| **PR-1** | definition 模型 + 校验 + 版本发布 + 单测 | P1 |
| **PR-2** | run/step 模型 + 线性执行 + 状态派生 | PR-1 |
| **PR-3** | 条件分支 + 并行 + join + 部分失败 | PR-2 |
| **PR-4** | 审批模型 + inbox 投影 + approve/reject/edit | PR-2 |
| **PR-5** | Artifact Store + 结果 contract + 备份接入 | PR-2 |
| **PR-6** | Runtime Adapter 抽象 + OpenCodeLocalRuntime 迁移 | PR-2 |
| **PR-7** | 容器 / Job Object adapter + 隔离与进程树测试 | PR-6 |
| **PR-8** | 调度、配额、ETA、成本、run graph 管理面 | PR-3 |

---

## 15. 依赖与阻断关系

- **依赖 P1**：step 的 lease/checkpoint/effect/events/outbox/commands 全部复用 P1，不得另建并行机制；P1 未完成不得开工。
- **依赖 P0**：任何 step 驱动最终仍落到 P0 的 ownership/CAS 语义上。
- 与业务工作流的关系：`workflow_engine` 继续服务动态数据流转；P2 只借鉴其 DAG/条件/inbox 概念并做只读投影，禁止共用实例表。
- 与 `docs/superpowers/specs/2026-06-14-cross-page-workflow-engine-design.md` 的关系：该 Spec 明确把分支/并行/会签列为后续扩展；本 Spec 的并行与 join 属于**AI 执行编排**范畴，不回头修改业务工作流语义。
- 与 `docs/design/ai/AI批任务能力总览与创新点.md` 的关系：P2 完成后需同步更新该文档的“能力边界”，避免把尚未落地的编排/审批/产物能力写成已有能力。
