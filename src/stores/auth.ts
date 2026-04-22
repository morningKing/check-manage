/**
 * 认证状态管理 Store
 *
 * 管理用户登录状态、当前用户信息、角色权限
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getStorage, setStorage, removeStorage, STORAGE_KEYS } from '@/utils/storage'
import { login as loginApi, getCurrentUser as getMeApi } from '@/api/auth'
import { useMenuStore } from '@/stores/menu'
import type { UserInfo, UserRole, LoginParams } from '@/types'
import type { CurrentBranch } from '@/api/projectVersion'

export const useAuthStore = defineStore('auth', () => {
  // ==================== State ====================

  /** JWT 令牌 */
  const token = ref<string>(getStorage(STORAGE_KEYS.AUTH_TOKEN, ''))

  /** 当前用户信息 */
  const user = ref<UserInfo | null>(getStorage<UserInfo | null>(STORAGE_KEYS.USER_INFO, null))

  /** 当前工作分支 */
  const currentBranch = ref<CurrentBranch | null>(null)

  // ==================== Getters ====================

  /** 是否已登录 */
  const isLoggedIn = computed(() => !!token.value && !!user.value)

  /** 当前用户角色 */
  const userRole = computed<UserRole | null>(() => user.value?.role ?? null)

  /** 是否管理员 */
  const isAdmin = computed(() => user.value?.role === 'admin')

  /** 是否访客（只读权限） */
  const isGuest = computed(() => user.value?.role === 'guest')

  /** 显示名称 */
  const displayName = computed(() => user.value?.displayName ?? '')

  // ==================== Actions ====================

  /**
   * 用户登录
   */
  async function login(params: LoginParams): Promise<void> {
    const result = await loginApi(params)
    token.value = result.token
    user.value = result.user
    setStorage(STORAGE_KEYS.AUTH_TOKEN, result.token)
    setStorage(STORAGE_KEYS.USER_INFO, result.user)
  }

  /**
   * 用户登出
   */
  function logout(): void {
    token.value = ''
    user.value = null
    removeStorage(STORAGE_KEYS.AUTH_TOKEN)
    removeStorage(STORAGE_KEYS.USER_INFO)
  }

  /**
   * 从服务端刷新用户信息（页面刷新时验证 token 有效性）
   */
  async function fetchCurrentUser(): Promise<void> {
    if (!token.value) return
    try {
      const userInfo = await getMeApi()
      user.value = userInfo
      setStorage(STORAGE_KEYS.USER_INFO, userInfo)
    } catch {
      logout()
    }
  }

  /**
   * 检查当前用户是否有权限访问指定路径
   *
   * 基于菜单配置的 roles 字段判断权限
   */
  function hasRoutePermission(path: string): boolean {
    if (!user.value) return false
    const role = user.value.role

    // 首页和根路径始终允许
    if (path === '/home' || path === '/') return true

    // 从 menuStore 查找该路径对应的菜单
    const menuStore = useMenuStore()
    const menu = menuStore.getMenuByPath(path)

    if (menu) {
      const menuRoles = menu.roles || ['admin', 'developer', 'guest']
      return menuRoles.includes(role)
    }

    // 动态页面路径 /page/:pageId — 没有对应菜单项则放行给已登录用户
    if (path.startsWith('/page/')) return true

    // 未匹配到菜单的路径，默认仅管理员可访问
    return role === 'admin'
  }

  return {
    // State
    token,
    user,
    currentBranch,
    // Getters
    isLoggedIn,
    userRole,
    isAdmin,
    isGuest,
    displayName,
    // Actions
    login,
    logout,
    fetchCurrentUser,
    hasRoutePermission,
  }
})
