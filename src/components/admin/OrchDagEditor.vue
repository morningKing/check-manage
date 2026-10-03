<script setup lang="ts">
/**
 * 拖拽式 DAG 定义编辑器（spec §4）：左侧 NodePalette + 中间 Vue Flow 画布
 * （editable）+ 右侧 NodePropertiesPanel + 右上角工具栏。
 *
 * 数据流（spec §4.3）：definition prop → Vue Flow nodes/edges（dagre 初始布局）
 * → 用户拖拽/连线/编辑 → 保存时从 Vue Flow state 反向提取 definition JSON
 * → emit('save')，由父组件 POST /ai/orchestrations/definitions（后端
 * validate_definition 复检）。前端预校验：空画布 / 自环 / 环检测。
 */
import { ref, computed, markRaw } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { ElMessage } from 'element-plus'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { dagreLayout } from '@/utils/orchGraph'
import NodePalette from './NodePalette.vue'
import NodePropertiesPanel from './NodePropertiesPanel.vue'
import OrchStepNode from './OrchStepNode.vue'

const props = defineProps<{
  definition: { id?: string; name?: string; nodes: any[]; edges: any[] } | null
}>()
const emit = defineEmits<{
  (e: 'save', json: Record<string, any>): void
  (e: 'cancel'): void
}>()

// useVueFlow 必须在 <VueFlow> 的父组件 setup 里调用；getNodes/getEdges 是
// computed ref（onSave 读取 .value），addNodes/addEdges/findNode 操作内部 store。
const { addNodes, addEdges, findNode, getNodes, getEdges, onConnect } = useVueFlow()

const selectedNode = ref<Record<string, any> | null>(null)

// 自定义节点类型：markRaw 避免 Vue Flow 把组件包成响应式（与 OrchRunGraph 一致）
const nodeTypes = { 'orch-step': markRaw(OrchStepNode) as any }

// definition → Vue Flow nodes（dagre 自动布局作为初始坐标；status 固定 blocked——
// 编辑器不关心运行态，OrchStepNode 仅借其渲染 label/kind 徽标）
const initialNodes = computed(() => {
  if (!props.definition) return []
  const positions = dagreLayout(props.definition.nodes, props.definition.edges)
  return props.definition.nodes.map((n: any) => ({
    id: n.id,
    type: 'orch-step',
    position: positions[n.id] ?? { x: 0, y: 0 },
    data: {
      nodeId: n.id, label: n.name ?? n.id, kind: n.kind ?? 'agent',
      status: 'blocked', error: null,
      prompt_template: n.prompt_template ?? '',
      model: n.model ?? '',
      skills: n.skills ?? [],
      budget: n.budget ?? {},
      timeout_sec: n.timeout_sec,
      join_policy: n.join_policy,
      priority: n.priority ?? 0,
    },
  }))
})

// definition → Vue Flow edges。边的 kind/condition 存进 edge.data 以便保存时
// 还原（连线新建的边默认 advance）；label 仅作展示。
const initialEdges = computed(() => {
  if (!props.definition) return []
  return props.definition.edges.map((e: any, i: number) => ({
    id: `e${i}-${e.source}-${e.target}`,
    source: e.source, target: e.target,
    label: e.condition ? `${e.condition.op ?? ''} ${e.condition.value ?? ''}`.trim() : undefined,
    style: { stroke: e.kind === 'reject' ? '#f56c6c' : '#409eff' },
    data: { kind: e.kind ?? 'advance', condition: e.condition ?? null },
  }))
})

// 连线：从节点底部 handle 拖到另一节点顶部 handle → 创建 advance 边（spec §4.2）
onConnect((params: any) => {
  addEdges([{
    ...params,
    id: `e-${Date.now()}`,
    type: 'smoothstep',
    style: { stroke: '#409eff' },
    data: { kind: 'advance', condition: null },
  }])
})

// 点击节点 → 属性面板
function onNodeClick(e: any) {
  const node = e.node
  if (node) selectedNode.value = { id: node.id, ...node.data }
}

// 属性面板更新 → 写回 Vue Flow 节点 data（name 同步 label）
function onNodeUpdate(fields: Record<string, any>) {
  if (!selectedNode.value) return
  const node = findNode(selectedNode.value.id)
  if (!node) return
  for (const [k, v] of Object.entries(fields)) {
    node.data = { ...node.data, [k]: v }
    if (k === 'name') node.data = { ...node.data, label: v }
  }
  selectedNode.value = { id: node.id, ...node.data }
}

// palette 添加节点（点击 / 拖入兜底）
function onAddNode(kind: string) {
  const id = `${kind}-${Date.now()}`
  addNodes([{
    id,
    type: 'orch-step',
    position: { x: 200 + Math.random() * 200, y: 100 + Math.random() * 200 },
    data: {
      nodeId: id, label: `${kind} node`, kind,
      status: 'blocked', error: null,
      prompt_template: '', model: '', skills: [],
      budget: {}, timeout_sec: undefined, join_policy: undefined,
      priority: 0,
    },
  }])
}

/** 环检测（spec §4.3 前端预校验）：DFS 三色标记 */
function hasCycle(nodes: any[], edges: any[]): boolean {
  const adj: Record<string, string[]> = {}
  for (const e of edges) {
    if (e.source === e.target) return true // 自环
    ;(adj[e.source] = adj[e.source] ?? []).push(e.target)
  }
  const state: Record<string, 0 | 1> = {}
  const dfs = (u: string): boolean => {
    if (state[u] === 0) return true
    if (state[u] === 1) return false
    state[u] = 0
    for (const v of adj[u] ?? []) if (dfs(v)) return true
    state[u] = 1
    return false
  }
  return nodes.some(n => dfs(n.id))
}

// 保存：Vue Flow state → definition JSON（结构对齐后端 validate_definition）
function onSave() {
  const nodes = getNodes.value
  const edges = getEdges.value
  if (nodes.length === 0) {
    ElMessage.warning('画布为空：至少需要一个节点')
    return
  }
  if (hasCycle(nodes, edges)) {
    ElMessage.warning('DAG 存在环，请调整连线')
    return
  }
  emit('save', {
    id: props.definition?.id,
    name: props.definition?.name ?? 'Unnamed',
    nodes: nodes.map(n => ({
      id: n.id,
      kind: n.data?.kind ?? 'agent',
      name: n.data?.label ?? n.id,
      prompt_template: n.data?.prompt_template ?? '',
      model: n.data?.model ?? '',
      skills: n.data?.skills ?? [],
      budget: n.data?.budget ?? {},
      timeout_sec: n.data?.timeout_sec,
      join_policy: n.data?.join_policy,
      priority: n.data?.priority ?? 0,
    })),
    edges: edges.map(e => ({
      source: e.source, target: e.target,
      kind: e.data?.kind ?? 'advance',
      ...(e.data?.condition ? { condition: e.data.condition } : {}),
    })),
  })
}
</script>

<template>
  <div class="orch-dag-editor">
    <NodePalette @add-node="onAddNode" />
    <div class="orch-dag-editor__canvas">
      <div class="orch-dag-editor__toolbar">
        <el-button type="primary" size="small" @click="onSave">保存</el-button>
        <el-button size="small" @click="$emit('cancel')">取消</el-button>
      </div>
      <VueFlow
        :nodes="initialNodes"
        :edges="initialEdges"
        :node-types="nodeTypes"
        :fit-view-on-init="true"
        @node-click="onNodeClick"
      >
        <Background pattern-color="#e0e0e0" :gap="20" />
        <Controls />
      </VueFlow>
    </div>
    <NodePropertiesPanel :node="selectedNode" @update:node="onNodeUpdate" />
  </div>
</template>

<style scoped>
.orch-dag-editor {
  display: flex; height: 600px;
  border: 1px solid #dcdfe6; border-radius: 8px; overflow: hidden;
}
.orch-dag-editor__canvas { flex: 1; position: relative; }
.orch-dag-editor__toolbar {
  position: absolute; top: 8px; right: 8px; z-index: 10; display: flex; gap: 8px;
}
</style>
