import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandPalette from '@/components/ai-chat/CommandPalette.vue'

const items = [
  { kind: 'builtin', name: 'mcps', description: '列出 MCP' },
  { kind: 'command', name: 'init', description: '建 AGENTS.md' },
  { kind: 'skill', name: 'clawhub', description: '技能' },
]

describe('CommandPalette', () => {
  it('renders grouped items and highlights activeIndex', () => {
    const w = mount(CommandPalette, { props: { items, activeIndex: 1 } })
    expect(w.text()).toContain('mcps')
    expect(w.text()).toContain('init')
    expect(w.text()).toContain('clawhub')
    expect(w.findAll('.palette-item')[1].classes()).toContain('active')
  })
  it('emits select on click', async () => {
    const w = mount(CommandPalette, { props: { items, activeIndex: 0 } })
    await w.findAll('.palette-item')[2].trigger('mousedown')
    expect(w.emitted('select')![0][0]).toMatchObject({ name: 'clawhub', kind: 'skill' })
  })
  it('renders nothing when empty', () => {
    const w = mount(CommandPalette, { props: { items: [], activeIndex: 0 } })
    expect(w.find('.command-palette').exists()).toBe(false)
  })
})
