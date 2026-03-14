/**
 * ExcelView 组件 — 单元测试
 *
 * 测试 Excel 视图组件的核心功能。
 */
import { describe, it, expect, beforeAll, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ExcelView from '../ExcelView.vue'
import type { FieldConfig } from '@/types'

// Polyfill ResizeObserver for jsdom
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

// Mock exportToExcel
vi.mock('@/utils/excel', () => ({
  exportToExcel: vi.fn().mockResolvedValue(undefined)
}))

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

// 通用 stubs 配置
const commonStubs = {
  'el-table': true,
  'el-table-column': true,
  'el-input': true,
  'el-button': true,
  'el-icon': true,
  'el-pagination': true,
  'el-dialog': true,
  'el-select': true,
  'el-option': true,
  'el-date-picker': true,
  'el-input-number': true
}

describe('ExcelView — 组件基础', () => {
  it('组件可正常挂载', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('正确计算 visibleFields', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试', hidden: 'x' }],
        fields: [
          makeField(),
          makeField({ fieldName: 'hidden', label: '隐藏', hidden: true })
        ]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.visibleFields.length).toBe(1)
    expect(vm.visibleFields[0].fieldName).toBe('name')
  })

  it('visibleFields 按 order 排序', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', a: '1', b: '2', c: '3' }],
        fields: [
          makeField({ fieldName: 'a', label: 'A', order: 2 }),
          makeField({ fieldName: 'b', label: 'B', order: 0 }),
          makeField({ fieldName: 'c', label: 'C', order: 1 })
        ]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.visibleFields.map((f: FieldConfig) => f.fieldName)).toEqual(['b', 'c', 'a'])
  })

  it('正确计算 hasActiveFilters', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.hasActiveFilters).toBe(false)

    vm.searchText = 'test'
    expect(vm.hasActiveFilters).toBe(true)
  })

  it('hasActiveFilters 在有列筛选时为 true', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.hasActiveFilters).toBe(false)

    // 设置列筛选
    vm.columnFilters = { name: { value: 'test' } }
    expect(vm.hasActiveFilters).toBe(true)
  })
})

describe('ExcelView — 筛选判断', () => {
  it('isFilterable 正确判断可筛选类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.isFilterable({ controlType: 'text' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'textarea' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'number' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'date' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'datetime' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'autoTimestamp' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'autoSequence' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'select' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'radio' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'multiSelect' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'checkbox' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'relation' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'quoteSelect' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'reference' })).toBe(true)
    expect(vm.isFilterable({ controlType: 'file' })).toBe(false)
    expect(vm.isFilterable({ controlType: 'image' })).toBe(false)
  })

  it('isSortable 正确判断可排序类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.isSortable({ controlType: 'text' })).toBe(true)
    expect(vm.isSortable({ controlType: 'number' })).toBe(true)
    expect(vm.isSortable({ controlType: 'date' })).toBe(true)
    expect(vm.isSortable({ controlType: 'datetime' })).toBe(true)
    expect(vm.isSortable({ controlType: 'autoTimestamp' })).toBe(true)
    expect(vm.isSortable({ controlType: 'autoSequence' })).toBe(true)
    expect(vm.isSortable({ controlType: 'select' })).toBe(false)
    expect(vm.isSortable({ controlType: 'relation' })).toBe(false)
    expect(vm.isSortable({ controlType: 'textarea' })).toBe(false)
  })
})

describe('ExcelView — 列宽度', () => {
  it('getColumnWidth 返回正确的宽度', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.getColumnWidth({ controlType: 'text' })).toBe('150')
    expect(vm.getColumnWidth({ controlType: 'textarea' })).toBe('200')
    expect(vm.getColumnWidth({ controlType: 'date' })).toBe('150')
    expect(vm.getColumnWidth({ controlType: 'datetime' })).toBe('170')
    expect(vm.getColumnWidth({ controlType: 'autoTimestamp' })).toBe('170')
    expect(vm.getColumnWidth({ controlType: 'autoSequence' })).toBe('120')
    expect(vm.getColumnWidth({ controlType: 'select' })).toBe('120')
    expect(vm.getColumnWidth({ controlType: 'multiSelect' })).toBe('140')
    expect(vm.getColumnWidth({ controlType: 'reference' })).toBe('120')
    expect(vm.getColumnWidth({ controlType: 'relation' })).toBe('180')
    expect(vm.getColumnWidth({ controlType: 'quoteSelect' })).toBe('180')
    expect(vm.getColumnWidth({ controlType: 'richText' })).toBe('200')
  })
})

describe('ExcelView — 格式化值', () => {
  it('formatValue 处理空值', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.formatValue(null, { controlType: 'text' }, {})).toBe('-')
    expect(vm.formatValue(undefined, { controlType: 'text' }, {})).toBe('-')
    expect(vm.formatValue('', { controlType: 'text' }, {})).toBe('-')
  })

  it('formatValue 处理 select/radio 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = {
      controlType: 'select',
      options: [
        { label: '选项A', value: 'a' },
        { label: '选项B', value: 'b' }
      ]
    }
    expect(vm.formatValue('a', field, {})).toBe('选项A')
    expect(vm.formatValue('b', field, {})).toBe('选项B')
    expect(vm.formatValue('c', field, {})).toBe('c') // 无匹配选项返回原值
  })

  it('formatValue 处理 multiSelect/checkbox 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = {
      controlType: 'multiSelect',
      options: [
        { label: '选项A', value: 'a' },
        { label: '选项B', value: 'b' },
        { label: '选项C', value: 'c' }
      ]
    }
    expect(vm.formatValue(['a', 'b'], field, {})).toBe('选项A、选项B')
    expect(vm.formatValue(['a', 'b', 'c'], field, {})).toBe('选项A、选项B、选项C')
    expect(vm.formatValue('not-array', field, {})).toBe('not-array')
  })

  it('formatValue 处理 date 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const result = vm.formatValue('2024-03-15', { controlType: 'date' }, {})
    expect(result).toBe('2024-03-15')
  })

  it('formatValue 处理 datetime/autoTimestamp 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const result = vm.formatValue('2024-03-15T10:30:00', { controlType: 'datetime' }, {})
    expect(result).toMatch(/2024-03-15/)
    expect(result).toMatch(/10:30:00/)
  })

  it('formatValue 处理 reference 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = { controlType: 'reference', fieldName: 'ref' }
    const row = { _ref_ref_display: '关联显示值' }
    expect(vm.formatValue('ref-id', field, row)).toBe('关联显示值')
    expect(vm.formatValue('ref-id', field, {})).toBe('ref-id')
  })

  it('formatValue 处理 richText 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = { controlType: 'richText' }
    expect(vm.formatValue('<p>测试内容</p>', field, {})).toBe('测试内容')

    // 超过50字符截断
    const longHtml = '<p>' + 'a'.repeat(60) + '</p>'
    const result = vm.formatValue(longHtml, field, {})
    expect(result.length).toBeLessThanOrEqual(53) // 50 + '...'
    expect(result.endsWith('...')).toBe(true)
  })

  it('formatValue 处理 file/image 类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.formatValue([{ name: 'file1.pdf' }, { name: 'file2.pdf' }], { controlType: 'file' }, {})).toBe('2 个文件')
    expect(vm.formatValue(null, { controlType: 'file' }, {})).toBe('-')
    expect(vm.formatValue([{ name: 'img.png' }], { controlType: 'image' }, {})).toBe('1 个文件')
  })

  it('formatValue 处理默认文本类型', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.formatValue('普通文本', { controlType: 'text' }, {})).toBe('普通文本')
    expect(vm.formatValue(12345, { controlType: 'number' }, {})).toBe('12345')
  })
})

describe('ExcelView — 分页', () => {
  it('默认页面大小为100', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.pageSize).toBe(100)
  })

  it('indexMethod 计算正确序号', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentPage = 1
    expect(vm.indexMethod(0)).toBe(1)
    expect(vm.indexMethod(9)).toBe(10)

    vm.currentPage = 2
    expect(vm.indexMethod(0)).toBe(101)
    expect(vm.indexMethod(9)).toBe(110)

    vm.currentPage = 3
    expect(vm.indexMethod(50)).toBe(251)
  })

  it('handlePageSizeChange 重置页码', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentPage = 5
    vm.handlePageSizeChange()
    expect(vm.currentPage).toBe(1)
  })

  it('filteredCount 无筛选时返回全部数据', async () => {
    const data = Array.from({ length: 250 }, (_, i) => ({ id: String(i), name: `名称${i}` }))
    const wrapper = mount(ExcelView, {
      props: { data, fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    expect(vm.filteredCount).toBe(250)
  })
})

describe('ExcelView — 排序', () => {
  it('handleSortChange 正确设置排序状态', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentPage = 5

    // 升序
    vm.handleSortChange({ prop: 'name', order: 'ascending' })
    expect(vm.sortField).toBe('name')
    expect(vm.sortDirection).toBe('asc')
    expect(vm.currentPage).toBe(1) // 重置页码

    // 降序
    vm.handleSortChange({ prop: 'name', order: 'descending' })
    expect(vm.sortField).toBe('name')
    expect(vm.sortDirection).toBe('desc')

    // 取消排序
    vm.handleSortChange({ prop: 'name', order: null })
    expect(vm.sortField).toBe('')
  })

  it('sortData 正确排序数字', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const data = [
      { id: '1', num: 100 },
      { id: '2', num: 50 },
      { id: '3', num: 200 }
    ]
    const fields = [{ fieldName: 'num', controlType: 'number' }]

    const ascResult = vm.sortData(data.slice(), 'num', 'asc', fields)
    expect(ascResult.map((r: any) => r.num)).toEqual([50, 100, 200])

    const descResult = vm.sortData(data.slice(), 'num', 'desc', fields)
    expect(descResult.map((r: any) => r.num)).toEqual([200, 100, 50])
  })

  it('sortData 正确排序日期', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const data = [
      { id: '1', date: '2024-03-15' },
      { id: '2', date: '2024-01-01' },
      { id: '3', date: '2024-12-31' }
    ]
    const fields = [{ fieldName: 'date', controlType: 'date' }]

    const ascResult = vm.sortData(data.slice(), 'date', 'asc', fields)
    expect(ascResult.map((r: any) => r.date)).toEqual(['2024-01-01', '2024-03-15', '2024-12-31'])

    const descResult = vm.sortData(data.slice(), 'date', 'desc', fields)
    expect(descResult.map((r: any) => r.date)).toEqual(['2024-12-31', '2024-03-15', '2024-01-01'])
  })

  it('sortData 正确排序文本', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const data = [
      { id: '1', name: '张三' },
      { id: '2', name: '李四' },
      { id: '3', name: '王五' }
    ]
    const fields = [{ fieldName: 'name', controlType: 'text' }]

    const ascResult = vm.sortData(data.slice(), 'name', 'asc', fields)
    expect(ascResult[0].name).toBe('李四')
    expect(ascResult[2].name).toBe('张三')

    const descResult = vm.sortData(data.slice(), 'name', 'desc', fields)
    expect(descResult[0].name).toBe('张三')
    expect(descResult[2].name).toBe('李四')
  })
})

describe('ExcelView — 筛选功能', () => {
  it('openFilterDialog 设置正确的状态', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = makeField({ fieldName: 'status', label: '状态' })

    vm.openFilterDialog(field)

    expect(vm.currentFilterFieldName).toBe('status')
    expect(vm.currentFilterField.fieldName).toBe('status')
    expect(vm.currentFilterField.label).toBe('状态')
    expect(vm.filterDialogVisible).toBe(true)
  })

  it('applyCurrentFilter 添加筛选条件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentFilterFieldName = 'status'
    // filterForm 是 reactive 对象，需要设置属性
    vm.filterForm.value = 'active'
    vm.filterForm.value2 = null
    vm.filterForm.operator = 'eq'
    vm.filterDialogVisible = true
    vm.currentPage = 5

    vm.applyCurrentFilter()

    expect(vm.columnFilters.status.value).toBe('active')
    expect(vm.columnFilters.status.operator).toBe('eq')
    expect(vm.filterDialogVisible).toBe(false)
    expect(vm.currentPage).toBe(1)
  })

  it('applyCurrentFilter 清除空值筛选条件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentFilterFieldName = 'status'
    vm.columnFilters = { status: { value: 'old' } }
    vm.filterForm = { value: null, operator: 'eq' }

    vm.applyCurrentFilter()

    expect(vm.columnFilters.status).toBeUndefined()
  })

  it('clearCurrentFilter 清除当前筛选条件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.currentFilterFieldName = 'status'
    vm.columnFilters = { status: { value: 'active' }, other: { value: 'x' } }
    vm.filterDialogVisible = true

    vm.clearCurrentFilter()

    expect(vm.columnFilters.status).toBeUndefined()
    expect(vm.columnFilters.other).toBeDefined()
    expect(vm.filterDialogVisible).toBe(false)
  })

  it('clearAllFilters 清除所有筛选条件', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.searchText = 'test'
    vm.debouncedSearchText = 'test'
    vm.columnFilters = { name: { value: 'test' } }
    vm.currentPage = 5

    vm.clearAllFilters()

    expect(vm.searchText).toBe('')
    expect(vm.debouncedSearchText).toBe('')
    expect(Object.keys(vm.columnFilters).length).toBe(0)
    expect(vm.currentPage).toBe(1)
  })
})

describe('ExcelView — matchFilters 全文搜索', () => {
  it('匹配文本字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [makeField({ fieldName: 'name', controlType: 'text' })]

    expect(vm.matchFilters({ id: '1', name: '测试文本' }, '测试', true, [], false, fields)).toBe(true)
    expect(vm.matchFilters({ id: '1', name: '其他文本' }, '测试', true, [], false, fields)).toBe(false)
  })

  it('匹配 select 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{
      fieldName: 'status',
      controlType: 'select',
      options: [{ label: '进行中', value: 'active' }, { label: '已完成', value: 'done' }]
    }]

    expect(vm.matchFilters({ id: '1', status: 'active' }, '进行', true, [], false, fields)).toBe(true)
    expect(vm.matchFilters({ id: '1', status: 'done' }, '进行', true, [], false, fields)).toBe(false)
  })

  it('匹配 multiSelect 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{
      fieldName: 'tags',
      controlType: 'multiSelect',
      options: [{ label: '标签A', value: 'a' }, { label: '标签B', value: 'b' }]
    }]

    // 搜索 "标签A" - 匹配 value 'a' 对应的 label
    const result1 = vm.matchFilters({ id: '1', tags: ['a', 'b'] }, '标签a', true, [], false, fields)
    expect(result1).toBe(true)

    const result2 = vm.matchFilters({ id: '1', tags: ['b'] }, '标签a', true, [], false, fields)
    expect(result2).toBe(false)
  })

  it('匹配 relation 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'rel', controlType: 'relation' }]

    // 注意：record 需要包含 rel 字段（即使为空数组），否则 matchFilters 会跳过
    expect(vm.matchFilters({
      id: '1',
      rel: ['r1'],  // 需要有这个字段
      _rel_rel_labels: [{ id: 'r1', label: '关联记录A' }]
    }, '关联', true, [], false, fields)).toBe(true)

    expect(vm.matchFilters({
      id: '2',
      rel: ['r2'],
      _rel_rel_labels: [{ id: 'r2', label: '其他记录' }]
    }, '关联', true, [], false, fields)).toBe(false)
  })

  it('匹配 reference 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'ref', controlType: 'reference' }]

    expect(vm.matchFilters({
      id: '1',
      ref: 'ref-id',
      _ref_ref_display: '引用显示名称'
    }, '显示', true, [], false, fields)).toBe(true)

    expect(vm.matchFilters({
      id: '2',
      ref: 'ref-id',
      _ref_ref_display: '其他名称'
    }, '显示', true, [], false, fields)).toBe(false)
  })
})

describe('ExcelView — matchFilters 列筛选', () => {
  it('筛选 select 字段（精确匹配）', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'status', controlType: 'select' }]
    const filters = [['status', { value: 'active' }]]

    expect(vm.matchFilters({ id: '1', status: 'active' }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', status: 'done' }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 multiSelect 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'tags', controlType: 'multiSelect' }]
    const filters = [['tags', { value: ['a', 'b'] }]]

    expect(vm.matchFilters({ id: '1', tags: ['a', 'b', 'c'] }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', tags: ['a'] }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 number 字段（等于）', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'amount', controlType: 'number' }]
    const filters = [['amount', { value: 100, operator: 'eq' }]]

    expect(vm.matchFilters({ id: '1', amount: 100 }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', amount: 50 }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 number 字段（大于）', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'amount', controlType: 'number' }]
    const filters = [['amount', { value: 100, operator: 'gt' }]]

    expect(vm.matchFilters({ id: '1', amount: 150 }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', amount: 50 }, '', false, filters, true, fields)).toBe(false)
    expect(vm.matchFilters({ id: '3', amount: 100 }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 number 字段（介于）', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'amount', controlType: 'number' }]
    const filters = [['amount', { value: 50, value2: 100, operator: 'between' }]]

    expect(vm.matchFilters({ id: '1', amount: 75 }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', amount: 50 }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '3', amount: 100 }, '', false, filters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '4', amount: 49 }, '', false, filters, true, fields)).toBe(false)
    expect(vm.matchFilters({ id: '5', amount: 101 }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 date 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'date', controlType: 'date' }]

    // 早于
    const ltFilters = [['date', { value: '2024-06-01', operator: 'lt' }]]
    expect(vm.matchFilters({ id: '1', date: '2024-03-15' }, '', false, ltFilters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', date: '2024-07-01' }, '', false, ltFilters, true, fields)).toBe(false)

    // 晚于
    const gtFilters = [['date', { value: '2024-06-01', operator: 'gt' }]]
    expect(vm.matchFilters({ id: '1', date: '2024-07-01' }, '', false, gtFilters, true, fields)).toBe(true)
    expect(vm.matchFilters({ id: '2', date: '2024-03-15' }, '', false, gtFilters, true, fields)).toBe(false)
  })

  it('筛选 relation 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'rel', controlType: 'relation' }]
    const filters = [['rel', { value: '关联' }]]

    expect(vm.matchFilters({
      id: '1',
      _rel_rel_labels: [{ id: 'r1', label: '关联记录A' }]
    }, '', false, filters, true, fields)).toBe(true)

    expect(vm.matchFilters({
      id: '2',
      _rel_rel_labels: [{ id: 'r2', label: '其他记录' }]
    }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 quoteSelect 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'quote', controlType: 'quoteSelect' }]
    const filters = [['quote', { value: '引用' }]]

    expect(vm.matchFilters({
      id: '1',
      _quote_quote_labels: [{ id: 'q1', label: '引用项A' }]
    }, '', false, filters, true, fields)).toBe(true)

    expect(vm.matchFilters({
      id: '2',
      _quote_quote_labels: []
    }, '', false, filters, true, fields)).toBe(false)
  })

  it('筛选 reference 字段', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const fields = [{ fieldName: 'ref', controlType: 'reference' }]
    const filters = [['ref', { value: '显示' }]]

    expect(vm.matchFilters({
      id: '1',
      ref: 'ref-id',
      _ref_ref_display: '显示名称'
    }, '', false, filters, true, fields)).toBe(true)

    expect(vm.matchFilters({
      id: '2',
      ref: 'ref-id',
      _ref_ref_display: '其他名称'
    }, '', false, filters, true, fields)).toBe(false)
  })
})

describe('ExcelView — 事件', () => {
  it('handleRowClick 触发 row-click 事件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const row = { id: '1', name: '测试' }
    vm.handleRowClick(row)

    expect(wrapper.emitted('row-click')).toBeTruthy()
    expect(wrapper.emitted('row-click')![0]).toEqual([row])
  })

  it('handleRelationClick 触发 relation-click 事件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = makeField({ fieldName: 'rel', controlType: 'relation' })
    vm.handleRelationClick('r1', field)

    expect(wrapper.emitted('relation-click')).toBeTruthy()
    expect(wrapper.emitted('relation-click')![0]).toEqual(['r1', field])
  })

  it('handleReferenceClick 触发 reference-click 事件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = makeField({ fieldName: 'ref', controlType: 'reference' })
    const row = { id: '1', ref: 'r1' }
    vm.handleReferenceClick(row, field)

    expect(wrapper.emitted('reference-click')).toBeTruthy()
    expect(wrapper.emitted('reference-click')![0]).toEqual([row, field])
  })

  it('handleQuoteClick 触发 quote-click 事件', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    const field = makeField({ fieldName: 'quote', controlType: 'quoteSelect' })
    vm.handleQuoteClick('q1', field)

    expect(wrapper.emitted('quote-click')).toBeTruthy()
    expect(wrapper.emitted('quote-click')![0]).toEqual(['q1', field])
  })
})

describe('ExcelView — 导出功能', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('handleExport 导出全部数据（无筛选）', async () => {
    const { exportToExcel } = await import('@/utils/excel')

    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.handleExport()

    expect(exportToExcel).toHaveBeenCalled()
    expect(wrapper.emitted('export')).toBeTruthy()
  })

  it('handleExport 无数据时不导出', async () => {
    const { exportToExcel } = await import('@/utils/excel')

    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    const vm = wrapper.vm as any
    vm.handleExport()

    expect(exportToExcel).not.toHaveBeenCalled()
    expect(wrapper.emitted('export')).toBeFalsy()
  })
})

describe('ExcelView — 暴露方法', () => {
  it('暴露 clearAllFilters 和 tableRef', async () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    expect(typeof (wrapper.vm as any).clearAllFilters).toBe('function')
    expect((wrapper.vm as any).tableRef).toBeDefined()
  })
})

describe('ExcelView — loading 状态', () => {
  it('传递 loading prop', async () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [],
        fields: [makeField()],
        loading: true
      },
      global: { stubs: commonStubs }
    })

    expect(wrapper.props('loading')).toBe(true)
  })
})