import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AutoSequence from '@/components/dynamic-form/controls/AutoSequence.vue'

describe('AutoSequence 只读控件', () => {
  it('无值时显示「保存后生成」', () => {
    const w = mount(AutoSequence, { props: { field: { fieldName: 'code' } as any, modelValue: null } })
    expect(w.text()).toBe('保存后生成')
  })
  it('有值时显示实际编号', () => {
    const w = mount(AutoSequence, { props: { field: { fieldName: 'code' } as any, modelValue: 'IC-007' } })
    expect(w.text()).toBe('IC-007')
  })
})
