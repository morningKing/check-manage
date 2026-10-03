import { describe, it, expect } from 'vitest'
import { toFlowNodes, toFlowEdges, dagreLayout, STEP_STATUS_COLORS } from '../orchGraph'

describe('toFlowNodes', () => {
  it('maps step status', () => {
    const nodes = [{ id: 'a', kind: 'agent', name: 'A' }]
    const steps = [{ node_id: 'a', status: 'succeeded' }]
    const flow = toFlowNodes(nodes, steps)
    expect(flow[0].data.status).toBe('succeeded')
  })
  it('defaults to blocked when no step', () => {
    const flow = toFlowNodes([{ id: 'a', kind: 'agent', name: 'A' }], [])
    expect(flow[0].data.status).toBe('blocked')
  })
  it('keeps node label and kind', () => {
    const flow = toFlowNodes(
      [{ id: 'a', kind: 'approval', name: '审核' }],
      [{ node_id: 'a', status: 'waiting_approval' }],
    )
    expect(flow[0].data.label).toBe('审核')
    expect(flow[0].data.kind).toBe('approval')
    expect(flow[0].type).toBe('orch-step')
  })
})

describe('toFlowEdges', () => {
  it('labels conditional edges and styles reject edges', () => {
    const edges = toFlowEdges(
      [
        { source: 'a', target: 'b', kind: 'advance', condition: { op: 'gt', value: 3 } },
        { source: 'b', target: 'c', kind: 'reject' },
      ],
      [],
    )
    expect(edges[0].label).toBe('gt 3')
    expect(edges[0].style.stroke).toBe('#409eff')
    expect(edges[1].label).toBe('reject')
    expect(edges[1].style.stroke).toBe('#f56c6c')
    expect(edges[1].style.strokeDasharray).toBe('5,5')
  })
  it('animates edges whose source step is running', () => {
    const edges = toFlowEdges(
      [{ source: 'a', target: 'b', kind: 'advance' }],
      [{ node_id: 'a', status: 'running' }],
    )
    expect(edges[0].animated).toBe(true)
  })
})

describe('dagreLayout', () => {
  it('positions linear DAG top-to-bottom', () => {
    const pos = dagreLayout(
      [{ id: 'a' }, { id: 'b' }],
      [{ source: 'a', target: 'b' }],
    )
    expect(pos['a'].y).toBeLessThan(pos['b'].y)
  })
})

describe('STEP_STATUS_COLORS', () => {
  it('covers the engine step statuses', () => {
    for (const s of ['succeeded', 'failed', 'running', 'waiting_approval',
                     'blocked', 'skipped', 'needs_review']) {
      expect(STEP_STATUS_COLORS[s]).toBeTruthy()
    }
  })
})
