/**
 * MergeModifiedSection 组件单元测试
 *
 * 测试修改记录选择区块：
 * - 渲染与条件显示
 * - 记录级选择
 * - 字段级选择
 * - 展开/收起功能
 * - 全选状态
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import MergeModifiedSection from '../MergeModifiedSection.vue'
import type { FieldConfig, DiffModifiedItem } from '@/types'

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
    template: `<input type="checkbox" :checked="modelValue" :indeterminate="indeterminate" @change="$emit('change', $event.target.checked)" />`,
    props: ['modelValue', 'indeterminate'],
    emits: ['change'],
  },
  'el-button': {
    template: `<button @click="$emit('click')"><slot /></button>`,
    props: ['text', 'size'],
    emits: ['click'],
  },
  'el-tag': {
    template: '<span class="el-tag"><slot /></span>',
    props: ['type', 'size'],
  },
  'el-icon': { template: '<span><slot /></span>' },
  'el-radio-group': {
    template: `<div class="el-radio-group"><slot /></div>`,
    props: ['modelValue', 'size'],
  },
  'el-radio-button': {
    template: `<label><input type="radio" :value="value" @change="$parent.$emit('update:modelValue', value)" /><slot /></label>`,
    props: ['value'],
  },
  'EditPen': { template: '<span>edit-icon</span>' },
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

function makeModifiedItem(overrides: Partial<DiffModifiedItem> = {}): DiffModifiedItem {
  return {
    id: '1',
    record: { name: '新值' },
    oldRecord: { name: '旧值' },
    fields: [{ fieldName: 'name', oldValue: '旧值', newValue: '新值' }],
    ...overrides,
  }
}

function makeFieldDecision(recordId: string, choices: Record<string, 'source' | 'target'> = {}) {
  const fieldDecisions = new Map<string, 'source' | 'target'>()
  Object.entries(choices).forEach(([field, choice]) => {
    fieldDecisions.set(field, choice)
  })
  return { recordId, fieldDecisions }
}

describe('MergeModifiedSection', () => {
  describe('渲染', () => {
    it('无记录时不渲染', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.el-collapse-item').exists()).toBe(false)
    })

    it('有记录时正确渲染', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.el-collapse-item').exists()).toBe(true)
      expect(wrapper.text()).toContain('修改记录')
    })

    it('显示正确的记录数量', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({ id: '1' }), makeModifiedItem({ id: '2' })],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('2')
    })

    it('显示已选数量', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1', { name: 'source' }))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({ id: '1' }), makeModifiedItem({ id: '2' })],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('已选 1')
    })
  })

  describe('记录显示逻辑', () => {
    it('使用主键字段显示记录', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({
            record: { code: 'CODE001', name: '新值' },
            oldRecord: { code: 'CODE001', name: '旧值' },
          })],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField({ fieldName: 'code', isPrimaryKey: true }), makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('CODE001')
    })

    it('使用自动序列字段显示记录', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({
            record: { seq: 'SEQ001', name: '新值' },
            oldRecord: { seq: 'SEQ001', name: '旧值' },
          })],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField({ fieldName: 'seq', controlType: 'autoSequence' }), makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('SEQ001')
    })

    it('使用第一个文本字段显示记录', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({
            record: { name: '新名称' },
            oldRecord: { name: '旧名称' },
          })],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('新名称')
    })
  })

  describe('选择功能', () => {
    it('点击记录复选框触发 toggle-record 事件', async () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      const recordCheckbox = checkboxes[1] // 第一个是全选
      await recordCheckbox.setValue(true)

      expect(wrapper.emitted('toggle-record')).toBeTruthy()
      expect(wrapper.emitted('toggle-record')![0]).toEqual(['1', true])
    })

    it('点击全选复选框触发 select-all 事件', async () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({ id: '1' }), makeModifiedItem({ id: '2' })],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
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
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))
      selectedRecords.set('2', makeFieldDecision('2'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({ id: '1' }), makeModifiedItem({ id: '2' })],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true)
    })

    it('部分选择时显示 indeterminate 状态', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem({ id: '1' }), makeModifiedItem({ id: '2' })],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      expect((checkboxes[0].element as HTMLInputElement).indeterminate).toBe(true)
    })
  })

  describe('展开/收起功能', () => {
    it('未选择记录时也显示展开按钮', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('展开')
    })

    it('已选择但未展开时显示展开按钮', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('展开')
    })

    it('已选择且展开时显示收起按钮', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('收起')
    })

    it('点击展开按钮触发 toggle-expand 事件', async () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      // 找到展开按钮（第二个按钮，第一个是"全用源版本"）
      const buttons = wrapper.findAll('button')
      const expandBtn = buttons.find(b => b.text().includes('展开'))
      await expandBtn!.trigger('click')

      expect(wrapper.emitted('toggle-expand')).toBeTruthy()
      expect(wrapper.emitted('toggle-expand')![0]).toEqual(['1'])
    })
  })

  describe('字段选择表格', () => {
    const selectedRecords = new Map()
    selectedRecords.set('1', makeFieldDecision('1', { name: 'source' }))

    it('展开时显示字段表格', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.field-table').exists()).toBe(true)
    })

    it('显示所有变更字段', () => {
      const item = makeModifiedItem({
        fields: [
          { fieldName: 'name', oldValue: '旧名', newValue: '新名' },
          { fieldName: 'status', oldValue: 'inactive', newValue: 'active' },
        ],
      })

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [item],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField(), makeField({ fieldName: 'status', label: '状态' })],
        },
        global: { stubs },
      })

      // 组件显示字段的标签（label），而非字段名（fieldName）
      expect(wrapper.text()).toContain('名称') // name 字段的标签
      expect(wrapper.text()).toContain('状态') // status 字段的标签
    })

    it('显示源版本和目标版本的值', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('新值') // 源版本
      expect(wrapper.text()).toContain('旧值') // 目标版本
    })

    it('显示字段标签', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField({ label: '名称字段' })],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('名称字段')
    })

    it('自动字段显示"自动"标签', () => {
      const item = makeModifiedItem({
        fields: [
          { fieldName: 'updateTime', oldValue: '2024-01-01', newValue: '2024-01-02' },
        ],
      })

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [item],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField({ fieldName: 'updateTime', label: '更新时间' })],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('自动')
    })
  })

  describe('批量设置字段', () => {
    const selectedRecords = new Map()
    selectedRecords.set('1', makeFieldDecision('1', { name: 'source', status: 'target' }))

    it('展开时显示"全用源版本"按钮', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('全用版本数据')
    })

    it('展开时显示"全用当前数据"按钮', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('全用当前数据')
    })

    it('点击"全用版本数据"触发 set-all-fields 事件', async () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const buttons = wrapper.findAll('button')
      const sourceBtn = buttons.find(b => b.text().includes('全用版本数据'))
      await sourceBtn!.trigger('click')

      expect(wrapper.emitted('set-all-fields')).toBeTruthy()
      expect(wrapper.emitted('set-all-fields')![0]).toEqual(['1', 'source'])
    })

    it('点击"全用当前数据"触发 set-all-fields 事件', async () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set(['1']),
          fields: [makeField()],
        },
        global: { stubs },
      })

      const buttons = wrapper.findAll('button')
      const targetBtn = buttons.find(b => b.text().includes('全用当前数据'))
      await targetBtn!.trigger('click')

      expect(wrapper.emitted('set-all-fields')).toBeTruthy()
      expect(wrapper.emitted('set-all-fields')![0]).toEqual(['1', 'target'])
    })
  })

  describe('折叠摘要', () => {
    it('已选择但未展开时显示选择摘要', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1', { name: 'source', status: 'target' }))

      const item = makeModifiedItem({
        fields: [
          { fieldName: 'name', oldValue: '旧', newValue: '新' },
          { fieldName: 'status', oldValue: 'a', newValue: 'b' },
        ],
      })

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [item],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField(), makeField({ fieldName: 'status', label: '状态' })],
        },
        global: { stubs },
      })

      expect(wrapper.text()).toContain('变更: 名称、状态')
    })
  })

  describe('样式状态', () => {
    it('已选记录有 is-selected 类', () => {
      const selectedRecords = new Map()
      selectedRecords.set('1', makeFieldDecision('1'))

      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords,
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.record-item.is-selected').exists()).toBe(true)
    })

    it('未选记录无 is-selected 类', () => {
      const wrapper = mount(MergeModifiedSection, {
        props: {
          records: [makeModifiedItem()],
          selectedRecords: new Map(),
          expandedRecords: new Set<string>(),
          fields: [makeField()],
        },
        global: { stubs },
      })

      expect(wrapper.find('.record-item.is-selected').exists()).toBe(false)
    })
  })
})