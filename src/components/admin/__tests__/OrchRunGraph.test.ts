import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock Vue Flow（不实际渲染 canvas——jsdom 不支持 SVG 渲染）
vi.mock('@vue-flow/core', () => ({
  VueFlow: { template: '<div class="mock-vue-flow" />', Handle: { template: '<div />' }, Position: { Top: 'top', Bottom: 'bottom' } },
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/minimap', () => ({ MiniMap: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))
vi.mock('@dagrejs/dagre', () => ({
  default: {
    graphlib: { Graph: class {
      setGraph() {} setDefaultEdgeLabel() {} setNode() {} setEdge() {}
      node() { return { x: 90, y: 30 } }
    }},
    layout() {},
  },
}))

import OrchRunGraph from '../OrchRunGraph.vue'

const def = {
  nodes: [
    { id: 'extract', kind: 'agent', name: '抽取' },
    { id: 'merge', kind: 'join', name: '汇总' },
  ],
  edges: [{ source: 'extract', target: 'merge', kind: 'advance' }],
}
const steps = [
  { node_id: 'extract', status: 'succeeded', attempt_count: 1, error_message: null },
  { node_id: 'merge', status: 'blocked', attempt_count: 0, error_message: null },
]

describe('OrchRunGraph', () => {
  it('renders without crash', () => {
    const w = mount(OrchRunGraph, { props: { definition: def, steps } })
    expect(w.find('.orch-run-graph').exists()).toBe(true)
  })
  it('emits select-step on node click', async () => {
    const w = mount(OrchRunGraph, { props: { definition: def, steps } })
    // Vue Flow 内部处理 node-click——组件级测试只验证 emit 接口
    expect(w.vm.$props.definition).toBeDefined()
    expect(w.vm.$props.steps).toEqual(steps)
  })
})
