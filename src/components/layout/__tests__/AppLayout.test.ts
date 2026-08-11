import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// AppLayout 头部按钮/外观弹层用到 el-popover / el-dropdown 等，依赖 ResizeObserver
// （见 CLAUDE.md「ResizeObserver Polyfill」）。
beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never
})

// AppLayout 头部本身就静态引用了 '@/components/common' 桶文件（NotificationBell /
// CommandPalette）。这个桶文件间接拉入体量很大的 ExcelView.vue（Univer.js），会让
// 本文件跟着变慢变抖 —— 与 settingsHubRoutes.test.ts 同样的问题、同样的解法：只
// stub 掉，不改组件内部逻辑（纯测试环境隔离）。
vi.mock('@/components/common', () => {
  const Stub = { template: '<div />' }
  return { NotificationBell: Stub, CommandPalette: Stub }
})

let currentRoute: { path: string; meta: Record<string, unknown> } = {
  path: '/home',
  meta: {},
}
const afterEachMock = vi.fn()
const routerPushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => currentRoute,
  useRouter: () => ({ push: routerPushMock, afterEach: afterEachMock }),
}))

// appStore.sidebarCollapsed/sidebarWidth 可在各测试里单独覆盖，模拟「用户之前点过
// 折叠」的持久化状态。
let sidebarCollapsed = false
let sidebarWidthWhenBusiness = 240

vi.mock('@/stores', () => ({
  useAppStore: () => ({
    get sidebarCollapsed() { return sidebarCollapsed },
    get sidebarWidth() { return sidebarWidthWhenBusiness },
    globalLoading: false,
    loadingText: '',
    themeMode: 'light',
    fontSize: 'default',
    compactMode: false,
    setThemeMode: vi.fn(),
    setFontSize: vi.fn(),
    setCompactMode: vi.fn(),
    applyTheme: vi.fn(),
    initializeApp: vi.fn().mockResolvedValue(undefined),
    toggleSidebar: vi.fn(),
  }),
  useMenuStore: () => ({
    getMenuByPath: () => null,
    getMenuById: () => null,
  }),
  useAuthStore: () => ({
    displayName: 'tester',
    userRole: 'admin',
    user: { role: 'admin', roleName: '管理员' },
    logout: vi.fn(),
  }),
  useTabStore: () => ({
    addTab: vi.fn(),
    addRecentPage: vi.fn(),
  }),
  useSystemConfigStore: () => ({
    initialize: vi.fn().mockResolvedValue(undefined),
  }),
}))

import AppLayout from '../AppLayout.vue'

function mountLayout() {
  return mount(AppLayout, {
    global: {
      stubs: {
        // 业务/设置侧边栏、内容区都不是本测试关心的对象，stub 掉避免额外噪音。
        SideMenu: true,
        SettingsSideMenu: true,
        ContentArea: true,
      },
    },
  })
}

describe('AppLayout —— 设置外壳下的侧边栏宽度与折叠按钮', () => {
  beforeEach(() => {
    sidebarCollapsed = false
    sidebarWidthWhenBusiness = 240
    routerPushMock.mockClear()
  })

  it('设置外壳下 sidebarWidth 固定为 240，不受 sidebarCollapsed 影响', () => {
    sidebarCollapsed = true // 模拟用户此前点过折叠、状态已持久化
    sidebarWidthWhenBusiness = 64
    currentRoute = { path: '/admin/users', meta: { shell: 'settings' } }

    const w = mountLayout()
    // el-aside 未在测试里注册真实 Element Plus 插件，Vue 会把它当未知自定义
    // 元素直接渲染，:width 绑定落地为字面 width 属性（而非 ElAside 内部转换出的
    // style），这里直接读该属性即可验证 sidebarWidth 的计算结果。
    const aside = w.find('el-aside')
    expect(aside.exists()).toBe(true)
    expect(aside.attributes('width')).toBe('240px')
  })

  it('设置外壳下不渲染折叠按钮', () => {
    currentRoute = { path: '/admin/users', meta: { shell: 'settings' } }
    const w = mountLayout()
    expect(w.find('.collapse-btn').exists()).toBe(false)
  })

  it('业务外壳下 sidebarWidth 仍跟随 appStore.sidebarWidth（含折叠态）', () => {
    sidebarCollapsed = true
    sidebarWidthWhenBusiness = 64
    currentRoute = { path: '/home', meta: {} }

    const w = mountLayout()
    const aside = w.find('el-aside')
    expect(aside.attributes('width')).toBe('64px')
  })

  it('业务外壳下渲染折叠按钮', () => {
    currentRoute = { path: '/home', meta: {} }
    const w = mountLayout()
    expect(w.find('.collapse-btn').exists()).toBe(true)
  })
})
