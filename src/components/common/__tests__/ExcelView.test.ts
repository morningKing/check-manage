/**
 * ExcelView 组件 — 单元测试
 *
 * 测试基于 Univer 的 Excel 视图组件核心功能。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { FieldConfig } from '@/types'

// Mock Univer libraries (they require Canvas/Path2D unavailable in jsdom)
const mockCreateWorkbook = vi.fn()
const mockGetActiveWorkbook = vi.fn(() => null)
const mockDisposeUnit = vi.fn()
const mockAddEvent = vi.fn()

vi.mock('@univerjs/presets', () => ({
  createUniver: vi.fn(() => ({
    univer: {},
    univerAPI: {
      createWorkbook: mockCreateWorkbook,
      getActiveWorkbook: mockGetActiveWorkbook,
      disposeUnit: mockDisposeUnit,
      addEvent: mockAddEvent,
      Event: { BeforeSheetEditStart: 'BeforeSheetEditStart', BeforeSheetEditEnd: 'BeforeSheetEditEnd' },
    }
  })),
  LocaleType: { ZH_CN: 'zh-CN' },
}))

vi.mock('@univerjs/preset-sheets-core', () => ({
  UniverSheetsCorePreset: vi.fn(() => ({})),
}))

vi.mock('@univerjs/preset-sheets-filter', () => ({
  UniverSheetsFilterPreset: vi.fn(() => ({})),
}))

vi.mock('@univerjs/preset-sheets-core/lib/locales/zh-CN', () => ({ default: {} }))
vi.mock('@univerjs/preset-sheets-filter/lib/locales/zh-CN', () => ({ default: {} }))

// Mock CSS imports
vi.mock('@univerjs/preset-sheets-core/lib/index.css', () => ({}))
vi.mock('@univerjs/preset-sheets-filter/lib/index.css', () => ({}))
vi.mock('@univerjs/design/lib/index.css', () => ({}))

// Mock helper（保留实际实现——parseCellEdit / cellToTarget 等纯函数直接测真实导出，
// 仅替换组件内用到的 buildWorkbookData 以做隔离）
vi.mock('@/utils/univerHelper', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/univerHelper')>()
  return {
    ...actual,
    buildWorkbookData: vi.fn(() => ({ sheets: {} })),
  }
})

// Must import after mocks
import ExcelView from '../ExcelView.vue'
import { parseCellEdit, cellToTarget } from '@/utils/univerHelper'
import type { DynamicRecord } from '@/types'

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

const commonStubs = {
  'el-icon': { template: '<span><slot /></span>' },
}

describe('ExcelView — 组件基础', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('组件可正常挂载', () => {
    const wrapper = mount(ExcelView, {
      props: {
        data: [{ id: '1', name: '测试' }],
        fields: [makeField()]
      },
      global: { stubs: commonStubs }
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('渲染 Univer 容器', () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [] },
      global: { stubs: commonStubs }
    })
    expect(wrapper.find('.univer-container').exists()).toBe(true)
  })
})

describe('ExcelView — 暴露方法', () => {
  it('暴露 clearAllFilters 方法', () => {
    const wrapper = mount(ExcelView, {
      props: { data: [], fields: [makeField()] },
      global: { stubs: commonStubs }
    })

    expect(typeof (wrapper.vm as any).clearAllFilters).toBe('function')
  })
})

describe('ExcelView — loading 状态', () => {
  it('传递 loading prop', () => {
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

describe('parseCellEdit — 编辑文本回转为原始字段值', () => {
  it('text 字段：字符串原样通过', () => {
    const field = makeField({ controlType: 'text' })
    expect(parseCellEdit('新名称', field)).toEqual({ ok: true, value: '新名称' })
  })

  it('textarea 字段：多行字符串原样通过', () => {
    const field = makeField({ controlType: 'textarea' })
    expect(parseCellEdit('第一行\n第二行', field)).toEqual({ ok: true, value: '第一行\n第二行' })
  })

  it('number 字段：数字文本回转为 Number', () => {
    const field = makeField({ controlType: 'number', fieldName: 'qty' })
    expect(parseCellEdit('42', field)).toEqual({ ok: true, value: 42 })
  })

  it('number 字段：非数字文本拒绝', () => {
    const field = makeField({ controlType: 'number', fieldName: 'qty' })
    const result = parseCellEdit('abc', field)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.reason).toContain('数量')
  })

  it('select 字段：选项 label 回转为 value', () => {
    const field = makeField({
      controlType: 'select',
      fieldName: 'status',
      options: [
        { label: '待处理', value: 'todo' },
        { label: '进行中', value: 'doing' },
      ],
    })
    expect(parseCellEdit('进行中', field)).toEqual({ ok: true, value: 'doing' })
  })

  it('select 字段：直接输入 option value 也接受', () => {
    const field = makeField({
      controlType: 'select',
      fieldName: 'status',
      options: [{ label: '待处理', value: 'todo' }],
    })
    expect(parseCellEdit('todo', field)).toEqual({ ok: true, value: 'todo' })
  })

  it('select 字段：未知文本拒绝且不发明值', () => {
    const field = makeField({
      controlType: 'select',
      fieldName: 'status',
      options: [{ label: '待处理', value: 'todo' }],
    })
    const result = parseCellEdit('不存在的选项', field)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.reason).toContain('状态')
  })

  it('radio 字段：与 select 同样按 label→value 回转', () => {
    const field = makeField({
      controlType: 'radio',
      fieldName: 'level',
      options: [{ label: '高', value: 'high' }],
    })
    expect(parseCellEdit('高', field)).toEqual({ ok: true, value: 'high' })
  })

  it('date 字段：显示格式字符串原样通过', () => {
    const field = makeField({ controlType: 'date' })
    expect(parseCellEdit('2026-10-06', field)).toEqual({ ok: true, value: '2026-10-06' })
  })

  it('空文本：视为清空字段', () => {
    const field = makeField({ controlType: 'text' })
    expect(parseCellEdit('', field)).toEqual({ ok: true, value: '' })
  })
})

describe('cellToTarget — 单元格坐标到记录/字段的映射', () => {
  // 与 buildWorkbookData 相同的布局：第 0 行表头、第 0 列序号列，
  // 数据行 = rowIndex - 1，数据列 = colIndex - 1（可见字段按 order 排序后）
  const fields = [
    makeField({ id: 'f2', fieldName: 'qty', label: '数量', controlType: 'number', order: 2 }),
    makeField({ id: 'f1', fieldName: 'name', label: '名称', controlType: 'text', order: 1 }),
    makeField({ id: 'f3', fieldName: 'secret', label: '隐藏列', controlType: 'text', order: 3, hidden: true }),
  ] as FieldConfig[]
  const records = [
    { id: 'r1', name: '甲', qty: 1 } as unknown as DynamicRecord,
    { id: 'r2', name: '乙', qty: 2 } as unknown as DynamicRecord,
  ]

  it('第 0 行（表头）不可编辑 → null', () => {
    expect(cellToTarget(0, 1, fields, records)).toBeNull()
  })

  it('第 0 列（序号列）不可编辑 → null', () => {
    expect(cellToTarget(1, 0, fields, records)).toBeNull()
  })

  it('数据单元格映射到正确的记录与字段（可见字段按 order 排序）', () => {
    // 名称列（order=1）在第 1 数据列；第 2 数据行 → records[1]
    expect(cellToTarget(2, 1, fields, records)).toEqual({
      record: records[1],
      field: expect.objectContaining({ fieldName: 'name' }),
    })
    // 数量列（order=2）在第 2 数据列；第 1 数据行 → records[0]
    expect(cellToTarget(1, 2, fields, records)).toEqual({
      record: records[0],
      field: expect.objectContaining({ fieldName: 'qty' }),
    })
  })

  it('隐藏字段列不参与映射（列序压缩）', () => {
    // secret 被隐藏：可见列只有 name、qty → 第 3 数据列越界 → null
    expect(cellToTarget(1, 3, fields, records)).toBeNull()
  })

  it('行越界 → null', () => {
    expect(cellToTarget(3, 1, fields, records)).toBeNull()
  })
})