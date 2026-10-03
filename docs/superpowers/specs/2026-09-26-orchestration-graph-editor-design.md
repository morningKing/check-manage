# AI 编排图形化管理设计

**版本**：v1.0
**状态**：待评审
**日期**：2026-09-26
**前置**：P2 编排骨架 + P3-A7 管理面前端骨架已合入
**范围**：Phase ② 运行可视化（只读图视图）→ Phase ① 定义编辑器（拖拽 DAG 编辑器）

---

## 1. 背景

`AiOrchestrationManager.vue` 当前用 Element Plus 表格展示 definitions 与 runs，step 状态只能看文本列。编排 DAG 的拓扑关系（依赖/条件/并行）在表格中不可见，管理员无法直观理解流程结构或诊断卡点。

项目已安装 `@vue-flow/core` + `@vue-flow/background` + `@vue-flow/controls` + `@vue-flow/minimap` + `@dagrejs/dagre`，零新依赖。

## 2. 方案选型

**采纳 Vue Flow**：Vue 3 原生节点编辑器库，支持自定义节点组件、拖拽连线、缩放/平移、minimap。项目已有依赖且其他模块可能使用。备选 cytoscape（dist 里有 chunk 但非直接依赖）和 force-graph（力导向图，不适合 DAG）已否决。

## 3. Phase ② — 运行可视化（只读）

### 3.1 组件结构

```
AiOrchestrationManager.vue
  └─ Run 展开行
       ├─ OrchRunGraph.vue（Vue Flow 只读画布）
       │    ├─ OrchStepNode.vue（自定义节点：step 名称 + 状态徽标 + 耗时）
       │    └─ dagre 自动布局（rankdir=TB, nodesep=40, ranksep=60）
       └─ OrchStepDetail.vue（右侧抽屉：点击节点 → prompt/输出/错误）
```

### 3.2 数据映射

**Vue Flow nodes**：每个 `ai_orchestration_steps` 行映射为一个节点。

```typescript
// 输入：run 的 definition.nodes（拓扑）+ steps（状态）
// 输出：VueFlow Node[]
function toFlowNodes(nodes: NodeDef[], steps: StepRow[]): FlowNode[] {
  return nodes.map(n => {
    const step = steps.find(s => s.node_id === n.id)
    return {
      id: n.id,
      type: 'orch-step',
      position: dagreLayout(n.id, nodes, edges),  // dagre 计算坐标
      data: {
        nodeId: n.id,
        label: n.name ?? n.id,
        kind: n.kind,
        status: step?.status ?? 'blocked',
        durationS: step ? durationS(step) : null,
        error: step?.error_message ?? null,
      },
    }
  })
}
```

**Vue Flow edges**：每条 `definition.edges` 映射为一条边。

```typescript
function toFlowEdges(edges: EdgeDef[], steps: StepRow[]): FlowEdge[] {
  return edges.map(e => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    animated: steps.find(s => s.node_id === e.source)?.status === 'running',
    label: e.condition ? condLabel(e.condition) : undefined,
    style: { stroke: e.kind === 'reject' ? '#f56c6c' : '#409eff',
             strokeDasharray: e.kind === 'reject' ? '5,5' : undefined },
  }))
}
```

### 3.3 状态着色

| step.status | 节点边框色 | 徽标 |
|---|---|---|
| `succeeded` | `#67c23a` 绿 | ✓ |
| `failed` | `#f56c6c` 红 | ✗ |
| `running` | `#409eff` 蓝 | ● (CSS animation pulse) |
| `waiting_approval` | `#e6a23c` 橙 | ⏳ |
| `blocked` | `#909399` 灰 | ○ |
| `skipped` | `#c0c4cc` 浅灰 | → |
| `needs_review` | `#9b59b6` 紫 | ? |

### 3.4 交互

- **点击节点** → 右侧抽屉 `OrchStepDetail.vue` 显示该 step 的 `output`、`error_message`、`attempt_count`、`duration`
- **悬停边** → 高亮该边 + 关联的 source/target 节点
- **minimap**：右下角缩略图（Vue Flow 内置 `MiniMap` 组件）

### 3.5 dagre 布局

```typescript
import dagre from '@dagrejs/dagre'

function dagreLayout(nodes: NodeDef[], edges: EdgeDef[]): Record<string, {x,y}> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 60 })
  g.setDefaultEdgeLabel(() => ({}))
  nodes.forEach(n => g.setNode(n.id, { width: 180, height: 60 }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  // 从 g.node(id) 读取 {x, y}（dagre 返回中心点，Vue Flow 需要左上角）
}
```

---

## 4. Phase ① — 定义编辑器（拖拽 DAG 编辑器）

### 4.1 组件结构

```
OrchestrationDagEditor.vue（独立页面或模态框）
  ├─ Vue Flow 画布（editable 模式）
  │    ├─ OrchStepNode.vue（复用 Phase ② 节点，加选中态）
  │    ├─ NodePalette.vue（左侧面板：agent/tool/approval/join 四种，拖入画布）
  │    └─ NodePropertiesPanel.vue（右侧面板：选中节点 → 编辑属性）
  └─ 工具栏：保存 / 取消 / 校验
```

### 4.2 交互

- **添加节点**：从左侧 NodePalette 拖入画布 → 创建默认节点（kind 决定默认字段）
- **连线**：从节点底部 handle 拖到另一节点顶部 handle → 创建 `advance` 边
- **删边**：点击边选中 → Delete 键删除
- **节点属性**：点击节点 → 右侧属性面板 → 编辑 `name/prompt_template/model/skills/budget/timeout_sec/join_policy` 等字段
- **条件边**：选中边 → 属性面板 → 设置 `condition.field/op/value`
- **删除节点**：选中 → Delete 键 → 关联边自动删除

### 4.3 保存流程

```text
Vue Flow state（nodes/edges 坐标+data）
  → 转换为 definition JSON（nodes 提取 node_def 字段，edges 提取 kind/condition）
  → 前端预校验（环检测/node id 唯一/边引用存在）
  → POST /ai/orchestrations/definitions（后端 validate_definition 复检）
  → 成功 → 刷新列表
```

### 4.4 文件清单（Phase ① 增量）

| 文件 | 操作 | 行数 |
|---|---|---|
| `OrchestrationDagEditor.vue` | 新增 | ~200 行 |
| `NodePalette.vue` | 新增 | ~40 行 |
| `NodePropertiesPanel.vue` | 新增 | ~120 行 |
| `OrchStepNode.vue` | 修改 | +20 行（加选中态 + 编辑态图标） |

---

## 5. 数据流

```text
Phase ②（只读）
  GET /ai/orchestrations/runs/<id>
    → { run, steps: [{node_id, status, output, error_message, ...}] }
  + definition.nodes/edges（从 definition_version 冻结）
    → toFlowNodes + toFlowEdges + dagreLayout
    → <VueFlow :nodes :edges>

Phase ①（编辑）
  definition.nodes/edges（从 API 加载）
    → toFlowNodes（editable mode）+ dagreLayout（初始坐标）
    → 用户拖拽/连线/编辑
    → 保存时从 Vue Flow state 反向提取 definition JSON
    → POST /ai/orchestrations/definitions
```

---

## 6. 测试策略

| 层 | 测试 |
|---|---|
| dagre 布局 | 单测：3 节点线性 DAG → 断言 y 坐标递增、x 居中 |
| toFlowNodes | 单测：step 状态映射到节点 data 正确 |
| toFlowEdges | 单测：条件边 label、reject 边样式 |
| OrchRunGraph | vitest：渲染 3 节点 DAG → 断言节点数、边数、状态类名 |
| OrchStepNode | vitest：断言状态徽标文本/颜色 |
| OrchDagEditor | vitest：添加节点→断言 nodes 数；连线→断言 edges；删除→断言减少 |
| e2e | Playwright：打开 run 详情 → 截图断言 Vue Flow 画布存在 + 节点着色 |

---

## 7. 非目标

- 不支持运行时动态修改 DAG（只读 run graph）
- 不支持嵌套子图（DAG 展开为子流程）
- 不支持撤销/重做（Vue Flow 有 useVueFlow hooks 可后续加）
- 不支持多选批量操作
- 不做实时协作编辑

---

## 8. 涉及文件

| 文件 | Phase | 操作 |
|---|---|---|
| `src/components/admin/OrchRunGraph.vue` | ② | 新增 |
| `src/components/admin/OrchStepNode.vue` | ② | 新增 |
| `src/components/admin/OrchStepDetail.vue` | ② | 新增 |
| `src/components/admin/OrchDagEditor.vue` | ① | 新增 |
| `src/components/admin/NodePalette.vue` | ① | 新增 |
| `src/components/admin/NodePropertiesPanel.vue` | ① | 新增 |
| `src/views/admin/AiOrchestrationManager.vue` | ②① | 修改 |
| `src/composables/useDagreLayout.ts` | ② | 新增 |
| `src/utils/orchGraph.ts`（toFlowNodes/toFlowEdges/状态色映射） | ②① | 新增 |
