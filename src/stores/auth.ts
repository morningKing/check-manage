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
import { findSettingsItem, filterGroups } from '@/views/admin/hub/settingsCatalog'
import type { UserInfo, UserRole, LoginParams } from '@/types'
import type { CurrentBranch } from '@/api/projectVersion'

type PageAction = 'read' | 'create' | 'update' | 'delete'

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

  /**
   * 是否超级用户。以解析后的 `permissions.isSuperuser` 为准；当权限集合缺失
   * （登录前/旧会话/尚未刷新）时回退到 role==='admin' —— 内置 admin 角色按设计
   * 永远是不可删除的超级用户，因此该回退安全且能避免把管理员锁在系统管理之外。
   */
  const isSuperuser = computed(
    () => user.value?.permissions?.isSuperuser ?? (user.value?.role === 'admin'),
  )

  /** 是否管理员（超级用户）。等价于 isSuperuser。 */
  const isAdmin = isSuperuser

  /** 是否访客（只读权限） */
  const isGuest = computed(() => user.value?.role === 'guest')

  /** 显示名称 */
  const displayName = computed(() => user.value?.displayName ?? '')

  /** 当前用户解析后的权限集合 */
  const permissions = computed(() => user.value?.permissions ?? null)

  /** 是否拥有某个管理功能权限 */
  function can(key: string): boolean {
    if (isSuperuser.value) return true
    const p = permissions.value
    return p ? p.adminKeys.includes(key) : false
  }

  /** 是否对某数据页拥有某 CRUD 动作权限 */
  function canPage(pageId: string, action: PageAction): boolean {
    if (isSuperuser.value) return true
    const p = permissions.value
    if (!p) return false
    const row = p.pagePerms[pageId]
    if (row) return !!row[action]
    if (p.defaultPageAccess === 'none') return false
    if (p.defaultPageAccess === 'read') return action === 'read'
    return true // 'write'
  }

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
   * 基于菜单配置的 roles 字段判断权限。
   *
   * @param meta 可选的路由 meta（`router.beforeEach` 里的 `to.meta`）。设置中心的
   *   路由由 `buildSettingsRoutes` 生成，`meta.perm` 与实际匹配到的路由直接绑定，
   *   优先使用；不传时（例如测试里直接传字符串路径）回退到按目录查表，语义不变。
   */
  function hasRoutePermission(path: string, meta?: Record<string, unknown>): boolean {
    if (!user.value) return false

    // 首页和根路径始终允许
    if (path === '/home' || path === '/') return true

    // 设置中心：/admin 按「是否有任一可见条目」放行；/admin/<id> 按该条目的能力 key
    if (path === '/admin') return filterGroups(can).length > 0
    if (path.startsWith('/admin/')) {
      const metaPerm = typeof meta?.perm === 'string' ? meta.perm : undefined
      const perm = metaPerm ?? findSettingsItem(path.split('/')[2])?.perm
      if (perm) return can(perm)
      // 非条目 id（分组 id / 老路径别名）→ 由路由重定向处理，这里落到下方兜底
    }

    // 从 menuStore 查找该路径对应的菜单
    const menuStore = useMenuStore()
    const menu = menuStore.getMenuByPath(path)

    if (menu) {
      const menuRoles = menu.roles || []
      // 空白名单或包含当前角色 slug 即放行；超管始终放行
      return isSuperuser.value || menuRoles.length === 0 || menuRoles.includes(user.value.role)
    }

    // 动态页面路径 /page/:pageId — 没有对应菜单项则放行给已登录用户
    if (path.startsWith('/page/')) return true

    // AI 助手及批任务页面 — 后端用 @write_required，只拦截访客；前端对齐
    if (path === '/ai-chat' || path.startsWith('/ai-chat/')) return !isGuest.value

    // 其他未匹配路径，默认仅超管可访问
    return isSuperuser.value
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
    isSuperuser,
    isGuest,
    displayName,
    permissions,
    // Actions
    login,
    logout,
    fetchCurrentUser,
    hasRoutePermission,
    can,
    canPage,
  }
})
