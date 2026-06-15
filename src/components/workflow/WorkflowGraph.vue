<!-- src/components/workflow/WorkflowGraph.vue
     工作流图形化设计器：Vue Flow 画布。可拖拽节点、从节点拉出连线建边、点选节点/边。
     节点坐标优先用 stage.position（拖拽持久化），缺失时用 dagre 自动布局。 -->
<template>
  <div class="wf-graph">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      :min-zoom="0.3"
      :max-zoom="2.5"
      :nodes-draggable="editable"
      :nodes-connectable="editable"
      :elements-selectable="true"
      fit-view-on-init
      @node-click="onNodeClick"
      @edge-click="onEdgeClick"
      @connect="onConnect"
      @node-drag-stop="onNodeDragStop"
    >
      <Background :gap="16" pattern-color="#e4e7ed" />
      <Controls :show-interactive="false" />

      <template #node-stage="{ data }">
        <div class="wf-node" :class="{ 'wf-node--active': data.id === selectedId }">
          <Handle v-if="editable" type="target" :position="Position.Left" />
          <div class="wf-node__head">
            <span class="wf-node__idx">{{ data.index + 1 }}</span>
            <span class="wf-node__name">{{ data.label }}</span>
          </div>
          <div class="wf-node__meta">
            <span class="wf-node__coll">{{ data.collection || '未绑定数据页' }}</span>
            <span v-if="data.statusField" class="wf-node__field">· {{ data.statusField }}</span>
          </div>
          <Handle v-if="editable" type="source" :position="Position.Right" />
        </div>
      </template>
    </VueFlow>

    <div class="wf-graph__legend">
      <span class="lg lg--adv"><i></i>推进/条件</span>
      <span class="lg lg--rej"><i></i>回退</span>
      <span v-if="editable" class="lg lg--hint">拖节点移位 · 从节点右侧拉到另一节点建边</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch, nextTick } from 'vue'
import { VueFlow, useVueFlow, Handle, MarkerType, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import dagre from '@dagrejs/dagre'
import type { WorkflowStage, WorkflowEdge } from '@/types/workflow'
import { deriveWorkflowGraph } from './workflowGraph'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = withDefaults(defineProps<{
  stages: WorkflowStage[]
  edges?: WorkflowEdge[]
  selectedId?: string
  selectedEdgeId?: string
  editable?: boolean
}>(), { editable: false })

const emit = defineEmits<{
  select: [stageId: string]
  'select-edge': [edgeId: string]
  connect: [payload: { source: string; target: string }]
  'node-move': [payload: { id: string; x: number; y: number }]
}>()

const NODE_WIDTH = 190
const NODE_HEIGHT = 64
const ADV_COLOR = '#409EFF'
const REJ_COLOR = '#E6A23C'

const model = computed(() => deriveWorkflowGraph(props.stages, props.edges))

/** dagre 仅给「没有 position」的节点兜底布局；advance 边参与排序，reject 反向边不参与避免成环。 */
function fallbackLayout() {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 50, ranksep: 120 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of model.value.nodes) g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const e of model.value.edges) if (e.kind === 'advance') g.setEdge(e.source, e.target)
  dagre.layout(g)
  const pos: Record<string, { x: number; y: number }> = {}
  for (const n of model.value.nodes) {
    const p = g.node(n.id)
    pos[n.id] = { x: p.x - NODE_WIDTH / 2, y: p.y - NODE_HEIGHT / 2 }
  }
  return pos
}

const nodes = computed(() => {
  const fb = fallbackLayout()
  return model.value.nodes.map((n) => ({
    id: n.id,
    type: 'stage',
    position: n.position || fb[n.id] || { x: 0, y: 0 },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: n,
  }))
})

const edges = computed(() =>
  model.value.edges.map((e) => {
    const isRej = e.kind === 'reject'
    const sel = e.id === props.selectedEdgeId
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
      animated: !isRej,
      type: isRej ? 'smoothstep' : 'default',
      markerEnd: { type: MarkerType.ArrowClosed, color: isRej ? REJ_COLOR : ADV_COLOR },
      style: {
        stroke: isRej ? REJ_COLOR : ADV_COLOR,
        strokeWidth: sel ? 3 : 1.6,
        ...(isRej ? { strokeDasharray: '5 3' } : {}),
      },
      labelStyle: { fontSize: '11px', fill: isRej ? REJ_COLOR : '#606266' },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.85 },
    }
  }),
)

function onNodeClick(e: { node: { id: string } }) {
  emit('select', e.node.id)
}
function onEdgeClick(e: { edge: { id: string } }) {
  emit('select-edge', e.edge.id)
}
function onConnect(params: { source: string; target: string }) {
  if (params.source && params.target) emit('connect', { source: params.source, target: params.target })
}
function onNodeDragStop(e: { node: { id: string; position: { x: number; y: number } } }) {
  emit('node-move', { id: e.node.id, x: Math.round(e.node.position.x), y: Math.round(e.node.position.y) })
}

const { fitView, onNodesInitialized } = useVueFlow()
onNodesInitialized(() => fitView({ padding: 0.2 }))
watch(
  () => model.value.nodes.map((n) => n.id).join(','),
  async () => { await nextTick(); fitView({ padding: 0.2 }) },
)
</script>

<style scoped>
.wf-graph {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 240px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafbfc;
  overflow: hidden;
}
.wf-node {
  box-sizing: border-box;
  width: 190px;
  background: #fff;
  border: 2px solid #dcdfe6;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.wf-node:hover { border-color: #409eff; }
.wf-node--active { border-color: #409eff; box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.18); }
.wf-node__head { display: flex; align-items: center; gap: 6px; padding: 6px 10px 2px; }
.wf-node__idx {
  flex: none; width: 18px; height: 18px; line-height: 18px; text-align: center;
  font-size: 11px; border-radius: 50%; background: #409eff; color: #fff;
}
.wf-node__name {
  font-weight: 600; font-size: 13px; color: #303133;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wf-node__meta {
  padding: 0 10px 7px 34px; font-size: 11px; color: #909399;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wf-node__field { color: #67c23a; }
.wf-graph__legend {
  position: absolute; right: 10px; bottom: 8px;
  display: flex; gap: 12px; align-items: center;
  font-size: 11px; color: #606266;
  background: rgba(255, 255, 255, 0.85); padding: 3px 8px; border-radius: 6px; z-index: 5;
}
.lg { display: inline-flex; align-items: center; gap: 5px; }
.lg i { width: 16px; height: 0; border-top: 2px solid; }
.lg--adv i { border-color: #409eff; }
.lg--rej i { border-top-style: dashed; border-color: #e6a23c; }
.lg--hint { color: #909399; }
</style>
