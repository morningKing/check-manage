<template>
  <el-dialog
    :model-value="modelValue"
    title="关系图谱"
    width="90%"
    top="5vh"
    destroy-on-close
    @update:model-value="$emit('update:modelValue', $event)"
    @opened="initGraph"
    @close="destroyGraph"
  >
    <div v-loading="loading" class="graph-wrapper">
      <el-empty v-if="!loading && isEmpty" description="该记录暂无关联关系" />
      <div v-show="!isEmpty" ref="containerRef" class="graph-container" />
    </div>
    <template #footer>
      <div class="dialog-footer">
        <div class="legend">
          <span class="legend-item">
            <span class="legend-line relation-line" />
            关联 (M:N)
          </span>
          <span class="legend-item">
            <span class="legend-line reference-line" />
            引用 (父子)
          </span>
          <span class="legend-item">
            <span class="legend-line quote-line" />
            引用选择
          </span>
          <span class="legend-tip">单击展开 · 双击跳转</span>
        </div>
        <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import ForceGraph2D from 'force-graph'
// force-graph runtime is a Kapsule factory function, not a class constructor
const createGraph = ForceGraph2D as any as () => (el: HTMLElement) => any
import { getRelationGraph } from '@/api/relationGraph'
import type { GraphNode, GraphEdge } from '@/api/relationGraph'

const props = defineProps<{
  modelValue: boolean
  collection: string
  recordId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  navigate: [collection: string, recordId: string]
}>()

const containerRef = ref<HTMLElement>()
const loading = ref(false)
const isEmpty = ref(false)

let fg: any = null
let centerId = ''
const allNodes = new Map<string, GraphNode>()
const allEdges: GraphEdge[] = []
const edgeKeySet = new Set<string>()
const loadedNodeIds = new Set<string>()

const CENTER_R = 14
const NORMAL_R = 9

const PALETTE = [
  '#5B8FF9', '#5AD8A6', '#F6BD16', '#E86452',
  '#6DC8EC', '#945FB9', '#FF9845', '#1E9493',
  '#FF99C3', '#269A99',
]
const colorMap = new Map<string, string>()
let colorIdx = 0

function getColor(col: string): string {
  if (!colorMap.has(col)) colorMap.set(col, PALETTE[colorIdx++ % PALETTE.length])
  return colorMap.get(col)!
}

function eKey(e: GraphEdge): string {
  return `${e.source}|${e.target}|${e.label}|${e.relType}`
}

function toNode(n: GraphNode) {
  const isCenter = n.id === centerId
  return {
    id: n.id,
    label: n.label,
    short: n.label.length > 10 ? n.label.slice(0, 10) + '…' : n.label,
    collection: n.collection,
    collectionLabel: n.collectionLabel,
    color: isCenter ? '#FF6A00' : getColor(n.collection),
    isCenter,
    val: (isCenter ? CENTER_R : NORMAL_R) ** 2,
  }
}

function toLink(e: GraphEdge) {
  const rel = e.relType === 'relation'
  const isRef = e.relType === 'reference'
  return {
    source: e.source,
    target: e.target,
    label: e.label,
    color: rel ? '#5B8FF9' : isRef ? '#5AD8A6' : '#F6BD16',
    dash: e.relType === 'quoteSelect' ? [5, 5] : undefined,
    arrow: !rel,
  }
}

async function initGraph() {
  if (!containerRef.value || !props.collection || !props.recordId) return

  loading.value = true
  isEmpty.value = false
  allNodes.clear()
  allEdges.length = 0
  edgeKeySet.clear()
  loadedNodeIds.clear()
  colorMap.clear()
  colorIdx = 0

  try {
    const data = await getRelationGraph(props.collection, props.recordId)
    centerId = data.centerId

    if (data.nodes.length <= 1 && data.edges.length === 0) {
      isEmpty.value = true
      return
    }

    for (const n of data.nodes) allNodes.set(n.id, n)
    for (const e of data.edges) { allEdges.push(e); edgeKeySet.add(eKey(e)) }
    loadedNodeIds.add(props.recordId)

    const el = containerRef.value
    const w = el.clientWidth || 800
    const h = el.clientHeight || 500

    fg = createGraph()(el)
      .width(w)
      .height(h)
      .backgroundColor('transparent')
      .graphData({ nodes: data.nodes.map(toNode), links: data.edges.map(toLink) })
      // Node
      .nodeRelSize(1)
      .nodeVal((n: any) => n.val)
      .nodeLabel((n: any) => `[${n.collectionLabel}] ${n.label}`)
      .nodeCanvasObjectMode(() => 'replace' as any)
      .nodeCanvasObject((node: any, ctx: CanvasRenderingContext2D, gs: number) => {
        const r = node.isCenter ? CENTER_R : NORMAL_R
        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
        ctx.fillStyle = node.color
        ctx.fill()
        if (node.isCenter) {
          ctx.strokeStyle = '#CC5500'
          ctx.lineWidth = 2 / gs
          ctx.stroke()
        }
        const fs = 12 / gs
        if (fs > 2) {
          ctx.font = `${fs}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          ctx.fillStyle = '#333'
          ctx.fillText(node.short, node.x, node.y + r + 3 / gs)
        }
      })
      .nodePointerAreaPaint((node: any, c: string, ctx: CanvasRenderingContext2D) => {
        const r = (node.isCenter ? CENTER_R : NORMAL_R) + 3
        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
        ctx.fillStyle = c
        ctx.fill()
      })
      // Link
      .linkColor((l: any) => l.color)
      .linkWidth(1.5)
      .linkLineDash((l: any) => l.dash || null)
      .linkDirectionalArrowLength((l: any) => l.arrow ? 6 : 0)
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalArrowColor((l: any) => l.color)
      .linkCanvasObjectMode(() => 'after' as any)
      .linkCanvasObject((link: any, ctx: CanvasRenderingContext2D, gs: number) => {
        if (!link.label) return
        const fs = 10 / gs
        if (fs < 3) return
        const mx = (link.source.x + link.target.x) / 2
        const my = (link.source.y + link.target.y) / 2
        ctx.font = `${fs}px sans-serif`
        const tw = ctx.measureText(link.label).width
        const p = 2 / gs
        ctx.fillStyle = 'rgba(255,255,255,0.85)'
        ctx.fillRect(mx - tw / 2 - p, my - fs / 2 - p, tw + p * 2, fs + p * 2)
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = '#999'
        ctx.fillText(link.label, mx, my)
      })
      // Interaction
      .onNodeClick(handleClick)
      // Layout: pre-run 30 ticks so first frame shows decent layout
      .cooldownTime(3000)
      .warmupTicks(30)

    fg.d3Force('charge')?.strength(-200)
    fg.d3Force('link')?.distance(140)
  } catch (err) {
    console.error('Failed to load relation graph:', err)
    isEmpty.value = true
  } finally {
    loading.value = false
  }
}

// ── Single / double click ──
let clickTimer: ReturnType<typeof setTimeout> | null = null
let lastClickId = ''

function handleClick(node: any) {
  if (clickTimer && lastClickId === node.id) {
    clearTimeout(clickTimer)
    clickTimer = null
    const nd = allNodes.get(node.id)
    if (nd) {
      emit('update:modelValue', false)
      emit('navigate', nd.collection, nd.id)
    }
    return
  }
  lastClickId = node.id
  clickTimer = setTimeout(() => { clickTimer = null; expandNode(node.id) }, 200)
}

async function expandNode(nodeId: string) {
  if (loadedNodeIds.has(nodeId)) return
  const nd = allNodes.get(nodeId)
  if (!nd) return
  loadedNodeIds.add(nodeId)
  loading.value = true

  try {
    const data = await getRelationGraph(nd.collection, nodeId)

    let changed = false
    for (const n of data.nodes) {
      if (!allNodes.has(n.id)) { allNodes.set(n.id, n); changed = true }
    }
    for (const e of data.edges) {
      const k = eKey(e)
      if (!edgeKeySet.has(k)) { edgeKeySet.add(k); allEdges.push(e); changed = true }
    }

    if (changed && fg) {
      const cur = fg.graphData().nodes as any[]
      const pos = new Map<string, { x: number; y: number }>()
      for (const n of cur) pos.set(n.id, { x: n.x, y: n.y })

      const nodes = Array.from(allNodes.values()).map((n) => {
        const fn: any = toNode(n)
        const p = pos.get(n.id)
        if (p) { fn.x = p.x; fn.y = p.y }
        return fn
      })
      fg.graphData({ nodes, links: allEdges.map(toLink) })
    }
  } catch (err) {
    console.error('Failed to expand node:', err)
  } finally {
    loading.value = false
  }
}

function destroyGraph() {
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null }
  if (fg) { fg.pauseAnimation(); fg = null }
  if (containerRef.value) containerRef.value.innerHTML = ''
  allNodes.clear()
  allEdges.length = 0
  edgeKeySet.clear()
  loadedNodeIds.clear()
}

onBeforeUnmount(() => destroyGraph())
</script>

<style scoped lang="scss">
.graph-wrapper {
  height: 65vh;
  min-height: 400px;
  position: relative;
}

.graph-container {
  width: 100%;
  height: 100%;
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.legend {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #666;
  align-items: center;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend-tip {
  color: #999;
  font-size: 12px;
  margin-left: 8px;
}

.legend-line {
  display: inline-block;
  width: 24px;
  height: 0;
  border-top: 2.5px solid;
}

.relation-line {
  border-color: #5B8FF9;
  border-style: solid;
}

.reference-line {
  border-color: #5AD8A6;
  border-style: solid;
}

.quote-line {
  border-color: #F6BD16;
  border-style: dashed;
}
</style>
