/**
 * 编排运行 DAG → Vue Flow 的转换工具（P3-Graph，spec §3）。
 *
 * 输入是编排定义的拓扑（nodes/edges）与 run 详情的 steps（状态），
 * 输出可直接交给 <VueFlow :nodes :edges> 的结构；坐标由 dagre 计算
 * （rankdir=TB，中心点换算为左上角）。
 */
import dagre from '@dagrejs/dagre'

export interface StepStatusColor { border: string; badge: string; label: string }

/** step 状态 → 节点边框色 / 徽标 / 中文标签（spec §3.3，与 orchestration_engine 状态枚举对齐） */
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
      position: { x: 0, y: 0 },
      data: {
        nodeId: n.id,
        label: n.name ?? n.id,
        kind: n.kind ?? 'agent',
        status,
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
      label = `${e.condition.op ?? ''} ${e.condition.value ?? ''}`.trim()
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

/** dagre 自动布局：返回 nodeId → 左上角坐标（dagre 给的是中心点） */
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
