<!-- src/components/PageConfigRelationGraph.vue -->
<template>
  <div class="relation-graph-container">
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>正在加载关系图谱...</span>
    </div>

    <el-empty
      v-if="!loading && nodes.length === 1 && edges.length === 0"
      description="当前页面配置无关联关系"
    />

    <VueFlow
      v-if="!loading && nodes.length > 0"
      :nodes="nodes"
      :edges="edges"
      :default-viewport="{ zoom: 1.0 }"
      :min-zoom="0.2"
      :max-zoom="4"
      fit-view-on-init
      @node-click="handleNodeClick"
      @node-mouse-enter="handleNodeHover"
      @node-mouse-leave="handleNodeLeave"
    >
      <Background />
      <Controls />
      <MiniMap
        :node-color="miniMapNodeColor"
        :mask-color="'rgba(0,0,0,0.08)'"
        pannable
        zoomable
      />

      <!-- Custom node template -->
      <template #node-custom="{ data }">
        <div
          class="custom-node"
          :class="{ 'current-node': data.isCurrent }"
        >
          <div class="node-header" :class="{ 'current-header': data.isCurrent }">
            {{ data.name }}
          </div>
          <div class="node-body">
            <div class="node-meta">{{ data.fields }} 个字段</div>
            <div class="node-id">{{ data.id }}</div>
          </div>
        </div>
      </template>

      <!-- Custom edge template -->
      <template #edge-custom="edgeProps">
        <BaseEdge
          :id="edgeProps.id"
          :path="computeEdgePath(edgeProps)[0]"
          :style="getEdgeLineStyle(edgeProps.data)"
          :class="getEdgeClass(edgeProps)"
          :marker-end="edgeProps.markerEnd"
        />
        <EdgeLabelRenderer>
          <div
            class="edge-label-badge"
            :class="[
              `edge-label-${edgeProps.data?.relationType}`,
              { 'edge-dimmed': hoveredNodeId && !isEdgeConnected(edgeProps) }
            ]"
            :style="{
              transform: `translate(-50%, -50%) translate(${computeEdgePath(edgeProps)[1]}px, ${computeEdgePath(edgeProps)[2]}px)`,
              pointerEvents: 'all',
            }"
          >
            {{ edgeProps.data?.fieldName }} · {{ relationTypeLabel(edgeProps.data?.relationType) }}
          </div>
        </EdgeLabelRenderer>
      </template>
    </VueFlow>

    <!-- Legend panel -->
    <div v-if="!loading && nodes.length > 0" class="legend-panel">
      <div class="legend-title">关联类型</div>
      <div class="legend-item">
        <svg width="24" height="8">
          <line x1="0" y1="4" x2="24" y2="4" stroke="#409EFF" stroke-width="2" stroke-dasharray="6,3" />
        </svg>
        <span class="legend-text" style="color: #409EFF">relation (M:N)</span>
      </div>
      <div class="legend-item">
        <svg width="24" height="8">
          <line x1="0" y1="4" x2="24" y2="4" stroke="#67C23A" stroke-width="2" />
        </svg>
        <span class="legend-text" style="color: #67C23A">reference (1:N)</span>
      </div>
      <div class="legend-item">
        <svg width="24" height="8">
          <line x1="0" y1="4" x2="24" y2="4" stroke="#E6A23C" stroke-width="2" stroke-dasharray="2,2" />
        </svg>
        <span class="legend-text" style="color: #E6A23C">quoteSelect</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { VueFlow, BaseEdge, EdgeLabelRenderer, MarkerType, getSmoothStepPath } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getPageConfigRelations } from '@/api/page'
import dagre from '@dagrejs/dagre'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/minimap/dist/style.css'

const props = defineProps<{
  pageId: string
}>()

const emit = defineEmits<{
  navigate: [targetPageId: string]
}>()

const loading = ref(false)
const nodes = ref<any[]>([])
const edges = ref<any[]>([])
const hoveredNodeId = ref<string | null>(null)

defineExpose({ nodes, edges, loading })

// ─── Edge styling ───

const EDGE_COLORS: Record<string, string> = {
  relation: '#409EFF',
  reference: '#67C23A',
  quoteSelect: '#E6A23C',
}

function relationTypeLabel(type: string) {
  switch (type) {
    case 'relation': return 'relation'
    case 'reference': return 'reference'
    case 'quoteSelect': return 'quote'
    default: return type
  }
}

function getEdgeLineStyle(data: any) {
  const color = EDGE_COLORS[data?.relationType] || '#909399'
  const dasharray = data?.relationType === 'relation'
    ? '6,3'
    : data?.relationType === 'quoteSelect'
      ? '2,2'
      : undefined
  return {
    stroke: color,
    strokeWidth: 2,
    ...(dasharray ? { strokeDasharray: dasharray } : {}),
  }
}

function getEdgeClass(edgeProps: any) {
  const classes: string[] = []
  if (edgeProps.data?.relationType === 'relation') {
    classes.push('edge-animated')
  }
  if (hoveredNodeId.value && !isEdgeConnected(edgeProps)) {
    classes.push('edge-dimmed')
  }
  return classes
}

function isEdgeConnected(edgeProps: any) {
  if (!hoveredNodeId.value) return true
  return edgeProps.source === hoveredNodeId.value || edgeProps.target === hoveredNodeId.value
}

// ─── Edge path computation ───

function computeEdgePath(edgeProps: any): [string, number, number, number, number] {
  return getSmoothStepPath({
    sourceX: edgeProps.sourceX,
    sourceY: edgeProps.sourceY,
    sourcePosition: edgeProps.sourcePosition,
    targetX: edgeProps.targetX,
    targetY: edgeProps.targetY,
    targetPosition: edgeProps.targetPosition,
  })
}

// ─── MiniMap ───

function miniMapNodeColor(node: any) {
  return node.data?.isCurrent ? '#409EFF' : '#C0C4CC'
}

// ─── Dagre layout ───

const NODE_WIDTH = 180
const NODE_HEIGHT = 80

function applyDagreLayout(
  rawNodes: any[],
  rawEdges: any[],
) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 80, ranksep: 200, edgesep: 40 })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of rawNodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const edge of rawEdges) {
    g.setEdge(edge.source, edge.target)
  }

  dagre.layout(g)

  return rawNodes.map(node => {
    const pos = g.node(node.id)
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    }
  })
}

// ─── Data loading ───

async function loadRelations() {
  loading.value = true

  try {
    const result = await getPageConfigRelations(props.pageId, 3)

    const rawNodes = result.nodes.map(n => ({
      id: n.id,
      type: 'custom',
      position: { x: 0, y: 0 },
      data: {
        name: n.name,
        fields: n.fields,
        id: n.id,
        isCurrent: n.id === props.pageId,
      },
    }))

    const rawEdges = result.edges.map((e, idx) => {
      const sourceId = e.source.startsWith('page-') ? e.source : `page-${e.source}`
      const targetId = e.target.startsWith('page-') ? e.target : `page-${e.target}`

      return {
        id: `edge-${idx}-${sourceId}-${targetId}`,
        source: sourceId,
        target: targetId,
        type: 'custom',
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: EDGE_COLORS[e.type] || '#909399',
        },
        data: {
          fieldName: e.label,
          relationType: e.type,
        },
      }
    })

    nodes.value = applyDagreLayout(rawNodes, rawEdges)
    edges.value = rawEdges
  } catch (error: any) {
    const msg = error?.response?.data?.error || '加载关系图谱失败'
    ElMessage.error(msg)
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

// ─── Interaction ───

function handleNodeClick(event: { node: any }) {
  const targetPageId = event.node.id
  if (targetPageId === props.pageId) return
  emit('navigate', targetPageId)
}

function handleNodeHover(event: { node: any }) {
  hoveredNodeId.value = event.node.id
}

function handleNodeLeave() {
  hoveredNodeId.value = null
}

// ─── Lifecycle ───

onMounted(() => {
  loadRelations()
})

watch(() => props.pageId, () => {
  loadRelations()
})
</script>

<style scoped>
.relation-graph-container {
  width: 100%;
  height: 600px;
  border: 1px solid #E4E7ED;
  border-radius: 8px;
  position: relative;
  background: #fafbfc;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: #606266;
}

/* ─── Node styles ─── */

.custom-node {
  background: #fff;
  border: 2px solid #DCDFE6;
  border-radius: 10px;
  min-width: 160px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: all 0.2s ease;
  cursor: pointer;
}

.custom-node:hover {
  border-color: #409EFF;
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.15);
}

.custom-node.current-node {
  border-color: #409EFF;
  border-width: 3px;
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.25);
}

.node-header {
  background: #F5F7FA;
  color: #303133;
  padding: 8px 14px;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid #EBEEF5;
}

.current-header {
  background: #409EFF;
  color: #fff;
  border-bottom-color: #409EFF;
}

.node-body {
  padding: 8px 14px;
  font-size: 12px;
  color: #606266;
}

.node-meta {
  margin-bottom: 2px;
}

.node-id {
  color: #909399;
  font-size: 11px;
}

/* ─── Edge label badges ─── */

.edge-label-badge {
  position: absolute;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 10px;
  font-weight: 500;
  white-space: nowrap;
  color: #fff;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.edge-label-relation {
  background: #409EFF;
}

.edge-label-reference {
  background: #67C23A;
}

.edge-label-quoteSelect {
  background: #E6A23C;
}

.edge-dimmed {
  opacity: 0.15;
}

/* ─── Edge animation ─── */

.edge-animated path {
  animation: dash 1s linear infinite;
}

@keyframes dash {
  to {
    stroke-dashoffset: -18;
  }
}

/* ─── Legend panel ─── */

.legend-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  background: #fff;
  border: 1px solid #E4E7ED;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 11px;
  z-index: 10;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.legend-title {
  font-weight: 600;
  margin-bottom: 6px;
  color: #303133;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.legend-item:last-child {
  margin-bottom: 0;
}

.legend-text {
  font-size: 11px;
}
</style>
