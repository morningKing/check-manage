/**
 * DataTable 组件 — 查看功能 单元测试
 *
 * 测试操作列中「查看」按钮的渲染和事件触发。
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import DataTable from '../DataTable.vue'
import type { FieldConfig } from '@/types'

// Polyfill ResizeObserver for jsdom
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

// Stub Element Plus 组件避免完整渲染
const stubs = {
  'el-table': {
    template: '<div class="el-table"><slot /></div>',
    methods: { clearSelection() {} }
  },
  'el-table-column': {
    template: `<div class="el-table-column">
      <div class="column-header"><slot name="header" /></div>
      <div class="column-body"><slot :row="row" /></div>
    </div>`,
    computed: {
      row() { return (this as any).$parent?.$attrs?.data?.[0] || {} }
    }
  },
  'el-pagination': { template: '<div />' },
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    emits: ['click']
  },
  'el-tag': {
    template: '<span><slot /></span>'
  },
  'el-dropdown': {
    template: '<div class="el-dropdown"><slot /><slot name="dropdown" /></div>'
  },
  'el-dropdown-menu': {
    template: '<div class="el-dropdown-menu"><slot /></div>'
  },
  'el-dropdown-item': {
    template: '<div class="el-dropdown-item" @click="$emit(\'click\')"><slot /></div>',
    emits: ['click']
  },
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
  } as FieldConfig
}

describe('DataTable — 查看功能', () => {
  it('组件可正常挂载', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()],
        showActions: false
      },
      global: { stubs }
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('showActions=false 时仍渲染操作列（仅查看按钮可见）', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()],
        showActions: false
      },
      global: { stubs }
    })

    const html = wrapper.html()
    expect(html).toContain('查看')
    expect(html).not.toContain('编辑')
    expect(html).not.toContain('删除')
  })

  it('showActions=true 时主操作为编辑，查看/删除收进溢出菜单', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()],
        showActions: true
      },
      global: { stubs }
    })

    // 主操作按钮：编辑外露
    const buttons = wrapper.findAll('button')
    expect(buttons.some(b => b.text().includes('编辑'))).toBe(true)

    // 溢出菜单：查看 + 删除 收纳其中
    const menu = wrapper.find('.el-dropdown-menu')
    expect(menu.exists()).toBe(true)
    expect(menu.text()).toContain('查看')
    expect(menu.text()).toContain('删除')
  })

  it('点击查看按钮触发 view 事件', async () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()],
        showActions: false
      },
      global: { stubs }
    })

    const buttons = wrapper.findAll('button')
    const viewBtn = buttons.find(b => b.text().includes('查看'))
    expect(viewBtn).toBeDefined()

    await viewBtn!.trigger('click')
    expect(wrapper.emitted('view')).toBeTruthy()
  })
})

describe('DataTable — 列搜索功能', () => {
  const filterStubs = {
    ...stubs,
    'el-popover': {
      template: '<div class="el-popover"><slot /><slot name="reference" /></div>'
    },
    'el-icon': true,
    'el-input': {
      template: '<input class="el-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
      props: ['modelValue', 'placeholder']
    },
    'el-select': {
      template: '<select class="el-select" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
      props: ['modelValue', 'placeholder', 'multiple']
    },
    'el-option': {
      template: '<option :value="value">{{ label }}</option>',
      props: ['label', 'value']
    },
    'el-input-number': {
      template: '<input type="number" class="el-input-number" :value="modelValue" @input="$emit(\'update:modelValue\', Number($event.target.value))" />',
      props: ['modelValue', 'placeholder']
    },
    'el-date-picker': {
      template: '<input type="date" class="el-date-picker" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
      props: ['modelValue', 'type', 'placeholder']
    }
  }

  it('文本字段表头包含筛选相关结构', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField({ controlType: 'text' })]
      },
      global: { stubs: filterStubs }
    })

    const html = wrapper.html()
    expect(html).toContain('column-header')
    expect(html).toContain('filter-icon')
  })

  it('不可筛选字段不显示筛选图标', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', file: [] }],
        fields: [makeField({ fieldName: 'file', controlType: 'file' })]
      },
      global: { stubs: filterStubs }
    })

    const html = wrapper.html()
    expect(html).not.toContain('filter-icon')
  })

  it('isFilterable 方法正确判断可筛选类型', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [],
        fields: []
      },
      global: { stubs: filterStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.isFilterable?.({ controlType: 'text' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'number' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'date' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'select' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'multiSelect' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'file' } as FieldConfig)).toBe(false)
    expect(vm.isFilterable?.({ controlType: 'image' } as FieldConfig)).toBe(false)
    expect(vm.isFilterable?.({ controlType: 'relation' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'quoteSelect' } as FieldConfig)).toBe(true)
    expect(vm.isFilterable?.({ controlType: 'reference' } as FieldConfig)).toBe(true)
  })
})
