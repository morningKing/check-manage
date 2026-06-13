import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, reactive } from 'vue'

// stub 子组件注册表，避免拉起真实管理页
vi.mock('../settingsComponents', () => ({
  SETTINGS_TAB_COMPONENTS: {
    users: { template: '<div class="stub-users">U</div>' },
    roles: { template: '<div class="stub-roles">R</div>' },
  },
}))

const routeMock = reactive({ meta: { categoryId: 'access' }, path: '/admin/access', query: {} as Record<string, any> })
const replaceMock = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ replace: replaceMock }),
}))

const canRef = ref<(k: string) => boolean>(() => true)
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canRef.value(k) }),
}))

import CategoryView from '../CategoryView.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

beforeEach(() => {
  routeMock.query = {}
  routeMock.meta.categoryId = 'access'
  replaceMock.mockClear()
})

const stubs = {
  'el-tabs': { template: '<div class="el-tabs"><slot /></div>' },
  'el-tab-pane': { template: '<div class="el-tab-pane" :data-name="name">{{ label }}</div>', props: ['name', 'label'] },
  'el-empty': { template: '<div class="el-empty" />' },
}

describe('CategoryView', () => {
  it('超管：access 分类渲染两个 tab，默认挂载首个(users)组件', () => {
    canRef.value = () => true
    routeMock.query = {}
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.findAll('.el-tab-pane')).toHaveLength(2)
    expect(w.find('.stub-users').exists()).toBe(true)
  })

  it('?tab=roles 时挂载 roles 组件', () => {
    canRef.value = () => true
    routeMock.query = { tab: 'roles' }
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.find('.stub-roles').exists()).toBe(true)
  })

  it('仅 admin.users 权限：只剩 1 个 tab，不渲染 tab 头，直接挂 users', () => {
    canRef.value = (k: string) => k === 'admin.users'
    routeMock.query = {}
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.find('.el-tabs').exists()).toBe(false)
    expect(w.find('.stub-users').exists()).toBe(true)
  })

  it('onTabChange: el-tabs 触发 tab-change 时 router.replace 携带 ?tab=', async () => {
    canRef.value = () => true
    const stubs2 = {
      ...stubs,
      'el-tabs': {
        template: '<div class="el-tabs"><slot /><button class="switch-btn" @click="$emit(\'tab-change\', \'roles\')">x</button></div>',
        emits: ['tab-change'],
      },
    }
    const w = mount(CategoryView, { global: { stubs: stubs2 } })
    await w.find('.switch-btn').trigger('click')
    expect(replaceMock).toHaveBeenCalledWith({ query: { tab: 'roles' } })
  })

  it('外部 URL 变更（后退键）时 activeTab 重新同步', async () => {
    canRef.value = () => true
    routeMock.query = { tab: 'roles' }
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.find('.stub-roles').exists()).toBe(true)
    routeMock.query = { tab: 'users' }
    await w.vm.$nextTick()
    expect(w.find('.stub-users').exists()).toBe(true)
  })
})
