import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import McpServicesBlock from '@/components/ai-chat/McpServicesBlock.vue'

describe('McpServicesBlock', () => {
  it('renders servers, status and tools', () => {
    const w = mount(McpServicesBlock, {
      props: {
        servers: [
          { name: 'check-manage', status: 'connected', tools: [
            { name: 'list_collections', description: '列出集合' },
            { name: 'run_python', description: '' },
          ] },
        ],
      },
    })
    expect(w.text()).toContain('MCP 服务 (1)')
    expect(w.text()).toContain('check-manage')
    expect(w.text()).toContain('connected')
    expect(w.text()).toContain('list_collections')
    expect(w.text()).toContain('列出集合')
  })

  it('shows a fallback when there are no servers', () => {
    const w = mount(McpServicesBlock, { props: { servers: [] } })
    expect(w.text()).toContain('无法获取')
  })
})
