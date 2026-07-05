/**
 * KefuManager FAQ 逻辑单元测试
 *
 * 覆盖：
 * - move(0, 1) 调用 reorderFaq 并传入交换后的 id 数组
 * - save() 在 answer 为空时不调用 createFaq（必填校验）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// Mock ElMessage before component import
vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn() },
}))

// Mock md-editor-v3 before component import
vi.mock('md-editor-v3', () => ({
  MdEditor: {
    template: '<textarea class="md-editor-mock" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
}))

// Mock the CSS import
vi.mock('md-editor-v3/lib/style.css', () => ({}))

// Mock @/api/kefu
const mockListInstances = vi.fn()
const mockListFaq = vi.fn()
const mockCreateFaq = vi.fn()
const mockUpdateFaq = vi.fn()
const mockDeleteFaq = vi.fn()
const mockReorderFaq = vi.fn()

vi.mock('@/api/kefu', () => ({
  listInstances: (...args: any[]) => mockListInstances(...args),
  listFaq: (...args: any[]) => mockListFaq(...args),
  createFaq: (...args: any[]) => mockCreateFaq(...args),
  updateFaq: (...args: any[]) => mockUpdateFaq(...args),
  deleteFaq: (...args: any[]) => mockDeleteFaq(...args),
  reorderFaq: (...args: any[]) => mockReorderFaq(...args),
}))

import KefuManager from '../KefuManager.vue'
import { ElMessage } from 'element-plus'

const stubs = {
  'el-page-header': { template: '<div />' },
  'el-select': { template: '<select><slot /></select>', props: ['modelValue'], emits: ['update:modelValue', 'change'] },
  'el-option': { template: '<option />', props: ['label', 'value'] },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', props: ['type', 'link', 'disabled'], emits: ['click'] },
  // Use a simple div so column default slots (which use scoped `row`) are NOT rendered
  'el-table': { template: '<div />', props: ['data', 'row-key'] },
  'el-table-column': { template: '<div />', props: ['prop', 'label', 'width', 'show-overflow-tooltip'] },
  'el-switch': { template: '<input type="checkbox" />', props: ['modelValue', 'model-value'], emits: ['change', 'update:modelValue'] },
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'], emits: ['update:modelValue'] },
  'el-form': { template: '<form><slot /></form>', props: ['label-width'] },
  'el-form-item': { template: '<div><slot /></div>', props: ['label'] },
  'el-input': { template: '<input />', props: ['modelValue', 'placeholder'], emits: ['update:modelValue'] },
  'el-tabs': { template: '<div><slot /></div>' },
  'el-tab-pane': { template: '<div><slot /></div>', props: ['label'] },
  MdEditor: {
    template: '<textarea class="md-editor-mock" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  KefuInstanceDialog: { template: '<div />', props: ['modelValue', 'instance'] },
  KefuTakeoverPanel: { template: '<div />', props: ['instanceId'] },
}

const sampleFaqs = [
  { id: 'faq-1', instance_id: 'inst-1', question: '问题A', answer: '答案A', category: null, sort_order: 1, click_count: 0, enabled: true },
  { id: 'faq-2', instance_id: 'inst-1', question: '问题B', answer: '答案B', category: null, sort_order: 2, click_count: 0, enabled: true },
]

describe('KefuManager - FAQ 逻辑', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListInstances.mockResolvedValue({ instances: [{ id: 'inst-1', slug: 'default', name: '默认实例', enabled: true }] })
    mockListFaq.mockResolvedValue({ items: [...sampleFaqs] })
    mockReorderFaq.mockResolvedValue({})
    mockCreateFaq.mockResolvedValue({})
    mockUpdateFaq.mockResolvedValue({})
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('onMounted 调用 listInstances', async () => {
    mount(KefuManager, { global: { stubs } })
    await nextTick()
    expect(mockListInstances).toHaveBeenCalledTimes(1)
  })

  it('move(0, 1) 调用 reorderFaq 并传入交换后的 id 顺序', async () => {
    const wrapper = mount(KefuManager, { global: { stubs } })
    await nextTick()

    // 直接设置 vm 内部状态
    const vm = wrapper.vm as any
    vm.activeIid = 'inst-1'
    vm.faqs = [...sampleFaqs]
    await nextTick()

    // 调用 move(0, 1): 将索引 0 的项与索引 1 的项交换
    await vm.move(0, 1)
    await nextTick()

    // reorderFaq 应以交换后的顺序调用：[faq-2, faq-1]
    expect(mockReorderFaq).toHaveBeenCalledWith('inst-1', ['faq-2', 'faq-1'])
  })

  it('move(1, -1) 调用 reorderFaq 并传入交换后的 id 顺序', async () => {
    const wrapper = mount(KefuManager, { global: { stubs } })
    await nextTick()

    const vm = wrapper.vm as any
    vm.activeIid = 'inst-1'
    vm.faqs = [...sampleFaqs]
    await nextTick()

    await vm.move(1, -1)
    await nextTick()

    expect(mockReorderFaq).toHaveBeenCalledWith('inst-1', ['faq-2', 'faq-1'])
  })

  it('save() 在 answer 为空时不调用 createFaq（必填校验）', async () => {
    const wrapper = mount(KefuManager, { global: { stubs } })
    await nextTick()

    const vm = wrapper.vm as any
    vm.activeIid = 'inst-1'
    vm.faqs = []
    vm.editing = null
    // 设置 question 但 answer 为空
    Object.assign(vm.form, { question: '测试问题', answer: '', category: '', enabled: true })
    await nextTick()

    await vm.save()
    await nextTick()

    expect(mockCreateFaq).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith('问题与答案必填')
  })

  it('save() 在 question 为空时不调用 createFaq（必填校验）', async () => {
    const wrapper = mount(KefuManager, { global: { stubs } })
    await nextTick()

    const vm = wrapper.vm as any
    vm.activeIid = 'inst-1'
    vm.faqs = []
    vm.editing = null
    Object.assign(vm.form, { question: '', answer: '测试答案', category: '', enabled: true })
    await nextTick()

    await vm.save()
    await nextTick()

    expect(mockCreateFaq).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith('问题与答案必填')
  })

  it('save() 在 question 和 answer 均填写时调用 createFaq', async () => {
    // mockListFaq needs to be called after createFaq resolves
    mockListFaq.mockResolvedValue({ items: [] })

    const wrapper = mount(KefuManager, { global: { stubs } })
    await nextTick()

    const vm = wrapper.vm as any
    vm.activeIid = 'inst-1'
    vm.faqs = []
    vm.editing = null
    Object.assign(vm.form, { question: '有效问题', answer: '有效答案', category: '', enabled: true })
    await nextTick()

    await vm.save()
    await nextTick()

    expect(mockCreateFaq).toHaveBeenCalledWith('inst-1', {
      question: '有效问题',
      answer: '有效答案',
      category: null,
      enabled: true,
    })
  })
})
