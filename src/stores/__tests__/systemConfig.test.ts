import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock API functions
vi.mock('@/api/systemConfig', () => ({
  getSystemConfig: vi.fn(),
  updateSystemConfig: vi.fn(),
  getHomeWidgets: vi.fn(),
  batchUpdateHomeWidgets: vi.fn(),
  createHomeWidget: vi.fn(),
  deleteHomeWidget: vi.fn(),
  updateWidgetsLayout: vi.fn(),
}))

// Mock auth store
vi.mock('@/stores/auth', () => ({
  useAuthStore: vi.fn(() => ({
    userRole: 'admin',
  })),
}))

import {
  getSystemConfig,
  updateSystemConfig,
  getHomeWidgets,
  updateWidgetsLayout,
} from '@/api/systemConfig'
import { useAuthStore } from '@/stores/auth'
import { useSystemConfigStore } from '../systemConfig'

const mockGetSystemConfig = vi.mocked(getSystemConfig)
const mockUpdateSystemConfig = vi.mocked(updateSystemConfig)
const mockGetHomeWidgets = vi.mocked(getHomeWidgets)
const mockUpdateWidgetsLayout = vi.mocked(updateWidgetsLayout)
const mockUseAuthStore = vi.mocked(useAuthStore)

describe('SystemConfig Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initialize', () => {
    it('初始化后加载配置', async () => {
      const mockConfig = {
        systemName: 'Test System',
        systemShortName: 'TS',
      }
      const mockWidgets = [
        {
          id: 'widget-1',
          widgetType: 'welcome' as const,
          title: 'Welcome',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: [],
        },
      ]

      mockGetSystemConfig.mockResolvedValueOnce(mockConfig as any)
      mockGetHomeWidgets.mockResolvedValueOnce(mockWidgets as any)

      const store = useSystemConfigStore()
      await store.initialize()

      expect(store.systemConfig).toEqual(mockConfig)
      expect(store.widgets).toEqual(mockWidgets)
      expect(store.initialized).toBe(true)
      expect(store.loading).toBe(false)
    })

    it('重复初始化不会重复加载', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const store = useSystemConfigStore()
      await store.initialize()
      await store.initialize()

      expect(mockGetSystemConfig).toHaveBeenCalledTimes(1)
      expect(mockGetHomeWidgets).toHaveBeenCalledTimes(1)
    })
  })

  describe('visibleWidgets', () => {
    it('visibleWidgets 根据角色过滤', async () => {
      const mockWidgets = [
        {
          id: 'widget-1',
          widgetType: 'welcome' as const,
          title: 'Admin Only',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: ['admin'],
        },
        {
          id: 'widget-2',
          widgetType: 'welcome' as const,
          title: 'All Users',
          content: {},
          enabled: true,
          order: 2,
          visibleRoles: [],
        },
        {
          id: 'widget-3',
          widgetType: 'welcome' as const,
          title: 'Developer Only',
          content: {},
          enabled: true,
          order: 3,
          visibleRoles: ['developer'],
        },
        {
          id: 'widget-4',
          widgetType: 'welcome' as const,
          title: 'Disabled Widget',
          content: {},
          enabled: false,
          order: 4,
          visibleRoles: [],
        },
      ]

      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce(mockWidgets as any)

      const store = useSystemConfigStore()
      await store.initialize()

      // Admin user should see admin-only and all-users widgets
      const adminVisible = store.visibleWidgets
      expect(adminVisible.length).toBe(2)
      expect(adminVisible.map(w => w.id)).toEqual(['widget-1', 'widget-2'])
    })

    it('未配置 visibleRoles 时所有用户可见', async () => {
      const mockWidgets = [
        {
          id: 'widget-1',
          widgetType: 'welcome' as const,
          title: 'Public Widget',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: [],
        },
      ]

      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce(mockWidgets as any)

      // Mock as guest user
      mockUseAuthStore.mockReturnValueOnce({ userRole: 'guest' } as any)

      const store = useSystemConfigStore()
      await store.initialize()

      expect(store.visibleWidgets.length).toBe(1)
      expect(store.visibleWidgets[0].id).toBe('widget-1')
    })

    it('guest 用户只能看到无角色限制的区块', async () => {
      const mockWidgets = [
        {
          id: 'widget-1',
          widgetType: 'welcome' as const,
          title: 'Admin Only',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: ['admin'],
        },
        {
          id: 'widget-2',
          widgetType: 'welcome' as const,
          title: 'Public',
          content: {},
          enabled: true,
          order: 2,
          visibleRoles: [],
        },
      ]

      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce(mockWidgets as any)

      // Mock as guest user
      mockUseAuthStore.mockReturnValueOnce({ userRole: 'guest' } as any)

      const store = useSystemConfigStore()
      await store.initialize()

      const guestVisible = store.visibleWidgets
      expect(guestVisible.length).toBe(1)
      expect(guestVisible[0].id).toBe('widget-2')
    })

    it('按 order 排序', async () => {
      const mockWidgets = [
        {
          id: 'widget-3',
          widgetType: 'welcome' as const,
          title: 'Widget 3',
          content: {},
          enabled: true,
          order: 3,
          visibleRoles: [],
        },
        {
          id: 'widget-1',
          widgetType: 'welcome' as const,
          title: 'Widget 1',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: [],
        },
        {
          id: 'widget-2',
          widgetType: 'welcome' as const,
          title: 'Widget 2',
          content: {},
          enabled: true,
          order: 2,
          visibleRoles: [],
        },
      ]

      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce(mockWidgets as any)

      const store = useSystemConfigStore()
      await store.initialize()

      const ordered = store.visibleWidgets
      expect(ordered.map(w => w.id)).toEqual(['widget-1', 'widget-2', 'widget-3'])
    })
  })

  describe('updateConfig', () => {
    it('updateConfig 更新系统名称', async () => {
      const initialConfig = {
        systemName: 'Original Name',
        systemShortName: 'ON',
      }
      const updatedConfig = {
        systemName: 'Updated Name',
        systemShortName: 'UN',
      }

      mockGetSystemConfig.mockResolvedValueOnce(initialConfig as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)
      mockUpdateSystemConfig.mockResolvedValueOnce(updatedConfig as any)

      const store = useSystemConfigStore()
      await store.initialize()

      await store.updateConfig({
        systemName: 'Updated Name',
        systemShortName: 'UN',
      })

      expect(mockUpdateSystemConfig).toHaveBeenCalledWith({
        systemName: 'Updated Name',
        systemShortName: 'UN',
      })
      expect(store.systemConfig).toEqual(updatedConfig)
    })

    it('updateConfig 失败时抛出错误', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)
      mockUpdateSystemConfig.mockRejectedValueOnce(new Error('Network error'))

      const store = useSystemConfigStore()
      await store.initialize()

      await expect(store.updateConfig({
        systemName: 'New Name',
        systemShortName: 'NN',
      })).rejects.toThrow('Network error')

      // Config should remain unchanged
      expect(store.systemConfig.systemName).toBe('Test')
    })
  })

  describe('computed properties', () => {
    it('systemName 返回系统名称', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({
        systemName: 'My System',
        systemShortName: 'MS',
      } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const store = useSystemConfigStore()
      await store.initialize()

      expect(store.systemName).toBe('My System')
    })

    it('systemShortName 返回系统简称', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({
        systemName: 'My System',
        systemShortName: 'MS',
      } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const store = useSystemConfigStore()
      await store.initialize()

      expect(store.systemShortName).toBe('MS')
    })
  })

  describe('updateLayout', () => {
    it('批量保存布局后用返回值刷新 widgets', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)

      const updated = [
        {
          id: 'welcome',
          widgetType: 'welcome' as const,
          title: 'Welcome',
          content: {},
          enabled: true,
          order: 1,
          visibleRoles: [],
          layout: { x: 0, y: 0, w: 6, h: 4 },
        },
      ]
      mockUpdateWidgetsLayout.mockResolvedValueOnce(updated as any)

      const store = useSystemConfigStore()
      await store.initialize()
      await store.updateLayout([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])

      expect(mockUpdateWidgetsLayout).toHaveBeenCalledWith([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])
      expect(store.widgets).toEqual(updated)
    })

    it('保存失败时抛出错误', async () => {
      mockGetSystemConfig.mockResolvedValueOnce({ systemName: 'Test', systemShortName: 'T' } as any)
      mockGetHomeWidgets.mockResolvedValueOnce([] as any)
      mockUpdateWidgetsLayout.mockRejectedValueOnce(new Error('Network error'))

      const store = useSystemConfigStore()
      await store.initialize()

      await expect(
        store.updateLayout([{ id: 'welcome', x: 0, y: 0, w: 6, h: 4 }])
      ).rejects.toThrow('Network error')
    })
  })
})