/**
 * 工作流图形化：把阶段 + 边推导为「节点 + 连线」。
 *
 * 纯函数，不依赖 Vue Flow / dagre / DOM，便于单测。
 *  - 有显式 edges（DAG + 条件边）→ 直接映射，advance 边标注条件（无条件=默认），reject 边标「回退」。
 *  - 无 edges → 按 stages 顺序回退线性链（与后端 _effective_edges 一致）：推进 i→i+1、回退 i→i-1。
 * 节点坐标优先用 stage.position（画布拖拽持久化），否则由 WorkflowGraph.vue 用 dagre 布局。
 */
import type { WorkflowStage, WorkflowEdge, WorkflowEdgeCondition } from '@/types/workflow'

export interface WorkflowGraphNode {
  id: string
  label: string
  collection: string
  statusField?: string
  index: number
  position?: { x: number; y: number }
}

export interface WorkflowGraphEdge {
  id: string
  source: string
  target: string
  kind: 'advance' | 'reject'
  label?: string
}

export interface WorkflowGraphModel {
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
}

function transitionLabel(t?: { from?: string; to?: string }): string | undefined {
  if (!t) return undefined
  const from = t.from || ''
  const to = t.to || ''
  if (!from && !to) return undefined
  return `${from || '∅'}→${to || '∅'}`
}

export function conditionLabel(cond?: WorkflowEdgeCondition): string | undefined {
  if (!cond || !cond.field) return undefined
  return `${cond.field} ${cond.op} ${cond.value}`
}

export function deriveWorkflowGraph(stages: WorkflowStage[], edges?: WorkflowEdge[]): WorkflowGraphModel {
  const list = stages || []
  const nodes: WorkflowGraphNode[] = list.map((s, i) => ({
    id: s.id,
    label: s.name || `阶段 ${i + 1}`,
    collection: s.collection || '',
    statusField: s.statusField,
    index: i,
    position: s.position,
  }))

  // 显式边（图形化 DAG）
  if (edges && edges.length) {
    const gedges: WorkflowGraphEdge[] = edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      kind: e.kind,
      label: e.kind === 'reject' ? '回退' : (conditionLabel(e.condition) || '默认'),
    }))
    return { nodes, edges: gedges }
  }

  // 回退线性链
  const linear: WorkflowGraphEdge[] = []
  list.forEach((s, i) => {
    if (i + 1 < list.length) {
      linear.push({
        id: `adv-${s.id}`,
        source: s.id,
        target: list[i + 1].id,
        kind: 'advance',
        label: transitionLabel(s.advanceTransition) || '推进',
      })
    }
    const rejLabel = transitionLabel(s.rejectTransition)
    if (i - 1 >= 0 && rejLabel) {
      linear.push({
        id: `rej-${s.id}`,
        source: s.id,
        target: list[i - 1].id,
        kind: 'reject',
        label: rejLabel,
      })
    }
  })
  return { nodes, edges: linear }
}
