import { describe, it, expect } from 'vitest'
import {
  SETTINGS_GROUPS,
  ALL_SETTINGS_ITEMS,
  findSettingsItem,
  filterGroups,
  firstAccessibleItemPath,
  LEGACY_PATH_ALIASES,
  SETTINGS_REDIRECTS,
} from '../settingsCatalog'

describe('SETTINGS_GROUPS', () => {
  it('共 7 组 22 条', () => {
    expect(SETTINGS_GROUPS).toHaveLength(7)
    expect(ALL_SETTINGS_ITEMS).toHaveLength(22)
  })

  it('条目 id 全局唯一', () => {
    const ids = ALL_SETTINGS_ITEMS.map(i => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('每条都有非空 label / perm / icon / component', () => {
    for (const it of ALL_SETTINGS_ITEMS) {
      expect(it.label, it.id).toBeTruthy()
      for (const p of itemPerms(it.perm)) expect(p, it.id).toMatch(/^admin\./)
      expect(it.icon, it.id).toBeTruthy()
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

import { itemPerms, canItem } from '../settingsCatalog'

describe('schema：any-of 权限', () => {
  it('itemPerms 归一化 string|string[]', () => {
    expect(itemPerms('admin.users')).toEqual(['admin.users'])
    expect(itemPerms(['admin.a', 'admin.b'])).toEqual(['admin.a', 'admin.b'])
  })
  it('canItem 任一命中即可', () => {
    expect(canItem(['admin.a', 'admin.b'], k => k === 'admin.b')).toBe(true)
    expect(canItem(['admin.a', 'admin.b'], () => false)).toBe(false)
    expect(canItem('admin.a', k => k === 'admin.a')).toBe(true)
  })
})

describe('AI 组合并', () => {
  it('共 7 组 22 条', () => {
    expect(SETTINGS_GROUPS).toHaveLength(7)
    expect(ALL_SETTINGS_ITEMS).toHaveLength(22)
  })
  it('被并掉的 5 个旧 id 不再是条目', () => {
    for (const id of ['ai-opencode', 'ai-skillopt', 'ai-batches', 'ai-sessions', 'ai-orchestrations']) {
      expect(findSettingsItem(id), id).toBeUndefined()
    }
  })
  it('三个域入口的 tabs 声明完整', () => {
    const tabIds = (id: string) => (findSettingsItem(id)?.tabs ?? []).map(t => t.id)
    expect(tabIds('ai-settings')).toEqual(['model', 'runtime'])
    expect(tabIds('ai-skills')).toEqual(['square', 'fit'])
    expect(tabIds('ai-execution')).toEqual(['batches', 'sessions', 'orchestrations'])
  })
  it('域入口 perm 为 any-of 数组', () => {
    expect(findSettingsItem('ai-execution')?.perm)
      .toEqual(['admin.ai_chat_admin', 'admin.ai_orchestration_admin'])
  })
})

describe('SETTINGS_REDIRECTS', () => {
  it('5 条旧路径齐全，目标是真实条目 + 合法 tab id', () => {
    // 注：sort() 默认按码位序，'ai-opencode'（o-p）排在 'ai-orchestrations'（o-r）之前
    expect(Object.keys(SETTINGS_REDIRECTS).sort())
      .toEqual(['ai-batches', 'ai-opencode', 'ai-orchestrations', 'ai-sessions', 'ai-skillopt'])
    for (const r of Object.values(SETTINGS_REDIRECTS)) {
      const item = findSettingsItem(r.path.replace('/admin/', ''))
      expect(item, r.path).toBeDefined()
      expect((item?.tabs ?? []).some(t => t.id === r.tab), r.path).toBe(true)
    }
  })
  it('重定向 key 不与真实条目 id 冲突', () => {
    for (const alias of Object.keys(SETTINGS_REDIRECTS)) {
      expect(findSettingsItem(alias), alias).toBeUndefined()
    }
  })
})
