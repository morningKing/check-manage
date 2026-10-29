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
    expect(w.text()).toContain('未获取到')
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

  it('分组模式下外部自定义服务仍可见（空工具清单有兜底行，不消失）', () => {
    const w = mount(McpServicesBlock, {
      props: {
        servers: [
          { name: 'check-manage', status: 'connected', tools: [
            { name: 'query_collection', description: '查', group: 'data-query', group_name: '数据查询' },
          ] },
          { name: 'my-custom-mcp', status: 'connected', tools: [] },
        ],
      },
    })
    const text = w.text()
    expect(text).toContain('my-custom-mcp')   // 服务名必须可见
    expect(text).toContain('外部服务')         // 兜底说明行（防止看起来像消失）
    expect(text).not.toContain('无可用工具信息') // 分组模式走新兜底文案
  })
})
