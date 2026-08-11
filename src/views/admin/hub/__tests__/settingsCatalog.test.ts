import { describe, it, expect } from 'vitest'
import {
  SETTINGS_GROUPS,
  ALL_SETTINGS_ITEMS,
  findSettingsItem,
  filterGroups,
  firstAccessibleItemPath,
  LEGACY_PATH_ALIASES,
} from '../settingsCatalog'

describe('SETTINGS_GROUPS', () => {
  it('共 7 组 21 条', () => {
    expect(SETTINGS_GROUPS).toHaveLength(7)
    expect(ALL_SETTINGS_ITEMS).toHaveLength(21)
  })

  it('条目 id 全局唯一', () => {
    const ids = ALL_SETTINGS_ITEMS.map(i => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('每条都有非空 label / perm / component', () => {
    for (const it of ALL_SETTINGS_ITEMS) {
      expect(it.label, it.id).toBeTruthy()
      expect(it.perm, it.id).toMatch(/^admin\./)
      expect(typeof it.component, it.id).toBe('function')
    }
  })

  it('收编的 3 条在位且权限键正确', () => {
    expect(findSettingsItem('trigger-rules')?.perm).toBe('admin.trigger_rules')
    expect(findSettingsItem('dependency-manager')?.perm).toBe('admin.dependencies')
    expect(findSettingsItem('factory-reset')?.perm).toBe('admin.backup')
  })

  it('danger 只标在 factory-reset 上', () => {
    const dangers = ALL_SETTINGS_ITEMS.filter(i => i.danger).map(i => i.id)
    expect(dangers).toEqual(['factory-reset'])
  })

  it('factory-reset 排在其所在组的最末', () => {
    const g = SETTINGS_GROUPS.find(x => x.items.some(i => i.id === 'factory-reset'))!
    expect(g.items[g.items.length - 1].id).toBe('factory-reset')
  })
})

describe('findSettingsItem', () => {
  it('命中返回条目', () => {
    expect(findSettingsItem('users')?.label).toBe('用户管理')
  })
  it('未命中返回 undefined', () => {
    expect(findSettingsItem('nope')).toBeUndefined()
  })
})

describe('filterGroups', () => {
  it('剔除无权限条目', () => {
    const groups = filterGroups(k => k === 'admin.users')
    expect(groups).toHaveLength(1)
    expect(groups[0].items.map(i => i.id)).toEqual(['users'])
  })

  it('整组无权限时该组不出现', () => {
    const groups = filterGroups(k => k === 'admin.users')
    expect(groups.some(g => g.id === 'data-ops')).toBe(false)
  })

  it('全无权限返回空数组', () => {
    expect(filterGroups(() => false)).toEqual([])
  })

  it('全有权限返回 7 组', () => {
    expect(filterGroups(() => true)).toHaveLength(7)
  })
})

describe('firstAccessibleItemPath', () => {
  it('返回首个有权限条目的路径', () => {
    expect(firstAccessibleItemPath(k => k === 'admin.backup')).toBe('/admin/backup')
  })
  it('无任何权限时回退 /home', () => {
    expect(firstAccessibleItemPath(() => false)).toBe('/home')
  })
})

describe('LEGACY_PATH_ALIASES', () => {
  it('4 条老路径别名齐全且目标是合法条目', () => {
    expect(LEGACY_PATH_ALIASES).toEqual({
      'webhook-settings': 'webhook',
      'ai-scan-tasks': 'ai-scan',
      'menu-export': 'data-export',
      'etl-tasks': 'etl',
    })
    for (const target of Object.values(LEGACY_PATH_ALIASES)) {
      expect(findSettingsItem(target), target).toBeDefined()
    }
  })

  it('别名 key 不与任何真实条目 id 重名', () => {
    for (const alias of Object.keys(LEGACY_PATH_ALIASES)) {
      expect(findSettingsItem(alias), alias).toBeUndefined()
    }
  })
})

import { SETTINGS_GROUPS as _G, ALL_SETTINGS_ITEMS as _I } from '../settingsCatalog'

describe('分组 id 与条目 id 不冲突', () => {
  it('两者无交集（否则 /admin/<分组> 与 /admin/<条目> 路由打架）', () => {
    const groupIds = new Set(_G.map(g => g.id))
    const clash = _I.filter(i => groupIds.has(i.id)).map(i => i.id)
    expect(clash).toEqual([])
  })
})
