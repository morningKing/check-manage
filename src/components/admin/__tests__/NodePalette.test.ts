/**
 * NodePalette 单测（P3-editor）：agent/approval/join 三种节点类型齐全
 * （tool 已移除：后端 validate_definition 不接受，YAGNI），点击/拖拽均 emit add-node。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NodePalette from '../NodePalette.vue'

describe('NodePalette', () => {
  it('渲染 agent/approval/join 三种节点类型，无 tool', () => {
    const w = mount(NodePalette)
    const items = w.findAll('.node-palette__item')
    expect(items).toHaveLength(3)
    const kinds = items.map(i => i.attributes('data-kind'))
    expect(kinds).toEqual(['agent', 'approval', 'join'])
    expect(kinds).not.toContain('tool')
  })

  it('点击节点项 → emit add-node 携带 kind', async () => {
    const w = mount(NodePalette)
    await w.find('[data-kind="approval"]').trigger('click')
    expect(w.emitted('add-node')).toEqual([['approval']])
  })

  it('dragstart 同样 emit add-node（拖入画布路径）', async () => {
    const w = mount(NodePalette)
    await w.find('[data-kind="join"]').trigger('dragstart')
    expect(w.emitted('add-node')).toEqual([['join']])
  })
})
