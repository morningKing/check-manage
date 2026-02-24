/**
 * DataTable 组件 — 查看功能 单元测试
 *
 * 测试操作列中「查看」按钮的渲染和事件触发。
 */
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import DataTable from '../DataTable.vue'
import type { FieldConfig } from '@/types'

// Polyfill ResizeObserver for jsdom
beforeAll(() => {
  global.ResizeObserver = class ResizeObserver {
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
    template: '<div class="el-table-column"><slot :row="row" /></div>',
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
  }
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

  it('showActions=true 时渲染查看、编辑、删除按钮', () => {
    const wrapper = mount(DataTable, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()],
        showActions: true
      },
      global: { stubs }
    })

    const html = wrapper.html()
    expect(html).toContain('查看')
    expect(html).toContain('编辑')
    expect(html).toContain('删除')
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
