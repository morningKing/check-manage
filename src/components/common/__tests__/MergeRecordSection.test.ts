/**
 * MergeRecordSection 组件单元测试
 *
 * 测试新增/删除记录选择区块：
 * - 渲染与条件显示
 * - 单条记录选择
 * - 全选功能
 * - 记录显示逻辑
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import MergeRecordSection from '../MergeRecordSection.vue'
import type { FieldConfig } from '@/types'

// Polyfill ResizeObserver for jsdom
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const stubs = {
  'el-collapse-item': {
    template: `<div class="el-collapse-item" v-if="$slots.default"><slot name="title" /><slot /></div>`,
    props: ['name'],
  },
  'el-checkbox': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('change', $event.target.checked)" />`,
    props: ['modelValue'],
    emits: ['change'],
  },
  'el-icon': {
    template: '<span class="el-icon"><slot /></span>',
  },
  'el-tag': {
    template: '<span class="el-tag"><slot /></span>',
    props: ['type', 'size'],
  },
  'CirclePlusFilled': { template: '<span>plus-icon</span>' },
  'RemoveFilled': { template: '<span>remove-icon</span>' },
}

function makeField(overrides: Partial<FieldConfig> = {}): FieldConfig {
  return {
    id: 'f1',
    fieldName: 'name',
    label: '名称',
    controlType: 'text',
    order: 0,
    required: false,
    ...overrides,
  }
}

describe('MergeRecordSection', () => {
  describe('渲染', () => {
    it('无记录时不渲染', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.el-collapse-item').exists()).toBe(false)
    })

    it('有记录时正确渲染', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', name: '测试记录' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.el-collapse-item').exists()).toBe(true)
      expect(wrapper.text()).toContain('新增记录')
      expect(wrapper.text()).toContain('测试记录')
    })

    it('显示正确的记录数量', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1' }, { id: '2' }, { id: '3' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('3')
    })

    it('显示已选数量', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1' }, { id: '2' }],
          selectedIds: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('已选 1')
    })
  })

  describe('记录显示逻辑', () => {
    it('使用主键字段显示记录', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', code: 'CODE001', name: '测试' }],
          selectedIds: new Set<string>(),
          fields: [makeField({ fieldName: 'code', isPrimaryKey: true }), makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('CODE001')
    })

    it('使用自动序列字段显示记录', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', seq: 'SEQ001', name: '测试' }],
          selectedIds: new Set<string>(),
          fields: [
            makeField({ fieldName: 'seq', controlType: 'autoSequence' }),
            makeField(),
          ],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('SEQ001')
    })

    it('使用第一个文本字段显示记录', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', name: '测试名称', other: '其他' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('测试名称')
    })

    it('无合适显示字段时使用 id', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: 'record-123', value: 100 }],
          selectedIds: new Set<string>(),
          fields: [makeField({ fieldName: 'value', controlType: 'number' })],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('record-123')
    })
  })

  describe('删除记录特殊显示', () => {
    it('删除记录未选择时显示"保留"标签', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'removed',
          title: '删除记录',
          name: 'removed',
          records: [{ id: '1', name: '测试' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('保留')
    })

    it('删除记录已选择时显示"删除"标签', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'removed',
          title: '删除记录',
          name: 'removed',
          records: [{ id: '1', name: '测试' }],
          selectedIds: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('删除')
    })
  })

  describe('选择功能', () => {
    it('点击记录复选框触发 toggle 事件', async () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', name: '测试' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      // 第二个 checkbox 是记录项的（第一个是全选）
      const recordCheckbox = checkboxes[1]
      await recordCheckbox.setValue(true)

      expect(wrapper.emitted('toggle')).toBeTruthy()
      expect(wrapper.emitted('toggle')![0]).toEqual(['1', true])
    })

    it('点击全选复选框触发 select-all 事件', async () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1' }, { id: '2' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      const selectAllCheckbox = checkboxes[0]
      await selectAllCheckbox.setValue(true)

      expect(wrapper.emitted('select-all')).toBeTruthy()
      expect(wrapper.emitted('select-all')![0]).toEqual([true])
    })
  })

  describe('全选状态', () => {
    it('全选时全选复选框选中', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1' }, { id: '2' }],
          selectedIds: new Set(['1', '2']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true)
    })

    it('部分选择时全选复选框未选中', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1' }, { id: '2' }],
          selectedIds: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      expect((checkboxes[0].element as HTMLInputElement).checked).toBe(false)
    })
  })

  describe('样式状态', () => {
    it('已选记录有 is-selected 类', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', name: '测试' }],
          selectedIds: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.record-item.is-selected').exists()).toBe(true)
    })

    it('未选记录无 is-selected 类', () => {
      const wrapper = mount(MergeRecordSection, {
        props: {
          type: 'added',
          title: '新增记录',
          name: 'added',
          records: [{ id: '1', name: '测试' }],
          selectedIds: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.record-item.is-selected').exists()).toBe(false)
    })
  })
})