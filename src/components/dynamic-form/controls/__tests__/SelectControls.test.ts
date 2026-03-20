/**
 * SelectInput / MultiSelect 控件单元测试
 *
 * 测试三种选项来源（static / api / collection）的选项加载逻辑。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { FieldConfig } from '@/types'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
}))

import { get } from '@/utils/request'
import SelectInput from '../SelectInput.vue'
import MultiSelect from '../MultiSelect.vue'

const mockGet = vi.mocked(get)

function makeField(overrides: Partial<FieldConfig>): FieldConfig {
  return {
    id: 'f1',
    fieldName: 'status',
    label: '状态',
    controlType: 'select',
    order: 1,
    required: false,
    ...overrides,
  } as FieldConfig
}

describe('SelectInput — loadOptions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('static 类型直接使用 field.options', async () => {
    const field = makeField({
      options: [
        { label: '启用', value: 'active' },
        { label: '禁用', value: 'disabled' },
      ],
      optionsSource: { type: 'static' },
    })

    const wrapper = mount(SelectInput, {
      props: { field, modelValue: null },
    })
    await flushPromises()

    expect(mockGet).not.toHaveBeenCalled()
    // 组件正常渲染
    expect(wrapper.exists()).toBe(true)
  })

  it('api 类型从 URL 加载选项', async () => {
    mockGet.mockResolvedValueOnce([
      { name: '选项A', code: 'a' },
      { name: '选项B', code: 'b' },
    ] as any)

    const field = makeField({
      optionsSource: { type: 'api', url: '/api/options', labelField: 'name', valueField: 'code' },
    })

    mount(SelectInput, {
      props: { field, modelValue: null },
    })
    await flushPromises()

    expect(mockGet).toHaveBeenCalledWith('/api/options')
  })

  it('collection 类型从数据页加载选项', async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', caseName: '用例A' },
        { id: 'r2', caseName: '用例B' },
        { id: 'r3', caseName: '用例C' },
      ],
      total: 3,
    } as any)

    const field = makeField({
      optionsSource: {
        type: 'collection',
        collection: 'inspection-case',
        labelField: 'caseName',
        valueField: 'caseName',
      },
    })

    mount(SelectInput, {
      props: { field, modelValue: null },
    })
    await flushPromises()

    expect(mockGet).toHaveBeenCalledWith('/inspection-case', { pageSize: 10000 })
  })

  it('collection 类型 labelField/valueField 缺省时使用 id', async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', name: '记录1' },
      ],
      total: 1,
    } as any)

    const field = makeField({
      optionsSource: {
        type: 'collection',
        collection: 'items',
        // 不指定 labelField 和 valueField
      },
    })

    mount(SelectInput, {
      props: { field, modelValue: null },
    })
    await flushPromises()

    expect(mockGet).toHaveBeenCalledWith('/items', { pageSize: 10000 })
  })

  it('collection 类型加载失败时选项为空', async () => {
    mockGet.mockRejectedValueOnce(new Error('网络错误'))

    const field = makeField({
      optionsSource: {
        type: 'collection',
        collection: 'nonexistent',
        labelField: 'name',
        valueField: 'name',
      },
    })

    mount(SelectInput, {
      props: { field, modelValue: null },
    })
    await flushPromises()

    expect(mockGet).toHaveBeenCalledWith('/nonexistent', { pageSize: 10000 })
    // 不抛出错误，组件正常渲染
  })
})

describe('MultiSelect — loadOptions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('collection 类型从数据页加载选项', async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        { id: 'r1', tagName: '标签A' },
        { id: 'r2', tagName: '标签B' },
      ],
      total: 2,
    } as any)

    const field = makeField({
      controlType: 'multiSelect',
      optionsSource: {
        type: 'collection',
        collection: 'tags',
        labelField: 'tagName',
        valueField: 'tagName',
      },
    })

    mount(MultiSelect, {
      props: { field, modelValue: [] },
    })
    await flushPromises()

    expect(mockGet).toHaveBeenCalledWith('/tags', { pageSize: 10000 })
  })

  it('static 类型不发起请求', async () => {
    const field = makeField({
      controlType: 'multiSelect',
      options: [
        { label: '红', value: 'red' },
        { label: '蓝', value: 'blue' },
      ],
      optionsSource: { type: 'static' },
    })

    mount(MultiSelect, {
      props: { field, modelValue: [] },
    })
    await flushPromises()

    expect(mockGet).not.toHaveBeenCalled()
  })
})
