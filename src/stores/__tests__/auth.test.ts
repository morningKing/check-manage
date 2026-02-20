import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock external dependencies
vi.mock('@/utils/storage', () => ({
  getStorage: vi.fn((_key: string, defaultValue: any) => defaultValue),
  setStorage: vi.fn(),
  removeStorage: vi.fn(),
  STORAGE_KEYS: {
    AUTH_TOKEN: 'check-manage:token',
    USER_INFO: 'check-manage:userInfo',
    SIDEBAR_COLLAPSED: 'check-manage:sidebarCollapsed',
  },
}))

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  getCurrentUser: vi.fn(),
}))

vi.mock('@/stores/menu', () => ({
  useMenuStore: vi.fn(() => ({
    getMenuByPath: (path: string) => {
      if (path === '/admin') return { roles: ['admin'] }
      if (path === '/shared') return { roles: ['admin', 'developer', 'guest'] }
      return undefined
    },
  })),
}))

import { setStorage, removeStorage } from '@/utils/storage'
import { login as loginApi, getCurrentUser as getMeApi } from '@/api/auth'
import { useAuthStore } from '../auth'

const mockSetStorage = vi.mocked(setStorage)
const mockRemoveStorage = vi.mocked(removeStorage)
const mockLoginApi = vi.mocked(loginApi)
const mockGetMeApi = vi.mocked(getMeApi)

describe('Auth Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('默认未登录', () => {
      const store = useAuthStore()
      expect(store.token).toBe('')
      expect(store.user).toBeNull()
      expect(store.isLoggedIn).toBe(false)
    })
  })

  describe('login', () => {
    it('登录成功后更新 token/user/storage', async () => {
      const store = useAuthStore()
      const result = {
        token: 'jwt-123',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' as const },
      }
      mockLoginApi.mockResolvedValueOnce(result as any)

      await store.login({ username: 'admin', password: '123456' })

      expect(store.token).toBe('jwt-123')
      expect(store.user).toEqual(result.user)
      expect(store.isLoggedIn).toBe(true)
      expect(mockSetStorage).toHaveBeenCalledWith('check-manage:token', 'jwt-123')
      expect(mockSetStorage).toHaveBeenCalledWith('check-manage:userInfo', result.user)
    })
  })

  describe('logout', () => {
    it('清除状态和 storage', async () => {
      const store = useAuthStore()
      // Set up logged in state
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt-123',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })

      store.logout()

      expect(store.token).toBe('')
      expect(store.user).toBeNull()
      expect(store.isLoggedIn).toBe(false)
      expect(mockRemoveStorage).toHaveBeenCalledWith('check-manage:token')
      expect(mockRemoveStorage).toHaveBeenCalledWith('check-manage:userInfo')
    })
  })

  describe('computed properties', () => {
    it('isLoggedIn 需要 token 和 user 都存在', async () => {
      const store = useAuthStore()
      expect(store.isLoggedIn).toBe(false)

      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt-123',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })
      expect(store.isLoggedIn).toBe(true)
    })

    it('userRole 返回当前用户角色', async () => {
      const store = useAuthStore()
      expect(store.userRole).toBeNull()

      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'dev', displayName: '开发', role: 'developer' },
      } as any)
      await store.login({ username: 'dev', password: '123456' })
      expect(store.userRole).toBe('developer')
    })

    it('isAdmin 判断管理员角色', async () => {
      const store = useAuthStore()

      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })
      expect(store.isAdmin).toBe(true)
    })

    it('displayName 返回显示名称', async () => {
      const store = useAuthStore()
      expect(store.displayName).toBe('')

      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'admin', displayName: '超级管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })
      expect(store.displayName).toBe('超级管理员')
    })
  })

  describe('fetchCurrentUser', () => {
    it('无 token 时不请求', async () => {
      const store = useAuthStore()
      await store.fetchCurrentUser()
      expect(mockGetMeApi).not.toHaveBeenCalled()
    })

    it('成功时更新 user 和 storage', async () => {
      const store = useAuthStore()
      // Login first
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt-123',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })
      vi.clearAllMocks()

      const updatedUser = { id: 'u1', username: 'admin', displayName: '更新名称', role: 'admin' }
      mockGetMeApi.mockResolvedValueOnce(updatedUser as any)

      await store.fetchCurrentUser()
      expect(store.user).toEqual(updatedUser)
      expect(mockSetStorage).toHaveBeenCalledWith('check-manage:userInfo', updatedUser)
    })

    it('失败时 logout', async () => {
      const store = useAuthStore()
      // Login first
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt-123',
        user: { id: 'u1', username: 'admin', displayName: '管理员', role: 'admin' },
      } as any)
      await store.login({ username: 'admin', password: '123456' })

      mockGetMeApi.mockRejectedValueOnce(new Error('401'))

      await store.fetchCurrentUser()
      expect(store.token).toBe('')
      expect(store.user).toBeNull()
    })
  })

  describe('hasRoutePermission', () => {
    it('未登录返回 false', () => {
      const store = useAuthStore()
      expect(store.hasRoutePermission('/admin')).toBe(false)
    })

    it('首页和根路径始终允许', async () => {
      const store = useAuthStore()
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'guest', displayName: '访客', role: 'guest' },
      } as any)
      await store.login({ username: 'guest', password: '123456' })

      expect(store.hasRoutePermission('/home')).toBe(true)
      expect(store.hasRoutePermission('/')).toBe(true)
    })

    it('/page/ 路径对已登录用户放行', async () => {
      const store = useAuthStore()
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'guest', displayName: '访客', role: 'guest' },
      } as any)
      await store.login({ username: 'guest', password: '123456' })

      expect(store.hasRoutePermission('/page/some-id')).toBe(true)
    })

    it('未匹配菜单的路径默认仅 admin 可访问', async () => {
      const store = useAuthStore()
      mockLoginApi.mockResolvedValueOnce({
        token: 'jwt',
        user: { id: 'u1', username: 'dev', displayName: '开发', role: 'developer' },
      } as any)
      await store.login({ username: 'dev', password: '123456' })

      expect(store.hasRoutePermission('/unknown-path')).toBe(false)
    })
  })
})
