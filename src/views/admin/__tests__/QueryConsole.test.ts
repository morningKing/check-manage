/**
 * QueryConsole 组件单元测试
 *
 * 测试数据查询控制台的AI搜索功能
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// Mock vue-router
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ path: '/' }),
  createRouter: vi.fn(() => ({
    push: vi.fn(),
    beforeEach: vi.fn(),
    afterEach: vi.fn()
  })),
  createWebHistory: vi.fn()
}))

// Mock request utils
const mockGet = vi.fn()
const mockPost = vi.fn()
vi.mock('@/utils/request', () => ({
  get: (...args: any[]) => mockGet(...args),
  post: (...args: any[]) => mockPost(...args)
}))

// Mock CodeMirror
vi.mock('vue-codemirror', () => ({
  Codemirror: {
    template: '<textarea class="cm-editor-mock" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)"></textarea>',
    props: ['modelValue', 'extensions', 'style', 'placeholder'],
    emits: ['update:modelValue', 'keydown']
  }
}))

// Mock codemirror modules
vi.mock('@codemirror/lang-json', () => ({
  json: vi.fn(() => ({})),
  jsonParseLinter: vi.fn(() => vi.fn())
}))
vi.mock('@codemirror/theme-one-dark', () => ({ oneDark: {} }))
vi.mock('@codemirror/autocomplete', () => ({ autocompletion: vi.fn() }))
vi.mock('@codemirror/lint', () => ({ linter: vi.fn() }))

// Mock xlsx
vi.mock('xlsx', () => ({
  utils: {
    aoa_to_sheet: vi.fn(),
    book_new: vi.fn(),
    book_append_sheet: vi.fn()
  },
  writeFile: vi.fn()
}))

import QueryConsole from '../QueryConsole.vue'

const mockCollections = [
  {
    collection: 'test-collection',
    name: '测试集合',
    fields: [
      { fieldName: 'name', label: '名称', controlType: 'text' },
      { fieldName: 'status', label: '状态', controlType: 'select', options: [{ label: '启用', value: 'active' }] },
      { fieldName: 'related', label: '关联', controlType: 'relation', targetCollection: 'other' }
    ]
  }
]

const mockAiSettings = {
  enabled: true,
  apiKey: '****test',
  endpoint: 'https://api.test.com',
  model: 'test-model',
  timeout: 30,
  maxTokens: 1024
}

const mockQueryResult = {
  data: [{ _id: '1', name: '测试记录' }],
  total: 1,
  columns: [{ key: 'name', label: '名称' }]
}

describe('QueryConsole - 基础功能', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockGet.mockImplementation((url: string) => {
      if (url === '/query/collections') return Promise.resolve(mockCollections)
      if (url === '/ai/settings') return Promise.resolve(mockAiSettings)
      return Promise.resolve([])
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  const basicStubs = {
    'el-select': { template: '<select><slot /></select>' },
    'el-option': { template: '<option><slot /></option>' },
    'el-button': { template: '<button><slot /></button>' },
    'el-input': { template: '<input />' },
    'el-radio-group': { template: '<div><slot /></div>' },
    'el-radio-button': { template: '<label><slot /></label>' },
    'el-icon': { template: '<span><slot /></span>' },
    'el-pagination': { template: '<div />' },
    'el-table': { template: '<table><slot /></table>' },
    'el-table-column': { template: '<td><slot /></td>' },
    'el-scrollbar': { template: '<div><slot /></div>' },
    'el-tag': { template: '<span><slot /></span>' },
    'el-empty': { template: '<div />' },
    'el-alert': { template: '<div />' },
    'el-dialog': { template: '<div><slot /></div>' },
    'el-textarea': { template: '<textarea></textarea>' },
    Codemirror: {
      template: '<textarea class="cm-editor-mock"></textarea>',
      props: ['modelValue']
    }
  }

  it('组件可正常挂载', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs: basicStubs } })
    await nextTick()
    expect(wrapper.exists()).toBe(true)
  })

  it('加载集合列表', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs: basicStubs } })
    await nextTick()
    expect(mockGet).toHaveBeenCalledWith('/query/collections')
  })
})

describe('QueryConsole - AI搜索功能', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockGet.mockImplementation((url: string) => {
      if (url === '/query/collections') return Promise.resolve(mockCollections)
      if (url === '/ai/settings') return Promise.resolve(mockAiSettings)
      return Promise.resolve([])
    })
    mockPost.mockImplementation((url: string) => {
      if (url === '/ai/query') {
        return Promise.resolve({ filter: { name: { $regex: '测试' } } })
      }
      if (url === '/query/execute') {
        return Promise.resolve(mockQueryResult)
      }
      return Promise.resolve({})
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  const stubs = {
    'el-select': { template: '<select><slot /></select>' },
    'el-option': { template: '<option><slot /></option>' },
    'el-button': { template: '<button><slot /></button>' },
    'el-input': { template: '<input />' },
    'el-radio-group': { template: '<div><slot /></div>' },
    'el-radio-button': { template: '<label><slot /></label>' },
    'el-icon': { template: '<span><slot /></span>' },
    'el-pagination': { template: '<div />' },
    'el-table': { template: '<table><slot /></table>' },
    'el-table-column': { template: '<td><slot /></td>' },
    'el-scrollbar': { template: '<div><slot /></div>' },
    'el-tag': { template: '<span><slot /></span>' },
    'el-empty': { template: '<div />' },
    'el-alert': { template: '<div />' },
    'el-dialog': { template: '<div><slot /></div>' },
    'el-textarea': { template: '<textarea></textarea>' },
    Codemirror: {
      template: '<textarea class="cm-editor-mock"></textarea>',
      props: ['modelValue']
    }
  }

  it('加载AI设置并正确设置enabled状态', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs } })
    await nextTick()

    // 等待所有异步操作完成
    await new Promise(resolve => setTimeout(resolve, 50))
    await nextTick()

    expect(mockGet).toHaveBeenCalledWith('/ai/settings')
    expect(wrapper.vm.aiEnabled).toBe(true)
  })

  it('AI禁用时切换按钮禁用', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/query/collections') return Promise.resolve(mockCollections)
      if (url === '/ai/settings') return Promise.resolve({ ...mockAiSettings, enabled: false })
      return Promise.resolve([])
    })

    const wrapper = mount(QueryConsole, { global: { stubs } })
    await nextTick()
    expect(wrapper.vm.aiEnabled).toBe(false)
  })

  it('aiQueryableFields过滤不可查询字段', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs } })
    wrapper.vm.selectedCollection = 'test-collection'
    await nextTick()

    const fields = wrapper.vm.aiQueryableFields
    expect(fields.length).toBe(2) // name 和 status，排除 relation 类型
    expect(fields.find((f: any) => f.fieldName === 'related')).toBeUndefined()
  })

  it('handleAiSearch调用AI接口', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs } })
    await nextTick()

    wrapper.vm.selectedCollection = 'test-collection'
    wrapper.vm.aiSearchText = '查找名称包含测试的记录'

    await wrapper.vm.handleAiSearch()
    await nextTick()

    expect(mockPost).toHaveBeenCalledWith('/ai/query', {
      collection: 'test-collection',
      question: '查找名称包含测试的记录'
    })
    expect(wrapper.vm.aiGeneratedFilter).toEqual({ name: { $regex: '测试' } })
  })

  it('applyGeneratedFilter切换到MongoDB模式并填充查询', async () => {
    const wrapper = mount(QueryConsole, { global: { stubs } })
    await nextTick()

    wrapper.vm.selectedCollection = 'test-collection'
    wrapper.vm.aiGeneratedFilter = { name: { $regex: '测试' } }
    wrapper.vm.showGeneratedFilter = true

    wrapper.vm.applyGeneratedFilter()

    expect(wrapper.vm.queryMode).toBe('mongodb')
    const queryObj = JSON.parse(wrapper.vm.queryText)
    expect(queryObj.collection).toBe('test-collection')
    expect(queryObj.query).toEqual({ name: { $regex: '测试' } })
  })
})