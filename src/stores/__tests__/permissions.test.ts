import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, it, expect } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import type { UserInfo } from '@/types'

function userWith(perms: UserInfo['permissions']): UserInfo {
  return { id: 'u', username: 'u', displayName: 'u', role: 'r', permissions: perms }
}

function adminNoPerms(): UserInfo {
  // Built-in admin whose resolved permissions are absent (stale session created
  // before the permissions payload existed). role === 'admin' is always a superuser.
  return { id: 'a', username: 'admin', displayName: '管理员', role: 'admin' }
}

describe('built-in admin superuser fallback (absent permissions)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('treats role==="admin" as superuser when permissions are absent', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = adminNoPerms()
    expect(s.can('admin.users')).toBe(true)
    expect(s.can('admin.roles')).toBe(true)
    expect(s.canPage('page-x', 'delete')).toBe(true)
    expect(s.hasRoutePermission('/admin/users')).toBe(true)
  })

  it('does NOT grant a non-admin role when permissions are absent', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = { id: 'g', username: 'g', displayName: 'g', role: 'guest' }
    expect(s.can('admin.users')).toBe(false)
    expect(s.canPage('page-x', 'create')).toBe(false)
    expect(s.hasRoutePermission('/admin/users')).toBe(false)
  })
})

describe('auth store permission helpers', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('superuser can do everything', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: true, adminKeys: [], defaultPageAccess: 'none', pagePerms: {} })
    expect(s.can('admin.users')).toBe(true)
    expect(s.canPage('page-x', 'delete')).toBe(true)
  })

  it('can() checks adminKeys', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: false, adminKeys: ['admin.backup'], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.can('admin.backup')).toBe(true)
    expect(s.can('admin.users')).toBe(false)
  })

  it('canPage() falls back to defaultPageAccess', () => {
    const s = useAuthStore()
    s.user = userWith({ isSuperuser: false, adminKeys: [], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.canPage('page-x', 'read')).toBe(true)
    expect(s.canPage('page-x', 'update')).toBe(false)
  })

  it('canPage() uses explicit row over default', () => {
    const s = useAuthStore()
    s.user = userWith({
      isSuperuser: false, adminKeys: [], defaultPageAccess: 'write',
      pagePerms: { 'page-x': { read: true, create: false, update: true, delete: false } },
    })
    expect(s.canPage('page-x', 'create')).toBe(false)
    expect(s.canPage('page-x', 'update')).toBe(true)
    expect(s.canPage('page-y', 'create')).toBe(true) // default write
  })

  it('denies when no permissions present', () => {
    const s = useAuthStore()
    s.user = userWith(undefined)
    expect(s.can('admin.users')).toBe(false)
    expect(s.canPage('page-x', 'read')).toBe(false)
  })
})

describe('hasRoutePermission for admin paths', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('blocks admin path without capability', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: false, adminKeys: [], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/users')).toBe(false)
  })

  it('allows admin path with capability', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: false, adminKeys: ['admin.users'], defaultPageAccess: 'read', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/users')).toBe(true)
  })

  it('superuser allowed on any admin path', () => {
    const s = useAuthStore()
    s.token = 't'
    s.user = userWith({ isSuperuser: true, adminKeys: [], defaultPageAccess: 'none', pagePerms: {} })
    expect(s.hasRoutePermission('/admin/backup')).toBe(true)
  })
})
