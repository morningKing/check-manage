import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '../StatusBadge.vue'
import type { FieldConfig } from '@/types'

function makeField(): FieldConfig {
  return {
    id: 'f1',
    label: '处理状态',
    fieldName: 'status',
    controlType: 'statusBadge',
    required: false,
    order: 1,
    statusBadgeConfig: {
      options: [
        { value: 'pending', label: '待处理', icon: 'Clock', color: '#909399' },
        { value: 'processing', label: '处理中', icon: 'Loading', color: '#409EFF', animated: true },
        { value: 'done', label: '已完成', icon: 'CircleCheck', color: '#67C23A', terminal: true },
      ],
    },
  }
}

describe('StatusBadge', () => {
  it('按当前值渲染对应选项的文字和颜色', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'processing', field: makeField() },
    })
    expect(wrapper.text()).toContain('处理中')
    expect(wrapper.find('.status-badge').attributes('style')).toContain('color')
  })

  it('animated 为真时图标带旋转 class', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'processing', field: makeField() },
    })
    expect(wrapper.find('.status-badge-spin').exists()).toBe(true)
  })

  it('animated 不为真时不带旋转 class', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'done', field: makeField() },
    })
    expect(wrapper.find('.status-badge-spin').exists()).toBe(false)
  })

  it('值找不到匹配选项时回退显示原始值', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'unknown-value', field: makeField() },
    })
    expect(wrapper.text()).toContain('unknown-value')
  })
})
