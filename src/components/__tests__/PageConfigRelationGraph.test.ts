import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import PageConfigRelationGraph from '../PageConfigRelationGraph.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

vi.mock('@/api/page', () => ({
  getPageConfigRelations: vi.fn()
}))

vi.mock('@vue-flow/core', () => ({
  VueFlow: { template: '<div class="vue-flow-mock"><slot /></div>', props: ['nodes', 'edges'] },
  BaseEdge: { template: '<div />' },
  EdgeLabelRenderer: { template: '<div><slot /></div>' },
  Background: { template: '<div />' },
  Controls: { template: '<div />' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  getSmoothStepPath: () => ['M0,0', 0, 0, 0, 0],
}))

vi.mock('@vue-flow/background', () => ({
  Background: { template: '<div />' },
}))

vi.mock('@vue-flow/controls', () => ({
  Controls: { template: '<div />' },
}))

vi.mock('@vue-flow/minimap', () => ({
  MiniMap: { template: '<div />' },
}))

describe('PageConfigRelationGraph', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading state initially', async () => {
    const { getPageConfigRelations } = await import('@/api/page')

    // Make the API call hang so loading stays true
    vi.mocked(getPageConfigRelations).mockReturnValue(new Promise(() => {}))

    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-test' }
    })

    await wrapper.vm.$nextTick()

    expect(wrapper.find('.loading-state').exists()).toBe(true)
  })

  it('renders nodes after loading', async () => {
    const { getPageConfigRelations } = await import('@/api/page')

    vi.mocked(getPageConfigRelations).mockResolvedValue({
      nodes: [
        { id: 'page-a', name: '页面A', fields: 5 }
      ],
      edges: []
    })

    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-a' }
    })

    await new Promise(resolve => setTimeout(resolve, 100))

    expect(wrapper.vm.nodes.length).toBe(1)
  })
})
