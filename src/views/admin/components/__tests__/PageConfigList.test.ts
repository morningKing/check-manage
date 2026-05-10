/**
 * PageConfigList 组件单元测试
 *
 * 验证搜索过滤、项目筛选、排序、全选等核心交互逻辑。
 * 直接读取通过 defineExpose 暴露的内部 computed 状态，
 * 避免过度耦合 DOM 渲染细节。
 */
import { describe, it, expect, beforeEach, beforeAll, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// ResizeObserver polyfill（jsdom 缺省没有）
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

// Mock 网络请求
vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

// Mock relation API（pageConfig store 引入了它）
vi.mock('@/api/relation', () => ({
  getRecordRelations: vi.fn(),
  getCollectionRelations: vi.fn(),
  updateFieldRelations: vi.fn(),
}))

import { usePageConfigStore, useMenuStore } from '@/stores'
import PageConfigList from '../PageConfigList.vue'
import type { PageConfig, MenuItem } from '@/types'

function makeConfig(overrides: Partial<PageConfig> = {}): PageConfig {
  const id = overrides.id || 'page-test'
  return {
    id,
    name: 'Test',
    description: '',
    apiEndpoint: `/api/data/${id.replace('page-', '')}`,
    fields: [],
    createdAt: '2026-04-20T10:00:00.000Z',
    updatedAt: '2026-04-20T10:00:00.000Z',
    ...overrides,
  }
}

function makeMenu(overrides: Partial<MenuItem> = {}): MenuItem {
  return {
    id: overrides.id || 'menu-x',
    name: 'Menu',
    menuType: 'data',
    order: 1,
    ...overrides,
  } as MenuItem
}

const stubs = {
  'el-input': {
    template: `<input :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: `<select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)"><slot /></select>`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-option': { template: '<option :value="value">{{ label }}</option>', props: ['value', 'label'] },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
  'el-icon': { template: '<i><slot /></i>' },
  'el-checkbox': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('change', $event.target.checked)" />`,
    props: ['modelValue', 'indeterminate'],
    emits: ['change', 'update:modelValue'],
  },
  'el-collapse': { template: '<div><slot /></div>', props: ['modelValue'] },
  'el-collapse-item': { template: '<div><slot name="title" /><slot /></div>', props: ['name'] },
  'el-empty': { template: '<div class="empty">{{ description }}</div>', props: ['description', 'imageSize'] },
  'el-tag': { template: '<span><slot /></span>', props: ['type', 'size', 'effect'] },
  'el-tooltip': { template: '<div><slot /></div>', props: ['content', 'placement'] },
  PageConfigListItem: { template: '<div class="page-item-stub">{{ config.name }}</div>', props: ['config', 'active', 'selected', 'isOrphan'] },
  ConfirmDialog: { template: '<div></div>', props: ['modelValue'] },
}

async function setupComponent(configs: PageConfig[], menus: MenuItem[] = []) {
  setActivePinia(createPinia())
  const pageStore = usePageConfigStore()
  const menuStore = useMenuStore()
  pageStore.pageConfigs = configs
  menuStore.menuList = menus

  const wrapper = mount(PageConfigList, {
    props: { modelValue: null, configs },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

describe('PageConfigList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('搜索过滤', () => {
    const configs: PageConfig[] = [
      makeConfig({ id: 'page-a', name: '巡检案例', description: '记录巡检', apiEndpoint: '/api/data/inspection' }),
      makeConfig({ id: 'page-b', name: '设备清单', description: '管理设备', apiEndpoint: '/api/data/device' }),
      makeConfig({ id: 'page-c', name: '其他', description: '说明描述', apiEndpoint: '/api/data/misc' }),
    ]

    it('按名称匹配', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.searchKeyword = '巡检'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
      expect(vm.filteredConfigs[0].id).toBe('page-a')
    })

    it('按描述匹配', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.searchKeyword = '说明'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
      expect(vm.filteredConfigs[0].id).toBe('page-c')
    })

    it('按 API 端点匹配', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.searchKeyword = 'device'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
      expect(vm.filteredConfigs[0].id).toBe('page-b')
    })

    it('大小写不敏感', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.searchKeyword = 'INSPECTION'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
    })

    it('空关键字返回全部', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.searchKeyword = ''
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(3)
    })
  })

  describe('项目筛选', () => {
    const configs = [
      makeConfig({ id: 'page-a', name: 'A' }),
      makeConfig({ id: 'page-b', name: 'B' }),
      makeConfig({ id: 'page-c', name: '孤立' }), // 不被任何菜单引用
    ]
    const menus: MenuItem[] = [
      makeMenu({ id: 'menu-proj-1', name: 'QA项目', menuType: 'project', parentId: null }),
      makeMenu({ id: 'menu-proj-2', name: '运维项目', menuType: 'project', parentId: null }),
      makeMenu({ id: 'menu-da', menuType: 'data', pageId: 'page-a', parentId: 'menu-proj-1' }),
      makeMenu({ id: 'menu-db', menuType: 'data', pageId: 'page-b', parentId: 'menu-proj-2' }),
    ]

    it('全部项目（默认）', async () => {
      const wrapper = await setupComponent(configs, menus)
      const vm = wrapper.vm as any
      expect(vm.projectFilter).toBe('__all__')
      expect(vm.filteredConfigs).toHaveLength(3)
    })

    it('选定单个项目，仅返回该项目下的配置', async () => {
      const wrapper = await setupComponent(configs, menus)
      const vm = wrapper.vm as any
      vm.projectFilter = 'menu-proj-1'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
      expect(vm.filteredConfigs[0].id).toBe('page-a')
    })

    it('孤立分组只返回未被任何菜单引用的页面', async () => {
      const wrapper = await setupComponent(configs, menus)
      const vm = wrapper.vm as any
      vm.projectFilter = '__orphan__'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)
      expect(vm.filteredConfigs[0].id).toBe('page-c')
      expect(vm.orphanIds.has('page-c')).toBe(true)
      expect(vm.orphanIds.has('page-a')).toBe(false)
    })

    it('分组视图按项目分桶 + 孤立桶置末', async () => {
      const wrapper = await setupComponent(configs, menus)
      const vm = wrapper.vm as any
      const groups = vm.groupedConfigs
      expect(groups).toHaveLength(3)
      expect(groups[0].name).toBe('QA项目')
      expect(groups[1].name).toBe('运维项目')
      expect(groups[2].key).toBe('__orphan__')
      expect(groups[2].items[0].id).toBe('page-c')
    })
  })

  describe('排序', () => {
    const configs = [
      makeConfig({ id: 'page-1', name: 'B', updatedAt: '2026-04-20T00:00:00.000Z', createdAt: '2026-04-10T00:00:00.000Z', fields: [{ id: 'f1' } as any] }),
      makeConfig({ id: 'page-2', name: 'A', updatedAt: '2026-04-25T00:00:00.000Z', createdAt: '2026-04-22T00:00:00.000Z', fields: [{ id: 'f1' } as any, { id: 'f2' } as any, { id: 'f3' } as any] }),
      makeConfig({ id: 'page-3', name: 'C', updatedAt: '2026-04-15T00:00:00.000Z', createdAt: '2026-04-15T00:00:00.000Z', fields: [{ id: 'f1' } as any, { id: 'f2' } as any] }),
    ]

    it('默认按更新时间倒序', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      expect(vm.filteredConfigs.map((c: PageConfig) => c.id)).toEqual(['page-2', 'page-1', 'page-3'])
    })

    it('按名称升序', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.sortBy = 'nameAsc'
      await flushPromises()
      expect(vm.filteredConfigs.map((c: PageConfig) => c.name)).toEqual(['A', 'B', 'C'])
    })

    it('按字段数倒序', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.sortBy = 'fieldsDesc'
      await flushPromises()
      expect(vm.filteredConfigs.map((c: PageConfig) => c.id)).toEqual(['page-2', 'page-3', 'page-1'])
    })

    it('按创建时间倒序', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      vm.sortBy = 'createdDesc'
      await flushPromises()
      expect(vm.filteredConfigs.map((c: PageConfig) => c.id)).toEqual(['page-2', 'page-3', 'page-1'])
    })
  })

  describe('全选行为', () => {
    const configs = [
      makeConfig({ id: 'page-a', name: 'AlphaOnly', apiEndpoint: '/x/alpha' }),
      makeConfig({ id: 'page-b', name: 'Beta', apiEndpoint: '/x/beta' }),
      makeConfig({ id: 'page-c', name: 'Cee', apiEndpoint: '/x/cee' }),
    ]

    it('全选 = 当前过滤后的可见项，而不是全部', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      // 先过滤
      vm.searchKeyword = 'alpha'
      await flushPromises()
      expect(vm.filteredConfigs).toHaveLength(1)

      // 全选
      vm.handleToggleSelectAll(true)
      await flushPromises()
      expect(vm.selectedIds.size).toBe(1)
      expect(vm.selectedIds.has('page-a')).toBe(true)
      expect(vm.selectedIds.has('page-b')).toBe(false)
    })

    it('取消全选只取消可见项', async () => {
      const wrapper = await setupComponent(configs)
      const vm = wrapper.vm as any
      // 先全选
      vm.handleToggleSelectAll(true)
      await flushPromises()
      expect(vm.selectedIds.size).toBe(3)

      // 过滤后取消
      vm.searchKeyword = 'alpha'
      await flushPromises()
      vm.handleToggleSelectAll(false)
      await flushPromises()
      expect(vm.selectedIds.size).toBe(2)
      expect(vm.selectedIds.has('page-a')).toBe(false)
      expect(vm.selectedIds.has('page-b')).toBe(true)
    })
  })
})
