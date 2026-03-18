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
      Event: { BeforeSheetEditStart: 'BeforeSheetEditStart' },
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

// Mock helper
vi.mock('@/utils/univerHelper', () => ({
  buildWorkbookData: vi.fn(() => ({ sheets: {} })),
}))

// Must import after mocks
import ExcelView from '../ExcelView.vue'

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