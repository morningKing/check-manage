import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandPalette, { type PaletteItem } from '@/components/ai-chat/CommandPalette.vue'

const items: PaletteItem[] = [
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
  it('renders a 文件 group header for file items (F2)', () => {
    const items: PaletteItem[] = [
      { kind: 'agent', name: 'build', description: '构建助手' },
      { kind: 'file', name: 'uploads/data.csv', description: '上传文件 · 1.0 KB' },
      { kind: 'file', name: 'outputs/report.json', description: '产出 · 0.5 KB' },
    ]
    const w = mount(CommandPalette, { props: { items, activeIndex: 0, prefix: '@' } })
    const text = w.text()
    expect(text).toContain('智能体')
    expect(text).toContain('文件')
    expect(text).toContain('@uploads/data.csv')
    expect(text).toContain('@outputs/report.json')
  })
})
