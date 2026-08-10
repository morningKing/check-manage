import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// menuStore 会被 hasRoutePermission 的兜底分支用到，这里给个最小桩
vi.mock('@/stores/menu', () => ({
  useMenuStore: () => ({ menuTree: [], getMenuByPath: () => null }),
}))

import { useAuthStore } from '@/stores/auth'

/**
 * 造一个带指定权限集合的登录态 auth store。
 *
 * `UserInfo`（src/types/user.ts）的 `displayName` 是必填字段（非 `?`），brief 里的
 * 示例遗漏了它、只能靠 `as never` 绕过类型检查；这里按实际类型补上 `displayName`，
 * 就不再需要类型断言。`permissions` 字段形状对齐 `ResolvedPermissions`
 * （src/types/role.ts）：isSuperuser / adminKeys / defaultPageAccess / pagePerms。
 */
function makeAuth(adminKeys: string[]) {
  const auth = useAuthStore()
  auth.$patch({
    user: {
      id: 'u1', username: 'tester', displayName: 'tester', role: 'custom',
      permissions: {
        isSuperuser: false,
        adminKeys,
        defaultPageAccess: 'read',
        pagePerms: {},
      },
    },
  })
  return auth
}

describe('hasRoutePermission —— 设置中心', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('未登录一律拒绝', () => {
    const auth = useAuthStore()
    expect(auth.hasRoutePermission('/admin/users')).toBe(false)
  })

  it('/admin/<id> 有权限则放行', () => {
    const auth = makeAuth(['admin.users'])
    expect(auth.hasRoutePermission('/admin/users')).toBe(true)
  })

  it('/admin/<id> 无权限则拒绝', () => {
    const auth = makeAuth(['admin.users'])
    expect(auth.hasRoutePermission('/admin/backup')).toBe(false)
  })

  it('收编的 trigger-rules 现在真的有权限门', () => {
    const auth = makeAuth(['admin.users'])
    expect(auth.hasRoutePermission('/admin/trigger-rules')).toBe(false)
    const auth2 = makeAuth(['admin.trigger_rules'])
    expect(auth2.hasRoutePermission('/admin/trigger-rules')).toBe(true)
  })

  it('factory-reset 走 admin.backup', () => {
    const auth = makeAuth(['admin.backup'])
    expect(auth.hasRoutePermission('/admin/factory-reset')).toBe(true)
  })

  it('/admin 只要有任一条目权限就放行', () => {
    const auth = makeAuth(['admin.backup'])
    expect(auth.hasRoutePermission('/admin')).toBe(true)
  })

  it('/admin 一个条目权限都没有则拒绝', () => {
    const auth = makeAuth([])
    expect(auth.hasRoutePermission('/admin')).toBe(false)
  })

  it('未知的 /admin/<段> 落到兜底逻辑，不误放行', () => {
    const auth = makeAuth(['admin.users'])
    expect(auth.hasRoutePermission('/admin/nonexistent')).toBe(false)
  })
})
