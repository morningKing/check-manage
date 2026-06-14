# 跨页可自定义工作流引擎（MVP）设计文档

> 日期：2026-06-14
> 分支：`feat/neutral-slate-ui-redesign`（或新建独立分支，见实现计划）
> 子系统 B（两子系统拆分中的第二个；子系统 A「CRUD 并发一致性」已完成并入分支）

## 背景与目标

平台是配置驱动的动态数据管理系统：业务实体不写新页面/表，而是定义 `PageConfig`（字段 schema）+ `Menu`。已有的自动化原语能驱动单记录状态流转与跨集合数据流转，但**没有把"多页 × 多阶段 × 角色 × 数据流转"绑成一个命名的、可视的、可管理的业务流程**的编排层。

**目标**：在现有原语之上新增一个**跨页工作流引擎**，让管理员可自定义一条**线性多阶段流程**：每个阶段绑定一个数据页，由不同角色在不同阶段处理；推进到下一阶段时按字段映射在下一页**生成下游记录**（记录递传链），并支持**简单回退**。复用现有执行底座，不另起炉灶。

### 已确认取向（brainstorming 决议）
1. 流转模型 = **记录递传链**（推进时在下一页生成新记录、反向关联；流程 = 一条跨页关联记录链）。
2. 推进机制 = **状态机定义合法性 + UI 以「推进」按钮呈现**（复用 `get_allowed_transitions`）。
3. **MVP 含简单回退**（退回上一阶段 + 意见）。
4. 指派粒度 = **按角色**。
5. v2 推迟：分支/并行/会签、SLA/超时、动态表单、指派到人。

## 现有可复用原语（实现前必读）

- **字段状态机** `server/utils/workflow.py`：`get_workflow_config`、`find_transition`、`check_conditions`、`validate_transition(fields, field, from, to, data, role) → (allowed, error, actions)`、`execute_actions`、`get_allowed_transitions(fields, field, current_status, role) → [{to,label}]`。在 `routes/dynamic.py:update_item`（约 `:687`）对带 `workflowConfig` 的状态字段强制校验。前端有 `handleWorkflowTransition`。字段类型见 `src/types/field.ts` `WorkflowConfig`/`WorkflowTransition`。
- **触发引擎** `server/utils/trigger_engine.py`：`fire_triggers(event, collection, record_id, old_data, new_data, operator, cur, operator_user_id)`，动作 `create`（`fieldMapping` 跨集合建记录，已含 `reseed_sequences`）、`update`、`notify`、`runScript`。从 `create_item`/`update_item` 触发。
- **建记录路径** `routes/dynamic.py:create_item`：原子分配 autoSequence（`allocate_sequence`）+ 主键 advisory lock + relations + 触发器 + webhook。下游记录生成应复用此路径或其等价逻辑以保持计数器/主键一致。
- **关联** `reference`/`relation`/`quoteSelect`（`data_relations` 表）。**通知** `server/utils/notifier.py:create_notification`。**RBAC** `auth` 角色、`require_page_action`。
- **数据/配置** `dynamic_data`（JSONB + branch_id + version）、`page_configs.fields`（含 `workflowConfig`）、`menus`、`notifications`。

## 设计

### 架构总览

新增 `server/utils/workflow_engine.py`（类比 `trigger_engine.py`），挂在记录生命周期：`update_item` 完成状态字段转换、`execute_actions` 之后，调用 `workflow_engine.on_transition(cur, collection, record_id, status_field, from_value, to_value, old_data, new_data, operator, role)`。引擎读 `workflow_definitions`，判断该转换是否匹配某工作流某阶段的"推进/回退"转换，若是则：推进/回退实例、生成/定位下游/上游记录、切换角色、通知、写 history。

**单一真相源**：工作流的阶段顺序、绑定页、推进/回退转换、角色、字段映射全部在 `workflow_definitions` 中；引擎据此编排。状态字段本身的合法性仍由该字段既有 `workflowConfig` 校验（设计器引用现有状态字段，不代写其配置——MVP）。

### 数据模型（2 张新表）

`workflow_definitions`：
```sql
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    stages      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```
`stages` 为**有序数组**，每个 stage：
```jsonc
{
  "id": "stage-1",
  "name": "需求评审",
  "collection": "requirement",          // 绑定数据页（page-<collection>）
  "statusField": "status",              // 该页的状态字段（须有 workflowConfig）
  "advanceTransition": { "from": "待评审", "to": "已通过" },   // 推进=此转换
  "rejectTransition": { "from": "待评审", "to": "已驳回" },    // 可选；回退=此转换
  "assignedRoles": ["reviewer"],        // 本阶段办理角色
  "spawn": {                            // 推进时在下一阶段页生成下游记录
    "fieldMapping": { "title": "$src.title", "fromReq": "$src.id" },
    "linkBackField": "sourceRequirement" // 下游记录上反指上游的 reference/字段
  }
}
```
- `$src.<field>` = 上游记录字段值；保留占位 `$now`/`$operator`。末阶段无 `spawn`。
- `rejectTransition` 缺省则该阶段不可回退。

`workflow_instances`：
```sql
CREATE TABLE IF NOT EXISTS workflow_instances (
    id              VARCHAR(100) PRIMARY KEY,
    workflow_id     VARCHAR(100) NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',  -- running|completed|cancelled
    current_stage_id VARCHAR(100),
    chain           JSONB NOT NULL DEFAULT '[]'::jsonb,       -- [{stageId, collection, recordId, enteredAt, completedBy}]
    history         JSONB NOT NULL DEFAULT '[]'::jsonb,       -- [{ts, action(advance|reject|start|complete), stageId, by, comment}]
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    started_by      VARCHAR(100),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
CREATE INDEX IF NOT EXISTS idx_wf_inst_current ON workflow_instances(current_stage_id);
```
- `chain` 记录每个走过阶段对应的实际记录；用于跨页追溯与回退定位。

### 运行时：推进

1. 前端在阶段页记录详情上，经现有 `get_allowed_transitions` 显示「推进到 X」按钮（角色/条件过滤；本就存在）。
2. 用户点击 → 走 `update_item`，现有 `workflowConfig` 校验角色/条件/合法性、`execute_actions`。
3. `update_item` 在状态转换成功后调用 `workflow_engine.on_transition(...)`。引擎：
   - 定位该 (collection, recordId) 所属的运行中实例与当前阶段（`current_stage_id`，且 chain 末项 recordId 匹配）；
   - 若 `(from,to)` == 当前阶段 `advanceTransition`：
     - 标记 chain 末项 `completedBy=operator`；
     - 若有下一阶段：按 `spawn.fieldMapping`（解析 `$src`/`$now`/`$operator`）在下一阶段 `collection` **生成下游记录**（复用 create 路径以保持 autoSequence/主键一致），写 `linkBackField` 反向关联；chain 追加新项；`current_stage_id` 前移；通知下一阶段 `assignedRoles`；
     - 若无下一阶段：实例 `status=completed`；
   - history 追加 `advance`。
4. 同一事务内完成（与 `update_item` 的 cursor 一致，原子）。

### 运行时：回退（MVP 简单回退）

- 若 `(from,to)` == 当前阶段 `rejectTransition`：
  - 引擎将实例 `current_stage_id` 回退到 chain 中上一阶段项（**复用已存在的上游记录**，不新建）；
  - 把上游记录状态字段置回其阶段的"待办值"（`advanceTransition.from`，即可重新办理），记 `_rejectComment`（来自请求体 `comment`）到上游记录或 history；
  - 当前阶段记录状态停留在"已驳回"（chain 末项保留但标记 rejected）；
  - 通知上一阶段 `assignedRoles`（携带驳回意见）；history 追加 `reject`。
- 回退意见来自前端推进/回退对话框的 `comment` 字段，随 `update_item` 请求体透传给引擎（不改 `update_item` 通用签名——从 `body` 读取可选 `_workflowComment`）。

### 待办收件箱

- 新 API `GET /workflow/inbox`：返回当前用户角色匹配"运行中实例当前阶段 `assignedRoles`"的实例 + 其当前记录摘要（collection、recordId、阶段名、进入时间）。
- 前端 `/workflow/inbox` 视图：列表，点击跳到对应数据页该记录详情（复用动态页路由 `/page/:pageId` + 记录定位）办理/推进。

### 流程设计器（admin）

- 新 admin 页 `WorkflowManager.vue`（纳入「设置中心」分类，权限 `admin.workflows`）：
  - 列表 + 新建/编辑工作流：命名、启用开关、**有序阶段编辑器**（增删/排序阶段；每阶段选 collection、状态字段、推进转换(from/to)、可选回退转换、办理角色多选、spawn 字段映射 + linkBack 字段）。
  - 字段/状态值下拉从所选 collection 的 `page_configs.fields` 动态加载（含其 `workflowConfig.transitions` 供选推进/回退转换）。
- REST `server/routes/workflows.py`：`workflow_definitions` CRUD（`@require_permission('admin.workflows')`）；实例只读列表/详情（含 chain 可视化）；inbox。

### 启动流程

- 在首阶段页新建记录时，提供"启动工作流 W"入口（数据页 `操作` 菜单或新增对话框旁）：调用 `POST /workflow/instances {workflowId, collection, recordId}` 创建实例，chain 首项=该记录，`current_stage_id`=首阶段，通知首阶段角色。
- MVP：手动启动；自动启动（首阶段页建记录即自动开实例）留作可选增强（若实现，复用 trigger create 事件钩子）。

### 权限（RBAC）

- 工作流定义管理：`admin.workflows`（新增能力 key，纳入 `PERMISSION_CATALOG`）。
- 阶段办理：沿用数据页 `require_page_action` + 状态转换的 `roles` 门禁 + 阶段 `assignedRoles`（引擎在 `on_transition` 校验 operator 角色 ∈ 当前阶段 `assignedRoles`，否则拒绝推进/回退）。
- 收件箱按角色过滤。

### 与现有原语的关系（避免重复）

- 状态字段合法性、角色、条件：**复用** `workflow.py`（不重写状态机）。
- 下游记录生成：**复用** create 路径（autoSequence 计数器一致、主键 advisory lock、relations、触发器），不绕过。
- 通知：复用 `notifier`。引擎只新增"阶段/实例编排 + chain 追踪 + spawn 映射 + 回退定位"。

## 不在范围（MVP，留 v2）

- 分支/并行/会签、SLA/超时/自动升级、动态表单（按阶段不同字段集）、指派到具体人、自动启动、流程版本化、撤销/作废以外的复杂状态。
- 不改 `update_item` 通用签名（仅从请求体读可选 `_workflowComment`）。
- 不引入工作流专用的独立"案件"实体（沿用记录递传链模型）。

## 测试策略

- **后端单测**（pytest）：
  1. `workflow_engine.on_transition` 推进：匹配 advanceTransition → 生成下游记录（字段映射 `$src` 解析正确、linkBack 关联建立、chain/`current_stage` 前移、末阶段 completed）。
  2. 回退：匹配 rejectTransition → `current_stage` 回上一阶段、上游记录状态复位、history 记 reject + comment、通知上一阶段角色。
  3. 角色门禁：operator 角色 ∉ 当前阶段 assignedRoles → 拒绝。
  4. 下游记录复用 create 路径 → autoSequence 计数器一致（不重号）。
  5. `workflow_definitions` CRUD + 权限 `admin.workflows`。
  6. inbox 按角色过滤正确。
  7. 实例并发推进：同一实例并发两次推进 → 仅一次生效（`current_stage_id` CAS 或行锁，防重复 spawn）。
- **前端单测**（vitest）：设计器阶段编辑（增删排序、字段/转换下拉联动）、inbox 列表渲染、推进/回退对话框（含 comment）。
- **集成/视觉**：定义一个 2-3 阶段 demo 流程（如 需求→设计→开发），端到端走一遍推进 + 回退，Playwright 截图核对收件箱与递传链。

## 净效果

管理员可在设计器里把现有数据页编排成一条命名的线性跨页流程；记录推进时自动按映射生成下游记录并通知下一阶段角色，支持回退；收件箱按角色聚合待办；全程复用现有状态机/建记录/通知/RBAC/计数器原语,新增仅编排引擎 + 2 张表 + 设计器/收件箱 UI。
