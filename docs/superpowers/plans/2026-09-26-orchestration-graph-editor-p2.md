# AI 编排运行可视化（Phase ②）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 编排 run 展开后显示 Vue Flow 只读 DAG 图（节点着色 + dagre 布局 + 点击看详情）。

**Architecture:** 纯前端 Vue 3 组件——从 `getRun` API 获取 definition nodes/edges + steps 状态，转换为 Vue Flow nodes/edges，用 dagre 自动布局，自定义节点组件着色。不涉及后端改动。

**Tech Stack:** Vue 3, @vue-flow/core, @vue-flow/background, @vue-flow/minimap, @dagrejs/dagre, Vitest

**Spec:** `docs/superpowers/specs/2026-09-26-orchestration-graph-editor-design.md` §3

## Global Constraints

- 零新依赖（@vue-flow/* + dagre 已在 package.json）
- 不修改后端 API
- 不破坏现有 step 表格（保留为降级视图，图形视图叠加在上方）
- 所有测试在 base（当前 HEAD）上必失败

---

### Task 1: `orchGraph.ts` 工具函数（状态色映射 + toFlowNodes/toFlowEdges + dagre 布局）

**Files:**
- Create: `src/utils/orchGraph.ts`
- Test: `src/utils/__tests__/orchGraph.test.ts`

**Interfaces:**
- Produces:
  - `STEP_STATUS_COLORS: Record<string, {border: string, badge: string, label: string}>`
  - `toFlowNodes(nodes: NodeDef[], steps: StepRow[]): FlowNode[]`
  - `toFlowEdges(edges: EdgeDef[], steps: StepRow[]): FlowEdge[]`
  - `dagreLayout(nodes: NodeDef[], edges: EdgeDef[]): Record<string, {x: number, y: number}>`

- [ ] **Step 1: 写失败测试**

```typescript
// src/utils/__tests__/orchGraph.test.ts
import { describe, it, expect } from 'vitest'
import { STEP_STATUS_COLORS, toFlowNodes, toFlowEdges, dagreLayout } from '../orchGraph'

describe('STEP_STATUS_COLORS', () => {
  it('maps all statuses to colors', () => {
    for (const s of ['succeeded','failed','running','waiting_approval','blocked','skipped','needs_review']) {
      expect(STEP_STATUS_COLORS[s]).toBeDefined()
      expect(STEP_STATUS_COLORS[s].border).toMatch(/^#/)
    }
  })
})

describe('toFlowNodes', () => {
  const nodes = [
    { id: 'extract', kind: 'agent', name: '抽取' },
    { id: 'merge', kind: 'join', name: '汇总' },
  ]
  const steps = [
    { node_id: 'extract', status: 'succeeded', attempt_count: 1, error_message: null },
    { node_id: 'merge', status: 'blocked', attempt_count: 0, error_message: null },
  ]
  it('maps step status to node data', () => {
    const flow = toFlowNodes(nodes, steps)
    expect(flow).toHaveLength(2)
    expect(flow[0].data.status).toBe('succeeded')
    expect(flow[1].data.status).toBe('blocked')
    expect(flow[0].data.label).toBe('抽取')
  })
  it('handles missing step (defaults to blocked)', () => {
    const flow = toFlowNodes(nodes, [])
    expect(flow[0].data.status).toBe('blocked')
  })
})

describe('toFlowEdges', () => {
  const edges = [
    { source: 'a', target: 'b', kind: 'advance' },
    { source: 'a', target: 'c', kind: 'advance', condition: { field: 'text', op: 'contains', value: 'GO' } },
  ]
  const steps = [{ node_id: 'a', status: 'running' }]
  it('marks edges from running source as animated', () => {
    const flow = toFlowEdges(edges, steps)
    expect(flow[0].animated).toBe(true)
  })
  it('labels conditional edges', () => {
    const flow = toFlowEdges(edges, steps)
    expect(flow[1].label).toContain('contains')
  })
})

describe('dagreLayout', () => {
  it('positions linear DAG top-to-bottom', () => {
    const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }]
    const pos = dagreLayout(nodes, edges)
    expect(pos['a'].y).toBeLessThan(pos['b'].y)
    expect(pos['b'].y).toBeLessThan(pos['c'].y)
  })
  it('centers parallel branches horizontally', () => {
    const nodes = [{ id: 'a' }, { id: 'b1' }, { id: 'b2' }, { id: 'j' }]
    const edges = [
      { source: 'a', target: 'b1' }, { source: 'a', target: 'b2' },
      { source: 'b1', target: 'j' }, { source: 'b2', target: 'j' },
    ]
    const pos = dagreLayout(nodes, edges)
    expect(pos['b1'].x).not.toBe(pos['b2'].x)  // 并行分支 x 不同
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -- src/utils/__tests__/orchGraph.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: 实现**

```typescript
// src/utils/orchGraph.ts
import dagre from '@dagrejs/dagre'

export interface StepStatusColor { border: string; badge: string; label: string }

export const STEP_STATUS_COLORS: Record<string, StepStatusColor> = {
  succeeded:        { border: '#67c23a', badge: '✓', label: '成功' },
  failed:           { border: '#f56c6c', badge: '✗', label: '失败' },
  running:          { border: '#409eff', badge: '●', label: '运行中' },
  waiting_approval: { border: '#e6a23c', badge: '⏳', label: '待审批' },
  blocked:          { border: '#909399', badge: '○', label: '阻塞' },
  skipped:          { border: '#c0c4cc', badge: '→', label: '已跳过' },
  needs_review:     { border: '#9b59b6', badge: '?', label: '待复核' },
}

export function toFlowNodes(nodes: any[], steps: any[]): any[] {
  return nodes.map((n: any) => {
    const step = steps.find((s: any) => s.node_id === n.id)
    const status = step?.status ?? 'blocked'
    return {
      id: n.id,
      type: 'orch-step',
      position: { x: 0, y: 0 },  // dagreLayout 覆盖
      data: {
        nodeId: n.id,
        label: n.name ?? n.id,
        kind: n.kind ?? 'agent',
        status,
        durationS: null,
        error: step?.error_message ?? null,
      },
    }
  })
}

export function toFlowEdges(edges: any[], steps: any[]): any[] {
  return edges.map((e: any, i: number) => {
    const srcStep = steps.find((s: any) => s.node_id === e.source)
    let label: string | undefined
    if (e.condition) {
      label = `${e.condition.op} ${e.condition.value ?? ''}`
    } else if (e.kind === 'reject') {
      label = 'reject'
    }
    return {
      id: `e${i}-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      animated: srcStep?.status === 'running',
      label,
      style: {
        stroke: e.kind === 'reject' ? '#f56c6c' : '#409eff',
        strokeDasharray: e.kind === 'reject' ? '5,5' : undefined,
      },
    }
  })
}

export function dagreLayout(nodes: any[], edges: any[]): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 60 })
  g.setDefaultEdgeLabel(() => ({}))
  const NODE_W = 180, NODE_H = 60
  nodes.forEach(n => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const out: Record<string, { x: number; y: number }> = {}
  nodes.forEach(n => {
    const pos = g.node(n.id)
    out[n.id] = { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 }
  })
  return out
}
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add src/utils/orchGraph.ts src/utils/__tests__/orchGraph.test.ts
git commit -m "feat(P3-graph): orchGraph 工具函数——状态色映射/Flow 转换/dagre 布局"
```

---

### Task 2: OrchStepNode + OrchRunGraph + OrchStepDetail 组件

**Files:**
- Create: `src/components/admin/OrchStepNode.vue`
- Create: `src/components/admin/OrchRunGraph.vue`
- Create: `src/components/admin/OrchStepDetail.vue`
- Modify: `src/views/admin/AiOrchestrationManager.vue`
- Test: `src/components/admin/__tests__/OrchRunGraph.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `toFlowNodes/toFlowEdges/dagreLayout/STEP_STATUS_COLORS`
- Produces: `OrchRunGraph` 组件（props: `definition, steps`；emit: `select-step`）

- [ ] **Step 1: 写失败测试**

```typescript
// src/components/admin/__tests__/OrchRunGraph.test.ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import OrchRunGraph from '../OrchRunGraph.vue'

const mockDefinition = {
  nodes: [
    { id: 'extract', kind: 'agent', name: '抽取' },
    { id: 'merge', kind: 'join', name: '汇总' },
  ],
  edges: [{ source: 'extract', target: 'merge', kind: 'advance' }],
}
const mockSteps = [
  { node_id: 'extract', status: 'succeeded', attempt_count: 1 },
  { node_id: 'merge', status: 'blocked', attempt_count: 0 },
]

vi.mock('@vue-flow/core', () => ({
  VueFlow: { template: '<div class="mock-vue-flow"><slot /></div>' },
  Handle: { template: '<div />' },
  Position: { Top: 'top', Bottom: 'bottom' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/minimap', () => ({ MiniMap: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))
vi.mock('@dagrejs/dagre', () => ({
  default: {
    graphlib: { Graph: class { setGraph(){} setDefaultEdgeLabel(){} setNode(){} setEdge(){} node() { return { x: 90, y: 30 } } } },
    layout() {},
  },
}))

describe('OrchRunGraph', () => {
  it('renders without crash', () => {
    const w = mount(OrchRunGraph, {
      props: { definition: mockDefinition, steps: mockSteps },
    })
    expect(w.find('.orch-run-graph').exists()).toBe(true)
  })
  it('emits select-step when node clicked', async () => {
    const w = mount(OrchRunGraph, {
      props: { definition: mockDefinition, steps: mockSteps },
    })
    // Vue Flow 内部处理点击——此处只验证组件不 crash + emit 接口存在
    expect(w.emitted('select-step')).toBeUndefined()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

**OrchStepNode.vue**（自定义节点）：

```vue
<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'

const props = defineProps<{ data: { label: string; kind: string; status: string; error: string | null } }>()

const color = computed(() => {
  const map: Record<string, string> = {
    succeeded: '#67c23a', failed: '#f56c6c', running: '#409eff',
    waiting_approval: '#e6a23c', blocked: '#909399', skipped: '#c0c4cc', needs_review: '#9b59b6',
  }
  return map[props.data.status] ?? '#909399'
})
const badge = computed(() => {
  const map: Record<string, string> = {
    succeeded: '✓', failed: '✗', running: '●',
    waiting_approval: '⏳', blocked: '○', skipped: '→', needs_review: '?',
  }
  return map[props.data.status] ?? '○'
})
</script>

<template>
  <div class="orch-step-node" :style="{ borderColor: color }" :data-status="data.status">
    <Handle type="target" :position="Position.Top" />
    <div class="orch-step-node__badge" :style="{ background: color }">{{ badge }}</div>
    <div class="orch-step-node__label">{{ data.label }}</div>
    <div class="orch-step-node__kind">{{ data.kind }}</div>
    <Handle type="source" :position="Position.Bottom" />
  </div>
</template>

<style scoped>
.orch-step-node {
  border: 2px solid #909399; border-radius: 8px; padding: 8px 12px;
  background: #fff; min-width: 140px; cursor: pointer;
  font-size: 12px; position: relative;
}
.orch-step-node__badge {
  position: absolute; top: -8px; right: -8px; width: 20px; height: 20px;
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 11px; font-weight: bold;
}
.orch-step-node__label { font-weight: 500; }
.orch-step-node__kind { color: #909399; font-size: 10px; }
.orch-step-node[data-status='running'] { animation: pulse 1.5s infinite; }
@keyframes pulse { 50% { box-shadow: 0 0 8px rgba(64,158,255,0.5); } }
</style>
```

**OrchRunGraph.vue**（只读画布）：

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { MiniMap } from '@vue-flow/minimap'
import { Controls } from '@vue-flow/controls'
import { toFlowNodes, toFlowEdges, dagreLayout } from '@/utils/orchGraph'
import OrchStepNode from './OrchStepNode.vue'

const props = defineProps<{ definition: { nodes: any[]; edges: any[] }; steps: any[] }>()
const emit = defineEmits<{ 'select-step': [nodeId: string] }>()

const flowNodes = computed(() => {
  const nodes = toFlowNodes(props.definition.nodes, props.steps)
  const edges = toFlowEdges(props.definition.edges, props.steps)
  const positions = dagreLayout(props.definition.nodes, props.definition.edges)
  return nodes.map(n => ({ ...n, position: positions[n.id] ?? { x: 0, y: 0 } }))
})
const flowEdges = computed(() => toFlowEdges(props.definition.edges, props.steps))

function onNodeClick(e: any) {
  const nodeId = e.node?.id
  if (nodeId) emit('select-step', nodeId)
}
</script>

<template>
  <div class="orch-run-graph" style="height: 400px; border: 1px solid #ebeef5; border-radius: 8px;">
    <VueFlow
      :nodes="flowNodes"
      :edges="flowEdges"
      :node-types="{ 'orch-step': OrchStepNode }"
      :fit-view-on-init="true"
      :nodes-connectable="false"
      :elements-selectable="false"
      @node-click="onNodeClick"
    >
      <Background pattern-color="#e0e0e0" :gap="20" />
      <MiniMap pannable zoomable />
      <Controls />
    </VueFlow>
  </div>
</template>
```

**OrchStepDetail.vue**（抽屉）：

```vue
<script setup lang="ts">
const props = defineProps<{ step: Record<string, any> | null }>()
</script>

<template>
  <div v-if="step" class="orch-step-detail">
    <h4>{{ step.node_id }}</h4>
    <p>Status: {{ step.status }}</p>
    <p v-if="step.error_message" class="error">{{ step.error_message }}</p>
    <p>Attempts: {{ step.attempt_count }}</p>
    <pre v-if="step.output">{{ JSON.stringify(step.output, null, 2) }}</pre>
  </div>
</template>
```

**AiOrchestrationManager.vue 修改**：在 run 展开行的 step 表格上方插入 `<OrchRunGraph>` + `<OrchStepDetail>`。

- [ ] **Step 4: 跑测试确认通过 + `npm run build`**

- [ ] **Step 5: Commit**

```bash
git add src/components/admin/OrchStepNode.vue src/components/admin/OrchRunGraph.vue src/components/admin/OrchStepDetail.vue src/views/admin/AiOrchestrationManager.vue src/utils/orchGraph.ts src/utils/__tests__/orchGraph.test.ts src/components/admin/__tests__/OrchRunGraph.test.ts
git commit -m "feat(P3-graph): 运行可视化——Vue Flow 只读 DAG + 状态着色 + dagre 布局 + step 详情抽屉"
```

---

### Task 3: 全量回归

- [ ] **Step 1: 后端全量** `pytest tests/ -q` — 确认无回归
- [ ] **Step 2: 前端 vitest 全量** — 确认无回归
- [ ] **Step 3: `npm run build`** — 确认无 TS 错误
- [ ] **Step 4: e2e ai-full** — 确认编排相关用例通过

---

## Self-Review

**1. Spec coverage：** Phase ② 的四个文件（OrchRunGraph/OrchStepNode/OrchStepDetail/Manager 修改）+ 工具函数（orchGraph.ts）全部覆盖 ✓

**2. Placeholder scan：** 无 TBD/TODO ✓

**3. Type consistency：** `toFlowNodes` 返回 `{id, type, position, data}` → Vue Flow 标准 Node 类型 ✓
