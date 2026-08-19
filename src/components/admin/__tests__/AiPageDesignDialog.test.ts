import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AiPageDesignDialog from '../AiPageDesignDialog.vue'
import { usePageConfigStore, useMenuStore } from '@/stores'
import * as pageApi from '@/api/page'

vi.mock('@/api/page', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/page')>()
  return { ...actual, draftPageConfigWithAi: vi.fn() }
})

vi.mock('@/utils/request', () => ({
  get: vi.fn().mockResolvedValue([]),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

beforeAll(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const stubs = {
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
  'el-form': { template: '<div><slot /></div>' },
  'el-form-item': { template: '<div><slot /></div>' },
  'el-input': {
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue', 'size'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
    props: ['modelValue', 'size'],
    emits: ['update:modelValue'],
  },
  'el-option': { template: '<option :value="value"><slot /></option>', props: ['value', 'label'] },
  'el-checkbox': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-button': {
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
    props: ['disabled', 'loading'],
    emits: ['click'],
  },
  'el-table': { template: '<div><slot /></div>' },
  'el-table-column': { template: '<div><slot :row="{}" :$index="0" /></div>' },
  'el-alert': true,
  'el-icon': { template: '<i @click="$emit(\'click\')"><slot /></i>', emits: ['click'] },
}

const DRAFT = {
  name: '订货表',
  description: '记录客户订货信息',
  collectionSlug: 'purchase-orders',
  menuName: '订货表',
  menuPath: '/purchase-orders',
  fields: [
    { fieldName: 'customer', label: '客户', controlType: 'text', required: true },
    { fieldName: 'quantity', label: '数量', controlType: 'number', required: true },
  ],
}

function mountDialog() {
  return mount(AiPageDesignDialog, {
    props: { modelValue: true },
    global: { stubs },
  })
}

describe('AiPageDesignDialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('未选项目时「生成」按钮禁用', async () => {
    const w = mountDialog()
    await w.find('textarea, input[data-test="description"]').setValue('我要创建一张订货表')
    const btn = w.find('[data-test="generate-btn"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('选了项目 + 填了描述后可以生成，生成后进入预览阶段并展示字段表格', async () => {
    vi.mocked(pageApi.draftPageConfigWithAi).mockResolvedValue(DRAFT as any)
    const menuStore = useMenuStore()
    menuStore.menuList = [
      { id: 'menu-proj-1', name: '测试项目', menuType: 'project', order: 1, parentId: 'menu-ws-1' } as any,
    ]

    const w = mountDialog()
    await w.find('[data-test="description"]').setValue('我要创建一张订货表')
    await w.find('[data-test="project"]').setValue('menu-proj-1')
    await w.find('[data-test="generate-btn"]').trigger('click')
    await flushPromises()

    expect(pageApi.draftPageConfigWithAi).toHaveBeenCalledWith('我要创建一张订货表')
    expect((w.vm as any).phase).toBe('preview')
    expect((w.vm as any).draft.fields).toHaveLength(2)
  })

  it('字段表格支持增删行', async () => {
    vi.mocked(pageApi.draftPageConfigWithAi).mockResolvedValue(DRAFT as any)
    const menuStore = useMenuStore()
    menuStore.menuList = [
      { id: 'menu-proj-1', name: '测试项目', menuType: 'project', order: 1, parentId: 'menu-ws-1' } as any,
    ]
    const w = mountDialog()
    await w.find('[data-test="description"]').setValue('desc')
    await w.find('[data-test="project"]').setValue('menu-proj-1')
    await w.find('[data-test="generate-btn"]').trigger('click')
    await flushPromises()

    const vm = w.vm as any
    expect(vm.draft.fields).toHaveLength(2)
    vm.addFieldRow()
    expect(vm.draft.fields).toHaveLength(3)
    vm.removeFieldRow(0)
    expect(vm.draft.fields).toHaveLength(2)
  })

  it('集合标识与已有页面冲突时阻止提交', async () => {
    vi.mocked(pageApi.draftPageConfigWithAi).mockResolvedValue(DRAFT as any)
    const pageConfigStore = usePageConfigStore()
    pageConfigStore.pageConfigs = [
      { id: 'page-purchase-orders', name: '已存在', apiEndpoint: '/x', fields: [] } as any,
    ]
    const menuStore = useMenuStore()
    menuStore.menuList = [
      { id: 'menu-proj-1', name: '测试项目', menuType: 'project', order: 1, parentId: 'menu-ws-1' } as any,
    ]

    const w = mountDialog()
    await w.find('[data-test="description"]').setValue('desc')
    await w.find('[data-test="project"]').setValue('menu-proj-1')
    await w.find('[data-test="generate-btn"]').trigger('click')
    await flushPromises()

    expect((w.vm as any).slugConflict).toBe(true)
    const confirmBtn = w.find('[data-test="confirm-btn"]')
    expect((confirmBtn.element as HTMLButtonElement).disabled).toBe(true)
  })
})
