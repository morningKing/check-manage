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

  it('按 group_name 分组渲染，缺失归「其他」', () => {
    const w = mount(McpServicesBlock, {
      props: {
        servers: [
          { name: 'check-manage', status: 'connected', tools: [
            { name: 'query_collection', description: '查', group: 'data-query', group_name: '数据查询' },
            { name: 'run_python', description: '跑', group: 'exec', group_name: '通用执行与产物' },
            { name: 'mystery_tool', description: '无组' },
          ] },
        ],
      },
    })
    const text = w.text()
    expect(text).toContain('数据查询')
    expect(text).toContain('通用执行与产物')
    expect(text).toContain('其他')            // 无 group_name 的工具归「其他」
    expect(text).toContain('mystery_tool')
  })
})
