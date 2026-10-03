/**
 * NodePalette 单测（P3-editor）：四种节点类型齐全，点击/拖拽均 emit add-node。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NodePalette from '../NodePalette.vue'

describe('NodePalette', () => {
  it('渲染 agent/tool/approval/join 四种节点类型', () => {
    const w = mount(NodePalette)
    const items = w.findAll('.node-palette__item')
    expect(items).toHaveLength(4)
    expect(items.map(i => i.attributes('data-kind')))
      .toEqual(['agent', 'tool', 'approval', 'join'])
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
