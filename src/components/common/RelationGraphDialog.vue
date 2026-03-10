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
              <el-button :icon="Close" size="small" text @click="closePanel" />
            </div>
            <div class="detail-title">{{ selectedNode?.label }}</div>

            <!-- 编辑模式 -->
            <template v-if="editing">
              <el-scrollbar class="detail-scroll">
                <el-form
                  ref="editFormRef"
                  :model="editFormData"
                  label-position="top"
                  class="edit-form"
                  size="small"
                >
                  <el-form-item
                    v-for="f in editableFields"
                    :key="f.fieldName"
                    :label="f.label"
                    :prop="f.fieldName"
                  >
                    <el-input
                      v-if="f.controlType === 'textarea' || f.controlType === 'richText'"
                      v-model="editFormData[f.fieldName]"
                      type="textarea"
                      :rows="3"
                    />
                    <el-input-number
                      v-else-if="f.controlType === 'number'"
                      v-model="editFormData[f.fieldName]"
                      controls-position="right"
                      style="width: 100%"
                    />
                    <el-select
                      v-else-if="['select', 'radio'].includes(f.controlType)"
                      v-model="editFormData[f.fieldName]"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="opt in (f.options || [])"
                        :key="opt.value"
                        :label="opt.label"
                        :value="opt.value"
                      />
                    </el-select>
                    <el-select
                      v-else-if="['multiSelect', 'checkbox'].includes(f.controlType)"
                      v-model="editFormData[f.fieldName]"
                      multiple
                      style="width: 100%"
                    >
                      <el-option
                        v-for="opt in (f.options || [])"
                        :key="opt.value"
                        :label="opt.label"
                        :value="opt.value"
                      />
                    </el-select>
                    <el-input
                      v-else
                      v-model="editFormData[f.fieldName]"
                    />
                  </el-form-item>
                </el-form>
              </el-scrollbar>
              <div class="edit-actions">
                <el-button size="small" @click="editing = false">取消</el-button>
                <el-button size="small" type="primary" :loading="saving" @click="handleSave">保存</el-button>
              </div>
            </template>

            <!-- 查看模式 -->
            <template v-else>
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
            </template>
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
          <span class="legend-tip">单击查看详情 · 双击跳转</span>
        </div>
        <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onBeforeUnmount } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import ForceGraph2D from 'force-graph'
const createGraph = ForceGraph2D as any as () => (el: HTMLElement) => any
import { getRelationGraph } from '@/api/relationGraph'
import type { GraphNode, GraphEdge } from '@/api/relationGraph'
import { usePageConfigStore } from '@/stores/pageConfig'
import type { FieldConfig } from '@/types'

const props = defineProps<{
  modelValue: boolean
  collection: string
  recordId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  navigate: [collection: string, recordId: string]
  updated: []
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
      if (ct === 'autoTimestamp' || ct === 'relation' || ct === 'quoteSelect') continue
      const raw = node.data[f.fieldName]
      if (raw === undefined || raw === null || raw === '') continue
      result.push({ label: f.label, value: formatValue(raw) })
    }
  } else {
    for (const [key, val] of Object.entries(node.data)) {
      if (val === undefined || val === null || val === '') continue
      result.push({ label: key, value: String(val) })
    }
  }
  return result
})

function formatValue(val: any): string {
  if (Array.isArray(val)) return val.join(', ')
  return String(val)
}

// ── Inline editing ──

const READONLY_TYPES = new Set(['relation', 'quoteSelect', 'reference', 'autoTimestamp', 'autoSequence'])

const editing = ref(false)
const saving = ref(false)
const editFormRef = ref()
const editFormData = reactive<Record<string, any>>({})

const editableFields = computed<FieldConfig[]>(() => {
  const node = selectedNode.value
  if (!node) return []
  const pageId = `page-${node.collection}`
  return pageConfigStore.getPageFields(pageId).filter(f => !READONLY_TYPES.has(f.controlType))
})

function startEditing() {
  const node = selectedNode.value
  if (!node?.data) return
  // Populate form data from node
  for (const f of editableFields.value) {
    editFormData[f.fieldName] = node.data[f.fieldName] ?? ''
  }
  editing.value = true
}

function closePanel() {
  editing.value = false
  selectedNode.value = null
}

async function handleSave() {
  const node = selectedNode.value
  if (!node?.data) return
  const pageId = `page-${node.collection}`
  saving.value = true
  try {
    const payload: Record<string, any> = {}
    for (const f of editableFields.value) {
      payload[f.fieldName] = editFormData[f.fieldName]
    }
    // Include version for optimistic lock
    if (node.data._version !== undefined) {
      payload._version = node.data._version
    }
    await pageConfigStore.updatePageData(pageId, node.id, payload)
    // Update local node data so the detail view reflects changes
    const updated = { ...node.data, ...payload }
    if (updated._version !== undefined) updated._version++
    node.data = updated
    // Update node label if changed
    const fields = pageConfigStore.getPageFields(pageId)
    const displayField = fields.find(f => f.controlType === 'autoSequence') || fields.find(f => !READONLY_TYPES.has(f.controlType))
    if (displayField && updated[displayField.fieldName]) {
      node.label = String(updated[displayField.fieldName])
      // Refresh graph node label
      if (fg) {
        const gd = fg.graphData()
        const gn = (gd.nodes as any[]).find((n: any) => n.id === node.id)
        if (gn) {
          gn.label = node.label
          gn.short = node.label.length > 10 ? node.label.slice(0, 10) + '...' : node.label
        }
      }
    }
    editing.value = false
    ElMessage.success('保存成功')
    emit('updated')
  } catch (err: any) {
    ElMessage.error(err?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

let fg: any = null
let centerId = ''
const allNodes = new Map<string, GraphNode>()
const allEdges: GraphEdge[] = []
const edgeKeySet = new Set<string>()
const loadedNodeIds = new Set<string>()

// ── Ring menu state ──
let ringNodeId: string | null = null   // which node has the ring open (graph-space id)

const CENTER_R = 14
const NORMAL_R = 9

// ── Ring menu geometry (graph-space units) ──
const RING_GAP = 5
const RING_WIDTH = 18
const SEGMENT_GAP = 0.06          // radians gap between arcs

interface RingSeg {
  id: 'expand' | 'edit' | 'navigate'
  startAngle: number
  endAngle: number
  icon: (ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number) => void
}

function buildSegments(canExpand: boolean): RingSeg[] {
  const segs: { id: RingSeg['id']; icon: RingSeg['icon'] }[] = []

  if (canExpand) {
    // Expand icon: branching dots
    segs.push({ id: 'expand', icon: drawExpandIcon })
  }
  // Edit icon: pencil
  segs.push({ id: 'edit', icon: drawEditIcon })
  // Navigate icon: arrow
  segs.push({ id: 'navigate', icon: drawNavigateIcon })

  const count = segs.length
  const totalGap = SEGMENT_GAP * count
  const arcEach = (Math.PI * 2 - totalGap) / count

  return segs.map((s, i) => ({
    ...s,
    startAngle: -Math.PI / 2 + i * (arcEach + SEGMENT_GAP),
    endAngle: -Math.PI / 2 + i * (arcEach + SEGMENT_GAP) + arcEach,
  }))
}

// ── Icon drawers (ctx centered at icon center, s = half-size) ──

function drawExpandIcon(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number) {
  // Central dot + 3 radiating lines with dots
  ctx.beginPath()
  ctx.arc(cx, cy, s * 0.2, 0, Math.PI * 2)
  ctx.fill()
  for (let i = 0; i < 3; i++) {
    const a = -Math.PI / 2 + (i * Math.PI * 2) / 3
    const ex = cx + Math.cos(a) * s
    const ey = cy + Math.sin(a) * s
    ctx.beginPath()
    ctx.moveTo(cx, cy)
    ctx.lineTo(ex, ey)
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(ex, ey, s * 0.22, 0, Math.PI * 2)
    ctx.fill()
  }
}

function drawEditIcon(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number) {
  // Pencil icon
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(-Math.PI / 4)
  // Pencil body
  const bw = s * 0.35, bh = s * 1.1
  ctx.strokeRect(-bw, -bh, bw * 2, bh * 1.8)
  // Pencil tip
  ctx.beginPath()
  ctx.moveTo(-bw, bh * 0.8)
  ctx.lineTo(0, bh * 1.15)
  ctx.lineTo(bw, bh * 0.8)
  ctx.closePath()
  ctx.fill()
  ctx.restore()
}

function drawNavigateIcon(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number) {
  // Arrow pointing right
  ctx.beginPath()
  ctx.moveTo(cx - s * 0.6, cy)
  ctx.lineTo(cx + s * 0.3, cy)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(cx + s * 0.3, cy - s * 0.5)
  ctx.lineTo(cx + s * 0.85, cy)
  ctx.lineTo(cx + s * 0.3, cy + s * 0.5)
  ctx.closePath()
  ctx.fill()
}

// ── Colors ──

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
    short: n.label.length > 10 ? n.label.slice(0, 10) + '...' : n.label,
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

// ── Draw ring menu on canvas ──

function drawRingMenu(node: any, ctx: CanvasRenderingContext2D, gs: number) {
  const r = node.isCenter ? CENTER_R : NORMAL_R
  const inner = r + RING_GAP
  const outer = inner + RING_WIDTH
  const canExpand = !loadedNodeIds.has(node.id)
  const segs = buildSegments(canExpand)

  // Glow
  ctx.save()
  ctx.shadowColor = 'rgba(255, 210, 80, 0.7)'
  ctx.shadowBlur = 18 / gs
  ctx.beginPath()
  ctx.arc(node.x, node.y, r + 2, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(255, 210, 80, 0.15)'
  ctx.fill()
  ctx.restore()

  // Glow ring border
  ctx.beginPath()
  ctx.arc(node.x, node.y, outer + 1, 0, Math.PI * 2)
  ctx.strokeStyle = 'rgba(255, 210, 80, 0.35)'
  ctx.lineWidth = 1.5 / gs
  ctx.stroke()

  for (const seg of segs) {
    // Arc segment background
    ctx.beginPath()
    ctx.arc(node.x, node.y, outer, seg.startAngle, seg.endAngle)
    ctx.arc(node.x, node.y, inner, seg.endAngle, seg.startAngle, true)
    ctx.closePath()
    ctx.fillStyle = 'rgba(240, 240, 240, 0.92)'
    ctx.fill()
    ctx.strokeStyle = 'rgba(200, 200, 200, 0.6)'
    ctx.lineWidth = 0.8 / gs
    ctx.stroke()

    // Icon at center of arc
    const midAngle = (seg.startAngle + seg.endAngle) / 2
    const midR = (inner + outer) / 2
    const ix = node.x + Math.cos(midAngle) * midR
    const iy = node.y + Math.sin(midAngle) * midR
    const iconSize = RING_WIDTH * 0.32

    ctx.fillStyle = '#555'
    ctx.strokeStyle = '#555'
    ctx.lineWidth = 1.2 / gs
    seg.icon(ctx, ix, iy, iconSize)
  }
}

// ── Hit-test ring segment ──

function hitTestRing(node: any, gx: number, gy: number): RingSeg | null {
  const r = node.isCenter ? CENTER_R : NORMAL_R
  const inner = r + RING_GAP
  const outer = inner + RING_WIDTH
  const dx = gx - node.x
  const dy = gy - node.y
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist < inner || dist > outer + 2) return null

  let angle = Math.atan2(dy, dx)
  // Normalize to same range as segments
  const canExpand = !loadedNodeIds.has(node.id)
  const segs = buildSegments(canExpand)
  for (const seg of segs) {
    // Normalize angle comparison
    let a = angle
    if (a < seg.startAngle - 0.01) a += Math.PI * 2
    if (a >= seg.startAngle - 0.01 && a <= seg.endAngle + 0.01) return seg
  }
  return null
}

// ── Init graph ──

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
  ringNodeId = null

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
      .nodeLabel(() => '')  // disable default tooltip, we use ring menu
      .nodeCanvasObjectMode(() => 'replace' as any)
      .nodeCanvasObject((node: any, ctx: CanvasRenderingContext2D, gs: number) => {
        const r = node.isCenter ? CENTER_R : NORMAL_R
        const isRingNode = ringNodeId === node.id

        // Glow effect for ring-selected node
        if (isRingNode) {
          ctx.save()
          ctx.shadowColor = 'rgba(255, 210, 80, 0.6)'
          ctx.shadowBlur = 20 / gs
          ctx.beginPath()
          ctx.arc(node.x, node.y, r + 1, 0, Math.PI * 2)
          ctx.fillStyle = 'rgba(255, 210, 80, 0.08)'
          ctx.fill()
          ctx.restore()
        }

        // Node circle
        ctx.beginPath()
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
        ctx.fillStyle = node.color
        ctx.fill()
        if (node.isCenter || isRingNode) {
          ctx.strokeStyle = isRingNode ? 'rgba(255, 200, 50, 0.8)' : '#CC5500'
          ctx.lineWidth = 2 / gs
          ctx.stroke()
        }

        // Node label inside circle (if fits)
        const labelFs = (r * 1.2) / gs
        if (labelFs > 2.5) {
          ctx.font = `bold ${Math.min(labelFs, 11 / gs)}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          ctx.fillStyle = '#fff'
          const txt = node.label.length > 4 ? node.label.slice(0, 4) : node.label
          ctx.fillText(txt, node.x, node.y)
        }

        // Label below node
        const fs = 12 / gs
        if (fs > 2) {
          ctx.font = `${fs}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          ctx.fillStyle = '#333'
          ctx.fillText(node.short, node.x, node.y + r + 3 / gs)
        }

        // Ring menu
        if (isRingNode) {
          drawRingMenu(node, ctx, gs)
        }
      })
      .nodePointerAreaPaint((node: any, c: string, ctx: CanvasRenderingContext2D) => {
        // Hit area covers node + ring menu if active
        const r = (node.isCenter ? CENTER_R : NORMAL_R)
        if (ringNodeId === node.id) {
          const outer = r + RING_GAP + RING_WIDTH + 4
          ctx.beginPath()
          ctx.arc(node.x, node.y, outer, 0, Math.PI * 2)
          ctx.fillStyle = c
          ctx.fill()
        } else {
          ctx.beginPath()
          ctx.arc(node.x, node.y, r + 3, 0, Math.PI * 2)
          ctx.fillStyle = c
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
      .onNodeClick(handleNodeClick)
      .onBackgroundClick(handleBgClick)
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

// ── Click handling ──

let clickTimer: ReturnType<typeof setTimeout> | null = null
let lastClickId = ''

function handleNodeClick(node: any, event: MouseEvent) {
  // ── If this node has ring menu open, hit-test the ring segments ──
  if (ringNodeId === node.id && fg) {
    const coords = fg.screen2GraphCoords(event.offsetX, event.offsetY)
    const seg = hitTestRing(node, coords.x, coords.y)
    if (seg) {
      ringNodeId = null
      if (seg.id === 'expand') {
        expandNode(node.id)
      } else if (seg.id === 'edit') {
        const nd = allNodes.get(node.id)
        if (nd) {
          selectedNode.value = nd
          startEditing()
        }
      } else if (seg.id === 'navigate') {
        const nd = allNodes.get(node.id)
        if (nd) {
          emit('update:modelValue', false)
          emit('navigate', nd.collection, nd.id)
        }
      }
      return
    }
    // Clicked node body while ring is open → close ring
    ringNodeId = null
    return
  }

  // ── Double click detection → navigate ──
  if (clickTimer && lastClickId === node.id) {
    clearTimeout(clickTimer)
    clickTimer = null
    ringNodeId = null
    const nd = allNodes.get(node.id)
    if (nd) {
      emit('update:modelValue', false)
      emit('navigate', nd.collection, nd.id)
    }
    return
  }

  // ── Single click → show detail panel + open ring menu ──
  lastClickId = node.id
  const nd = allNodes.get(node.id)
  if (nd) selectedNode.value = nd
  clickTimer = setTimeout(() => {
    clickTimer = null
    ringNodeId = node.id
  }, 200)
}

function handleBgClick() {
  if (ringNodeId) {
    ringNodeId = null
  }
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
  editing.value = false
  ringNodeId = null
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

.edit-form {
  padding: 12px 14px;

  :deep(.el-form-item) {
    margin-bottom: 12px;
  }

  :deep(.el-form-item__label) {
    font-size: 12px;
    color: var(--el-text-color-secondary, #909399);
    padding-bottom: 2px;
  }
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 14px;
  border-top: 1px solid var(--el-border-color-extra-light, #f2f6fc);
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
