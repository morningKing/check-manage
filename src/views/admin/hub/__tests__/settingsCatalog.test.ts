import { describe, it, expect } from 'vitest'
import {
  SETTINGS_CATALOG,
  filterCatalog,
  categoryPerms,
  resolveActiveTab,
  firstAccessibleCategoryPath,
} from '../settingsCatalog'

describe('settingsCatalog', () => {
  it('共 7 个分类，tab 总数 17', () => {
    expect(SETTINGS_CATALOG).toHaveLength(7)
    const tabCount = SETTINGS_CATALOG.reduce((n, c) => n + c.tabs.length, 0)
    expect(tabCount).toBe(17)
  })

  it('每个 tab 的权限 key 以 admin. 开头且唯一标识', () => {
    const ids = SETTINGS_CATALOG.flatMap(c => c.tabs.map(t => `${c.id}/${t.id}`))
    expect(new Set(ids).size).toBe(ids.length)
    for (const c of SETTINGS_CATALOG)
      for (const t of c.tabs) expect(t.perm.startsWith('admin.')).toBe(true)
  })

  it('filterCatalog：超管式 can=()=>true 返回全部 7 类', () => {
    expect(filterCatalog(() => true)).toHaveLength(7)
  })

  it('filterCatalog：仅 admin.users 时只剩访问控制类且只含用户 tab', () => {
    const out = filterCatalog(k => k === 'admin.users')
    expect(out).toHaveLength(1)
    expect(out[0].id).toBe('access')
    expect(out[0].tabs.map(t => t.id)).toStrictEqual(['users'])
  })

  it('filterCatalog：无任何权限返回空数组', () => {
    expect(filterCatalog(() => false)).toStrictEqual([])
  })

  it('filterCatalog 不污染 SETTINGS_CATALOG', () => {
    filterCatalog(k => k === 'admin.users')
    expect(SETTINGS_CATALOG).toHaveLength(7)
    expect(SETTINGS_CATALOG[0].tabs).toHaveLength(2)
  })

  it('categoryPerms：返回该分类全部 tab 权限 key', () => {
    expect(categoryPerms('access')).toStrictEqual(['admin.users', 'admin.roles'])
    expect(categoryPerms('data-ops')).toContain('admin.query')
    expect(categoryPerms('不存在')).toStrictEqual([])
  })

  it('resolveActiveTab：query 命中则用之，否则取首个', () => {
    const tabs = [{ id: 'a' }, { id: 'b' }] as any
    expect(resolveActiveTab(tabs, 'b')).toBe('b')
    expect(resolveActiveTab(tabs, 'x')).toBe('a')
    expect(resolveActiveTab(tabs, undefined)).toBe('a')
    expect(resolveActiveTab([], 'a')).toBe('')
  })

  it('firstAccessibleCategoryPath：超管 → 首个分类 access', () => {
    expect(firstAccessibleCategoryPath(() => true)).toBe('/admin/access')
  })
  it('firstAccessibleCategoryPath：仅 data-ops 权限 → /admin/data-ops', () => {
    const can = (k: string) => k === 'admin.query'
    expect(firstAccessibleCategoryPath(can)).toBe('/admin/data-ops')
  })
  it('firstAccessibleCategoryPath：无权限 → /home', () => {
    expect(firstAccessibleCategoryPath(() => false)).toBe('/home')
  })
})
