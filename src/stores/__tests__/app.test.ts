import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/utils/storage', () => ({
  getStorage: vi.fn((_key: string, defaultValue: any) => defaultValue),
  setStorage: vi.fn(),
  STORAGE_KEYS: {
    SIDEBAR_COLLAPSED: 'check-manage:sidebarCollapsed',
  },
}))

vi.mock('@/stores/menu', () => ({
  useMenuStore: vi.fn(() => ({
    fetchMenus: vi.fn().mockResolvedValue(undefined),
  })),
}))

vi.mock('@/stores/pageConfig', () => ({
  usePageConfigStore: vi.fn(() => ({
    fetchPageConfigs: vi.fn().mockResolvedValue(undefined),
  })),
}))

import { setStorage } from '@/utils/storage'
import { useAppStore } from '../app'

const mockSetStorage = vi.mocked(setStorage)

describe('App Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('默认未折叠', () => {
      const store = useAppStore()
      expect(store.sidebarCollapsed).toBe(false)
      expect(store.initialized).toBe(false)
      expect(store.globalLoading).toBe(false)
      expect(store.loadingText).toBe('')
    })
  })

  describe('sidebarWidth', () => {
    it('未折叠时返回 240', () => {
      const store = useAppStore()
      expect(store.sidebarWidth).toBe(240)
    })

    it('折叠时返回 64', () => {
      const store = useAppStore()
      store.toggleSidebar()
      expect(store.sidebarWidth).toBe(64)
    })
  })

  describe('toggleSidebar', () => {
    it('切换折叠状态', () => {
      const store = useAppStore()
      expect(store.sidebarCollapsed).toBe(false)

      store.toggleSidebar()
      expect(store.sidebarCollapsed).toBe(true)

      store.toggleSidebar()
      expect(store.sidebarCollapsed).toBe(false)
    })

    it('持久化到 storage', () => {
      const store = useAppStore()
      store.toggleSidebar()
      expect(mockSetStorage).toHaveBeenCalledWith('check-manage:sidebarCollapsed', true)
    })
  })

  describe('setSidebarCollapsed', () => {
    it('设置折叠状态', () => {
      const store = useAppStore()
      store.setSidebarCollapsed(true)
      expect(store.sidebarCollapsed).toBe(true)

      store.setSidebarCollapsed(false)
      expect(store.sidebarCollapsed).toBe(false)
    })
  })

  describe('showLoading / hideLoading', () => {
    it('显示加载状态', () => {
      const store = useAppStore()
      store.showLoading('加载中...')
      expect(store.globalLoading).toBe(true)
      expect(store.loadingText).toBe('加载中...')
    })

    it('显示加载默认文本', () => {
      const store = useAppStore()
      store.showLoading()
      expect(store.loadingText).toBe('加载中...')
    })

    it('隐藏加载状态', () => {
      const store = useAppStore()
      store.showLoading('测试')
      store.hideLoading()
      expect(store.globalLoading).toBe(false)
      expect(store.loadingText).toBe('')
    })
  })

  describe('initializeApp', () => {
    it('首次调用初始化成功', async () => {
      const store = useAppStore()
      await store.initializeApp()
      expect(store.initialized).toBe(true)
      expect(store.globalLoading).toBe(false)
    })

    it('重复调用跳过', async () => {
      const store = useAppStore()
      await store.initializeApp()
      expect(store.initialized).toBe(true)

      // 第二次调用不应重新加载
      await store.initializeApp()
      expect(store.initialized).toBe(true)
    })
  })
})
