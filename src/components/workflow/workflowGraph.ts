/**
 * 工作流图形化：把线性阶段链推导为「节点 + 连线」。
 *
 * 纯函数，不依赖 Vue Flow / dagre / DOM，便于单测。布局坐标由 WorkflowGraph.vue
 * 在渲染时用 dagre 计算。当前引擎是线性链（推进 → 下一阶段，回退 → 上一阶段），
 * 故连线规则与 server/utils/workflow_engine.py 的 idx±1 一致：
 *   - 推进边：stages[i] → stages[i+1]（相邻即连，体现流水线走向）；
 *   - 回退边：stages[i] → stages[i-1]，仅当该阶段配置了 rejectTransition。
 */
import type { WorkflowStage } from '@/types/workflow'

export interface WorkflowGraphNode {
  id: string
  label: string
  collection: string
  statusField?: string
  index: number
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

export function deriveWorkflowGraph(stages: WorkflowStage[]): WorkflowGraphModel {
  const list = stages || []
  const nodes: WorkflowGraphNode[] = list.map((s, i) => ({
    id: s.id,
    label: s.name || `阶段 ${i + 1}`,
    collection: s.collection || '',
    statusField: s.statusField,
    index: i,
  }))

  const edges: WorkflowGraphEdge[] = []
  list.forEach((s, i) => {
    // 推进边：相邻阶段间始终连线，体现链路走向；有转换则标注 from→to
    if (i + 1 < list.length) {
      edges.push({
        id: `adv-${s.id}`,
        source: s.id,
        target: list[i + 1].id,
        kind: 'advance',
        label: transitionLabel(s.advanceTransition) || '推进',
      })
    }
    // 回退边：仅当配置了 rejectTransition 且存在上一阶段
    const rejLabel = transitionLabel(s.rejectTransition)
    if (i - 1 >= 0 && rejLabel) {
      edges.push({
        id: `rej-${s.id}`,
        source: s.id,
        target: list[i - 1].id,
        kind: 'reject',
        label: rejLabel,
      })
    }
  })

  return { nodes, edges }
}
