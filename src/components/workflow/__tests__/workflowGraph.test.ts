import { describe, it, expect } from 'vitest'
import { deriveWorkflowGraph } from '../workflowGraph'
import type { WorkflowStage } from '@/types/workflow'

function stage(partial: Partial<WorkflowStage> & { id: string }): WorkflowStage {
  return { name: '', collection: '', ...partial } as WorkflowStage
}

describe('deriveWorkflowGraph', () => {
  it('一个节点对应一个阶段，带序号/数据页/状态字段', () => {
    const { nodes } = deriveWorkflowGraph([
      stage({ id: 's1', name: '初审', collection: 'req', statusField: 'status' }),
      stage({ id: 's2', name: '设计', collection: 'design' }),
    ])
    expect(nodes).toHaveLength(2)
    expect(nodes[0]).toMatchObject({ id: 's1', label: '初审', collection: 'req', statusField: 'status', index: 0 })
    expect(nodes[1]).toMatchObject({ id: 's2', label: '设计', collection: 'design', index: 1 })
  })

  it('阶段名为空时回退为「阶段 N」', () => {
    const { nodes } = deriveWorkflowGraph([stage({ id: 'a' }), stage({ id: 'b' })])
    expect(nodes[0].label).toBe('阶段 1')
    expect(nodes[1].label).toBe('阶段 2')
  })

  it('相邻阶段间生成推进边，方向 i→i+1', () => {
    const { edges } = deriveWorkflowGraph([
      stage({ id: 's1' }),
      stage({ id: 's2' }),
      stage({ id: 's3' }),
    ])
    const adv = edges.filter((e) => e.kind === 'advance')
    expect(adv).toHaveLength(2)
    expect(adv[0]).toMatchObject({ source: 's1', target: 's2', label: '推进' })
    expect(adv[1]).toMatchObject({ source: 's2', target: 's3' })
  })

  it('推进边标注 advanceTransition 的 from→to', () => {
    const { edges } = deriveWorkflowGraph([
      stage({ id: 's1', advanceTransition: { from: '待评审', to: '已通过' } }),
      stage({ id: 's2' }),
    ])
    expect(edges[0]).toMatchObject({ kind: 'advance', label: '待评审→已通过' })
  })

  it('仅当配置 rejectTransition 时生成回退边，方向 i→i-1', () => {
    const { edges } = deriveWorkflowGraph([
      stage({ id: 's1' }),
      stage({ id: 's2', rejectTransition: { from: '设计中', to: '退回' } }),
    ])
    const rej = edges.filter((e) => e.kind === 'reject')
    expect(rej).toHaveLength(1)
    expect(rej[0]).toMatchObject({ source: 's2', target: 's1', label: '设计中→退回' })
  })

  it('无 rejectTransition 时不生成回退边', () => {
    const { edges } = deriveWorkflowGraph([stage({ id: 's1' }), stage({ id: 's2' })])
    expect(edges.filter((e) => e.kind === 'reject')).toHaveLength(0)
  })

  it('首阶段即使配了 rejectTransition 也无回退边（无上一阶段）', () => {
    const { edges } = deriveWorkflowGraph([
      stage({ id: 's1', rejectTransition: { from: 'a', to: 'b' } }),
      stage({ id: 's2' }),
    ])
    expect(edges.filter((e) => e.kind === 'reject')).toHaveLength(0)
  })

  it('空/单阶段不产生边', () => {
    expect(deriveWorkflowGraph([])).toEqual({ nodes: [], edges: [] })
    expect(deriveWorkflowGraph([stage({ id: 'only' })]).edges).toHaveLength(0)
  })
})
