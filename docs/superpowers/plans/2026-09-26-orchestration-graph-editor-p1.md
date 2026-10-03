# AI 编排定义编辑器（Phase ①）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** 拖拽式 DAG 定义编辑器——从节点面板拖入节点、连线表依赖、属性面板编辑字段、保存发布。

**Architecture:** 在 Phase ② 的 Vue Flow 基础上切 editable 模式。新增 NodePalette（左侧拖拽源）、NodePropertiesPanel（右侧属性编辑器）、OrchDagEditor（画布容器，组合 palette + flow + panel + toolbar）。定义 JSON ↔ Vue Flow state 双向转换。

**Tech Stack:** Vue 3, @vue-flow/core, Element Plus, Vitest

**Spec:** `docs/superpowers/specs/2026-09-26-orchestration-graph-editor-design.md` §4

## Global Constraints

- 零新依赖
- 保存走既有 `POST /ai/orchestrations/definitions`（后端 validate_definition 复检）
- 归一化白名单已由 A3 扩充（skills/input_refs/runtime/budget/priority/timeout_sec/join_policy）
- 编辑器不修改已发布 version——每次保存都是新 version

---

### Task 1: NodePalette + NodePropertiesPanel

**Files:**
- Create: `src/components/admin/NodePalette.vue`（~40 行）
- Create: `src/components/admin/NodePropertiesPanel.vue`（~120 行）
- Test: `src/components/admin/__tests__/NodePalette.test.ts`

**Interfaces:**
- NodePalette: emits `add-node(kind: string)` — 点击按钮触发
- NodePropertiesPanel: props `{ node: { id, kind, name, prompt_template, model, skills, budget, timeout_sec, join_policy } | null }`；emits `update:node(updatedFields)` — v-model 模式

- [ ] **Step 1: 写失败测试**

```typescript
// NodePalette: 点击 agent 按钮触发 add-node('agent')
// NodePropertiesPanel: 传入 node → 编辑 prompt_template → emit update:node
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

**NodePalette.vue**：四个按钮（Agent/Tool/Approval/Join），点击 emit `add-node`。

**NodePropertiesPanel.vue**：Element Plus 表单，按 kind 显隐字段：
- agent: name / prompt_template(textarea) / model(input) / skills(tags) / budget.maxTokens(number) / timeout_sec(number)
- approval: name / requested_roles(tags)
- join: name / join_policy(select: all_success/any_success)
- 通用: priority(number)

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add src/components/admin/NodePalette.vue src/components/admin/NodePropertiesPanel.vue src/components/admin/__tests__/NodePalette.test.ts
git commit -m "feat(P3-editor): NodePalette + NodePropertiesPanel"
```

---

### Task 2: OrchDagEditor 画布容器

**Files:**
- Create: `src/components/admin/OrchDagEditor.vue`（~200 行）
- Test: `src/components/admin/__tests__/OrchDagEditor.test.ts`

**Interfaces:**
- Props: `{ definition: { id, name, nodes, edges } | null }`
- Emits: `save(definitionJson)` — 保存按钮触发
- Consumes: NodePalette (emit add-node)、NodePropertiesPanel (v-model)、Vue Flow (editable)

- [ ] **Step 1: 写失败测试**

```typescript
// 初始化：传入 definition → Vue Flow nodes 数 = definition.nodes 数
// 添加节点：emit add-node('agent') → nodes 数 +1
// 删除节点：Vue Flow nodesRemove 事件 → nodes 数 -1 + 关联边删除
// 连线：Vue Flow onConnect → edges +1
// 保存：点击保存按钮 → emit save(definitionJson) 且 JSON 结构正确
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

核心逻辑：

**definition → Vue Flow 转换**（编辑模式初始化）：
```typescript
// 已有 dagreLayout 做初始坐标
// 节点 data 携带完整 node_def（含 prompt_template/skills/budget 等）
// edges 从 definition.edges 转换，type='default'（可编辑连线）
```

**Vue Flow 事件处理**：
- `@connect`: 新连线 → edges 数组追加
- `@nodes-remove`: 删除节点 → 同时删除关联边
- `@node-drag-stop`: 更新节点坐标（不影响 definition JSON）

**保存**：
```typescript
function onSave() {
  // 从 Vue Flow nodes/edges 提取 definition JSON
  const nodes = flowNodes.value.map(n => ({
    id: n.id,
    kind: n.data.kind,
    name: n.data.label,
    prompt_template: n.data.prompt_template,
    model: n.data.model,
    skills: n.data.skills,
    budget: n.data.budget,
    timeout_sec: n.data.timeout_sec,
    join_policy: n.data.join_policy,
    priority: n.data.priority,
  }))
  const edges = flowEdges.value.map(e => ({
    source: e.source, target: e.target,
    kind: e.label === 'reject' ? 'reject' : 'advance',
    condition: e.data?.condition,
  }))
  emit('save', { id: props.definition?.id, name: props.definition?.name, nodes, edges })
}
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add src/components/admin/OrchDagEditor.vue src/components/admin/__tests__/OrchDagEditor.test.ts
git commit -m "feat(P3-editor): OrchDagEditor 画布容器——palette+flow+panel+toolbar"
```

---

### Task 3: 集成到 AiOrchestrationManager

**Files:**
- Modify: `src/views/admin/AiOrchestrationManager.vue`
- Test: `src/views/admin/__tests__/AiOrchestrationManager.test.ts`

**Interfaces:**
- Consumes: `OrchDagEditor` 组件 + `publishDefinition` API
- Produces: 管理页的"编辑"按钮打开 OrchDagEditor 对话框；保存后调 publishDefinition

- [ ] **Step 1: 写失败测试**

```typescript
// 点击"编辑"按钮 → OrchDagEditor 对话框打开（传入选中 definition）
// OrchDagEditor emit save → publishDefinition API 被调用
// API 成功 → 对话框关闭 + 列表刷新
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

在 AiOrchestrationManager.vue 中：
1. definition tab 每行加"编辑"按钮
2. 编辑按钮 → 打开 `el-dialog` 包含 `<OrchDagEditor :definition="editingDef" @save="onSave" />`
3. `onSave(json)` → 调 `publishDefinition(json)` → 成功后关闭对话框 + `loadDefinitions()`

- [ ] **Step 4: 跑测试确认通过 + `npm run build`**

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/AiOrchestrationManager.vue src/views/admin/__tests__/AiOrchestrationManager.test.ts
git commit -m "feat(P3-editor): 管理页集成 OrchDagEditor——编辑按钮打开对话框+保存发布"
```

---

### Task 4: 全量回归

- [ ] 前端 vitest 全量
- [ ] `npm run build`
- [ ] 后端全量（确认定义 API 无回归）

---

## Self-Review

**1. Spec coverage：** Phase ① 的 NodePalette/NodePropertiesPanel/OrchDagEditor/Manager 集成 全覆盖 ✓

**2. Placeholder scan：** 无 TBD ✓

**3. Type consistency：** definition JSON 的 nodes[].kind/name/prompt_template 等字段在 Task 1（panel 编辑）→ Task 2（save 导出）→ Task 3（API 调用）之间一致 ✓
