import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import PageConfigRelationGraph from '../PageConfigRelationGraph.vue'

vi.mock('@/api/page', () => ({
  getPageConfigRelations: vi.fn()
}))

describe('PageConfigRelationGraph', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading state initially', () => {
    const wrapper = mount(PageConfigRelationGraph, {
      props: { pageId: 'page-test' }
    })

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