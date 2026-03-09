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
    <div class="graph-wrapper">
      <div v-if="initLoading" v-loading="true" class="graph-loading" />
      <el-empty v-if="!initLoading && isEmpty" description="该记录暂无关联关系" />
      <div v-show="!isEmpty" class="graph-body">
        <div ref="containerRef" class="graph-container" />
        <div v-show="selectedNode" class="detail-panel">
            <div class="detail-header">
              <span class="detail-collection">{{ selectedNode?.collectionLabel }}</span>
              <el-button :icon="Close" size="small" text @click="selectedNode = null" />
            </div>
            <div class="detail-title">{{ selectedNode?.label }}</div>
            <el-scrollbar class="detail-scroll">
              <table class="detail-table">
                <tr v-for="item in selectedNodeFields" :key="item.label">
                  <td class="detail-label">{{ item.label }}</td>
                  <td class="detail-value">{{ item.value }}</td>
                </tr>
              </table>
              <div v-if="selectedNodeFields.length === 0" class="detail-empty">
                暂无详细数据
              </div>
            </el-scrollbar>
        </div>
      </div>
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
          <span class="legend-tip">单击查看详情 · 点击 ⊕ 展开关系 · 双击跳转</span>
        </div>
        <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import { Close } from '@element-plus/icons-vue'
import ForceGraph2D from 'force-graph'
// force-graph runtime is a Kapsule factory function, not a class constructor
const createGraph = ForceGraph2D as any as () => (el: HTMLElement) => any
import { getRelationGraph } from '@/api/relationGraph'
import type { GraphNode, GraphEdge } from '@/api/relationGraph'
import { usePageConfigStore } from '@/stores/pageConfig'

const props = defineProps<{
  modelValue: boolean
  collection: string
  recordId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  navigate: [collection: string, recordId: string]
}>()

const pageConfigStore = usePageConfigStore()

const containerRef = ref<HTMLElement>()
const initLoading = ref(false)
const isEmpty = ref(false)
const selectedNode = ref<GraphNode | null>(null)

const selectedNodeFields = computed(() => {
  const node = selectedNode.value
  if (!node?.data) return []

  const pageId = `page-${node.collection}`
  const fields = pageConfigStore.getPageFields(pageId)
  const result: { label: string; value: string }[] = []

  if (fields.length > 0) {
    for (const f of fields) {
      const ct = f.controlType
      // Skip internal/auto fields and relation-type fields
      if (ct === 'autoTimestamp' || ct === 'relation' || ct === 'quoteSelect') continue
      const raw = node.data[f.fieldName]
      if (raw === undefined || raw === null || raw === '') continue
      result.push({ label: f.label, value: formatValue(raw, ct) })
    }
  } else {
    // Fallback: show raw key-value pairs
    for (const [key, val] of Object.entries(node.data)) {
      if (val === undefined || val === null || val === '') continue
      result.push({ label: key, value: String(val) })
    }
  }
  return result
})

function formatValue(val: any, controlType?: string): string {
  if (Array.isArray(val)) return val.join(', ')
  if (controlType === 'switch') return val ? '是' : '否'
  return String(val)
}

let fg: any = null
let centerId = ''
const allNodes = new Map<string, GraphNode>()
const allEdges: GraphEdge[] = []
const edgeKeySet = new Set<string>()
const loadedNodeIds = new Set<string>()

const CENTER_R = 14
const NORMAL_R = 9
const EXPAND_BTN_R = 6
/** Expand button center: bottom-right edge of the node circle */
function expandBtnPos(node: any) {
  const r = node.isCenter ? CENTER_R : NORMAL_R
  const angle = Math.PI * 0.25 // 45° bottom-right
  return { x: node.x + r * Math.cos(angle), y: node.y + r * Math.sin(angle) }
}

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

  initLoading.value = true
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
      .backgroundColor('#ffffff')
      .autoPauseRedraw(false)
      .graphData({ nodes: data.nodes.map(toNode), links: data.edges.map(toLink) })
      // Node
      .nodeRelSize(1)
      .nodeVal((n: any) => n.val)
      .nodeLabel((n: any) => `[${n.collectionLabel}] ${n.label}`)
      .nodeCanvasObjectMode(() => 'replace' as any)
      .nodeCanvasObject((node: any, ctx: CanvasRenderingContext2D, gs: number) => {
        const r = node.isCenter ? CENTER_R : NORMAL_R
        // Node circle
        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
        ctx.fillStyle = node.color
        ctx.fill()
        if (node.isCenter) {
          ctx.strokeStyle = '#CC5500'
          ctx.lineWidth = 2 / gs
          ctx.stroke()
        }
        // Label
        const fs = 12 / gs
        if (fs > 2) {
          ctx.font = `${fs}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          ctx.fillStyle = '#333'
          ctx.fillText(node.short, node.x, node.y + r + 3 / gs)
        }
        // Expand "⊕" button for unexpanded nodes
        if (!loadedNodeIds.has(node.id)) {
          const btn = expandBtnPos(node)
          const br = EXPAND_BTN_R
          ctx.beginPath()
          ctx.arc(btn.x, btn.y, br, 0, Math.PI * 2)
          ctx.fillStyle = '#fff'
          ctx.fill()
          ctx.strokeStyle = node.color
          ctx.lineWidth = 1.5 / gs
          ctx.stroke()
          // "+" sign
          const s = br * 0.5
          ctx.beginPath()
          ctx.moveTo(btn.x - s, btn.y)
          ctx.lineTo(btn.x + s, btn.y)
          ctx.moveTo(btn.x, btn.y - s)
          ctx.lineTo(btn.x, btn.y + s)
          ctx.strokeStyle = node.color
          ctx.lineWidth = 1.8 / gs
          ctx.stroke()
        }
      })
      .nodePointerAreaPaint((node: any, c: string, ctx: CanvasRenderingContext2D) => {
        const r = (node.isCenter ? CENTER_R : NORMAL_R) + 3
        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
        ctx.fillStyle = c
        ctx.fill()
        // Extend hit area to cover expand button
        if (!loadedNodeIds.has(node.id)) {
          const btn = expandBtnPos(node)
          ctx.beginPath()
          ctx.arc(btn.x, btn.y, EXPAND_BTN_R + 3, 0, Math.PI * 2)
          ctx.fill()
        }
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
    initLoading.value = false
  }
}

// ── Single / double click ──
let clickTimer: ReturnType<typeof setTimeout> | null = null
let lastClickId = ''

function handleClick(node: any, event: MouseEvent) {
  // ── Check if click landed on the expand "⊕" button ──
  if (!loadedNodeIds.has(node.id) && fg) {
    const btn = expandBtnPos(node)
    const coords = fg.screen2GraphCoords(event.offsetX, event.offsetY)
    const dx = coords.x - btn.x
    const dy = coords.y - btn.y
    if (Math.sqrt(dx * dx + dy * dy) < EXPAND_BTN_R + 3) {
      expandNode(node.id)
      return
    }
  }

  // ── Double click → navigate ──
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

  // ── Single click → show detail panel only ──
  lastClickId = node.id
  clickTimer = setTimeout(() => {
    clickTimer = null
    const nd = allNodes.get(node.id)
    if (nd) selectedNode.value = nd
  }, 200)
}

async function expandNode(nodeId: string) {
  if (loadedNodeIds.has(nodeId)) return
  const nd = allNodes.get(nodeId)
  if (!nd) return
  loadedNodeIds.add(nodeId)

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
  }
}

function destroyGraph() {
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null }
  if (fg) { fg.pauseAnimation(); fg = null }
  if (containerRef.value) containerRef.value.innerHTML = ''
  selectedNode.value = null
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

.graph-loading {
  position: absolute;
  inset: 0;
  z-index: 20;
}

.graph-body {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
}

.graph-container {
  width: 100%;
  height: 100%;
}

/* ── Detail panel (absolute overlay on the right) ── */
.detail-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 300px;
  height: 100%;
  border-left: 1px solid var(--el-border-color-lighter, #e4e7ed);
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color, #fff);
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.06);
  z-index: 10;
  transition: transform 0.2s ease;
}

.detail-panel[style*="display: none"] {
  transform: translateX(100%);
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 0;
}

.detail-collection {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  background: var(--el-fill-color-light, #f5f7fa);
  padding: 2px 8px;
  border-radius: 4px;
}

.detail-title {
  font-size: 15px;
  font-weight: 600;
  padding: 8px 14px 12px;
  color: var(--el-text-color-primary, #303133);
  border-bottom: 1px solid var(--el-border-color-extra-light, #f2f6fc);
}

.detail-scroll {
  flex: 1;
  overflow: auto;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;

  tr:hover {
    background: var(--el-fill-color-lighter, #fafafa);
  }

  td {
    padding: 8px 14px;
    vertical-align: top;
    border-bottom: 1px solid var(--el-border-color-extra-light, #f2f6fc);
  }
}

.detail-label {
  color: var(--el-text-color-secondary, #909399);
  white-space: nowrap;
  width: 1%;
}

.detail-value {
  color: var(--el-text-color-primary, #303133);
  word-break: break-all;
}

.detail-empty {
  text-align: center;
  padding: 30px 14px;
  color: var(--el-text-color-secondary, #909399);
  font-size: 13px;
}

/* ── Footer ── */
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
