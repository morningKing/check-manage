import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuBlocksEditor from '../KefuBlocksEditor.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
const stubs = {
  'el-select': { template: `<select :value="modelValue" @change="$emit('update:modelValue',$event.target.value)"><slot/></select>`, props: ['modelValue'] },
  'el-option': { template: `<option :value="value"><slot/></option>`, props: ['value','label'] },
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  'el-button': { template: `<button @click="$emit('click')"><slot/></button>` },
  'el-switch': { template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue',$event.target.checked)" />`, props: ['modelValue'] },
  MdEditor: { template: `<textarea :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
}

function lastEmit(emitted: unknown[][]): unknown[] {
  return emitted[emitted.length - 1]
}

describe('KefuBlocksEditor', () => {
  it('addBlock appends a typed block and emits update', async () => {
    const w = mount(KefuBlocksEditor, { props: { modelValue: [] }, global: { stubs } })
    ;(w.vm as any).addBlock('links')
    const allEmitted = w.emitted('update:modelValue')!
    const emitted = lastEmit(allEmitted)[0] as any[]
    expect(emitted).toHaveLength(1)
    expect(emitted[0].type).toBe('links')
    expect(emitted[0].id).toMatch(/^blk_/)
    expect(emitted[0].enabled).toBe(true)
  })

  it('removeBlock drops by index', async () => {
    const blocks = [{ id: 'blk_a', type: 'faq', title: '', enabled: true, config: {} },
                    { id: 'blk_b', type: 'contact', title: '', enabled: true, config: {} }]
    const w = mount(KefuBlocksEditor, { props: { modelValue: blocks }, global: { stubs } })
    ;(w.vm as any).removeBlock(0)
    const allEmitted = w.emitted('update:modelValue')!
    const emitted = lastEmit(allEmitted)[0] as any[]
    expect(emitted.map((b: any) => b.id)).toEqual(['blk_b'])
  })

  it('move reorders blocks', async () => {
    const blocks = [{ id: 'blk_a', type: 'faq', title: '', enabled: true, config: {} },
                    { id: 'blk_b', type: 'contact', title: '', enabled: true, config: {} }]
    const w = mount(KefuBlocksEditor, { props: { modelValue: blocks }, global: { stubs } })
    ;(w.vm as any).move(0, 1)
    const allEmitted = w.emitted('update:modelValue')!
    const emitted = lastEmit(allEmitted)[0] as any[]
    expect(emitted.map((b: any) => b.id)).toEqual(['blk_b', 'blk_a'])
  })
})
