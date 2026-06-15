<!-- src/components/workflow/WorkflowGraph.vue
     工作流阶段链的图形化视图：Vue Flow 画布 + dagre 自动布局。
     纯展示 + 点选定位（点节点 emit select），不在画布上编辑——编辑仍走下方表单。 -->
<template>
  <div class="wf-graph">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      :min-zoom="0.3"
      :max-zoom="2.5"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :elements-selectable="true"
      fit-view-on-init
      @node-click="onNodeClick"
    >
      <Background :gap="16" pattern-color="#e4e7ed" />
      <Controls :show-interactive="false" />

      <template #node-stage="{ data }">
        <div class="wf-node" :class="{ 'wf-node--active': data.id === selectedId }">
          <div class="wf-node__head">
            <span class="wf-node__idx">{{ data.index + 1 }}</span>
            <span class="wf-node__name">{{ data.label }}</span>
          </div>
          <div class="wf-node__meta">
            <span class="wf-node__coll">{{ data.collection || '未绑定数据页' }}</span>
            <span v-if="data.statusField" class="wf-node__field">· {{ data.statusField }}</span>
          </div>
        </div>
      </template>
    </VueFlow>

    <div class="wf-graph__legend">
      <span class="lg lg--adv"><i></i>推进</span>
      <span class="lg lg--rej"><i></i>回退</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { VueFlow, MarkerType, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import dagre from '@dagrejs/dagre'
import type { WorkflowStage } from '@/types/workflow'
import { deriveWorkflowGraph } from './workflowGraph'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps<{
  stages: WorkflowStage[]
  selectedId?: string
}>()

const emit = defineEmits<{ select: [stageId: string] }>()

const NODE_WIDTH = 190
const NODE_HEIGHT = 64

const ADV_COLOR = '#409EFF'
const REJ_COLOR = '#E6A23C'

/** 用 dagre 仅按「推进边」排版（回退边是反向的，纳入会形成环、破坏线性布局）。 */
function layout(model: ReturnType<typeof deriveWorkflowGraph>) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 50, ranksep: 90 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of model.nodes) g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const e of model.edges) if (e.kind === 'advance') g.setEdge(e.source, e.target)
  dagre.layout(g)
  const pos: Record<string, { x: number; y: number }> = {}
  for (const n of model.nodes) {
    const p = g.node(n.id)
    pos[n.id] = { x: p.x - NODE_WIDTH / 2, y: p.y - NODE_HEIGHT / 2 }
  }
  return pos
}

const model = computed(() => deriveWorkflowGraph(props.stages))

const nodes = computed(() => {
  const pos = layout(model.value)
  return model.value.nodes.map((n) => ({
    id: n.id,
    type: 'stage',
    position: pos[n.id] || { x: 0, y: 0 },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: n,
  }))
})

const edges = computed(() =>
  model.value.edges.map((e) => {
    const isRej = e.kind === 'reject'
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
        strokeWidth: 1.6,
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
</script>

<style scoped>
.wf-graph {
  position: relative;
  width: 100%;
  height: 260px;
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

.wf-node:hover {
  border-color: #409eff;
}

.wf-node--active {
  border-color: #409eff;
  box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.18);
}

.wf-node__head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 2px;
}

.wf-node__idx {
  flex: none;
  width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  font-size: 11px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
}

.wf-node__name {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wf-node__meta {
  padding: 0 10px 7px 34px;
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wf-node__field {
  color: #67c23a;
}

.wf-graph__legend {
  position: absolute;
  right: 10px;
  bottom: 8px;
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #606266;
  background: rgba(255, 255, 255, 0.85);
  padding: 3px 8px;
  border-radius: 6px;
  z-index: 5;
}

.lg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.lg i {
  width: 16px;
  height: 0;
  border-top: 2px solid;
}

.lg--adv i {
  border-color: #409eff;
}

.lg--rej i {
  border-top-style: dashed;
  border-color: #e6a23c;
}
</style>
