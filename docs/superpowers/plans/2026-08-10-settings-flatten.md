# 设置中心扁平化为独立外壳 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设置中心从「业务侧边栏 → 分类侧边栏 → tab → 功能」四层嵌套，拍平成「设置侧边栏 → 功能」两层，每个功能一个真实路由 `/admin/<id>`，一次点击到达。

**Architecture:** 目录数据（`settingsCatalog.ts`）改形为「分组 + 条目」并成为**唯一真源** —— 侧边栏渲染、路由生成、权限判定全从它派生。21 条路由在模块加载时由目录遍历生成，直接挂为 AppLayout 的子路由；`/admin` 只做重定向。进入设置路由时，AppLayout 按 `meta.shell === 'settings'` 用设置侧边栏替换业务侧边栏。

**Tech Stack:** Vue 3 + TypeScript + Vue Router 4 + Element Plus + Pinia；Vitest + @vue/test-utils；Playwright（MCP）。

设计文档：`docs/superpowers/specs/2026-08-10-settings-flatten-design.md`

## Global Constraints

- **本改造必须从 `main` 另开分支**，不要叠在 `feat/row-custom-actions`（PR #107 未合）上。
- TypeScript 严格模式（`noUnusedLocals`、`noUnusedParameters` 已开）。`npx vue-tsc --noEmit` 必须**零错误零输出**。
- 所有面向用户的字符串用中文，与现有 UI 一致。
- **绝对不新增任何依赖。**
- 提交信息用中文 + conventional commit 前缀。
- **不改任何功能组件的内部逻辑**（`UserManager.vue`、`BackupManager.vue` 等一律原样复用）。
- **不改后端**，不做数据库迁移，`menus` 表的 `menu-settings` 行保持不动。
- 前台跑测试，不要放后台。前端全量回归基线以实际跑出来的为准（本分支从 `main` 开出，`main` 的基线是 **877 passed**）。
- **绝对不要用 `taskkill /IM node.exe` 或任何批量杀 node/python 的命令** —— 会干掉客户端托管的 Playwright MCP。停服务必须先 `netstat -ano | grep :端口` 拿 PID，再按 PID 精确 kill。

---

### Task 1: 目录数据模型改形

**Files:**
- Rewrite: `src/views/admin/hub/settingsCatalog.ts`
- Rewrite: `src/views/admin/hub/__tests__/settingsCatalog.test.ts`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SettingsItem { id, label, perm, component, danger? }`
  - `SettingsGroup { id, label, icon, items }`
  - `SETTINGS_GROUPS: readonly SettingsGroup[]`（7 组 21 条）
  - `ALL_SETTINGS_ITEMS: readonly SettingsItem[]`（扁平化，供路由生成与权限查表）
  - `findSettingsItem(id: string): SettingsItem | undefined`
  - `filterGroups(can): SettingsGroup[]`（剔除无权限条目，再剔除空组）
  - `firstAccessibleItemPath(can): string`
  - `LEGACY_PATH_ALIASES: Record<string, string>`（4 条老路径别名）

**背景**：现有 `settingsCatalog.ts` 是「分类 + tab」模型（`SETTINGS_CATALOG` / `filterCatalog` / `categoryPerms` / `resolveActiveTab` / `firstAccessibleCategoryPath`），组件映射在另一个文件 `settingsComponents.ts`。本任务把两者合并成一个数据源。

- [ ] **Step 1: 写失败的测试**

覆盖 `src/views/admin/hub/__tests__/settingsCatalog.test.ts`：

```ts
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts
```

预期：FAIL，`SETTINGS_GROUPS` 等导出不存在。

- [ ] **Step 3: 重写目录**

用下面内容整体覆盖 `src/views/admin/hub/settingsCatalog.ts`：

```ts
/**
 * 设置中心目录 —— 唯一真源。
 *
 * 侧边栏渲染、路由生成、权限判定全部从这里派生：新增一个设置功能只需在
 * 这里加一条，路由和权限门会自动跟上，不会出现「加了功能忘了加路由」。
 *
 * 分组（SettingsGroup）只是**视觉分组**：不可点、不折叠、不参与路由。
 */
import type { Component } from 'vue'

export interface SettingsItem {
  /** 路由末段，如 'users' → /admin/users */
  id: string
  label: string
  /** 所需管理能力 key（admin.*） */
  perm: string
  /** 懒加载的功能组件 */
  component: () => Promise<Component>
  /** 不可逆/高危操作：视觉区分，且排在所在组最末 */
  danger?: boolean
}

export interface SettingsGroup {
  id: string
  /** 侧边栏分组标题 */
  label: string
  /** Element Plus 图标组件名 */
  icon: string
  items: SettingsItem[]
}

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  { id: 'access', label: '访问控制', icon: 'Lock', items: [
    { id: 'users', label: '用户管理', perm: 'admin.users',
      component: () => import('@/views/admin/UserManager.vue') },
    { id: 'roles', label: '角色权限', perm: 'admin.roles',
      component: () => import('@/views/admin/RoleManager.vue') },
  ] },
  { id: 'structure', label: '结构配置', icon: 'Files', items: [
    { id: 'menu', label: '菜单管理', perm: 'admin.menus',
      component: () => import('@/views/admin/MenuManager.vue') },
    { id: 'page-config', label: '页面配置', perm: 'admin.page_configs',
      component: () => import('@/views/admin/PageConfigManager.vue') },
    { id: 'workflows', label: '工作流', perm: 'admin.workflows',
      component: () => import('@/views/admin/WorkflowManager.vue') },
    { id: 'dependency-manager', label: '依赖管理', perm: 'admin.dependencies',
      component: () => import('@/views/admin/DependencyManager.vue') },
  ] },
  { id: 'integration', label: '集成对接', icon: 'Link', items: [
    { id: 'api-keys', label: 'Open API', perm: 'admin.api_keys',
      component: () => import('@/views/admin/ApiKeyManager.vue') },
    { id: 'webhook', label: 'Webhook', perm: 'admin.webhooks',
      component: () => import('@/views/admin/WebhookSettings.vue') },
    { id: 'kefu', label: '智能客服', perm: 'admin.kefu',
      component: () => import('@/views/admin/KefuManager.vue') },
  ] },
  { id: 'ai', label: 'AI 能力', icon: 'MagicStick', items: [
    { id: 'ai-settings', label: 'AI 配置', perm: 'admin.ai_settings',
      component: () => import('@/views/admin/AiSettings.vue') },
    { id: 'ai-scan', label: 'AI 定时任务', perm: 'admin.ai_scan',
      component: () => import('@/views/admin/AiScanTaskManager.vue') },
  ] },
  { id: 'data-ops', label: '数据运维', icon: 'DataLine', items: [
    { id: 'query', label: '数据查询', perm: 'admin.query',
      component: () => import('@/views/admin/QueryConsole.vue') },
    { id: 'data-export', label: '数据导出', perm: 'admin.menus',
      component: () => import('@/views/admin/DataMigrationPage.vue') },
    { id: 'etl', label: 'ETL 管理', perm: 'admin.etl_tasks',
      component: () => import('@/views/admin/EtlTaskManager.vue') },
    { id: 'export-scripts', label: '导出脚本', perm: 'admin.export_scripts',
      component: () => import('@/views/admin/ExportScriptManager.vue') },
    { id: 'validation-scripts', label: '校验脚本', perm: 'admin.validation_scripts',
      component: () => import('@/views/admin/ValidationScriptManager.vue') },
    { id: 'trigger-rules', label: '联动规则', perm: 'admin.trigger_rules',
      component: () => import('@/views/admin/TriggerRuleManager.vue') },
  ] },
  { id: 'sys-ops', label: '系统运维', icon: 'Monitor', items: [
    { id: 'operation-log', label: '操作日志', perm: 'admin.operation_logs',
      component: () => import('@/views/admin/OperationLog.vue') },
    { id: 'backup', label: '系统备份', perm: 'admin.backup',
      component: () => import('@/views/admin/BackupManager.vue') },
    { id: 'factory-reset', label: '恢复出厂设置', perm: 'admin.backup', danger: true,
      component: () => import('@/views/admin/FactoryReset.vue') },
  ] },
  { id: 'general', label: '通用设置', icon: 'Setting', items: [
    { id: 'system-settings', label: '系统设置', perm: 'admin.system_config',
      component: () => import('@/views/admin/SystemSettings.vue') },
  ] },
]

/** 扁平化的全部条目，供路由生成与权限查表 */
export const ALL_SETTINGS_ITEMS: readonly SettingsItem[] =
  SETTINGS_GROUPS.flatMap(g => g.items)

/** 按条目 id 查找（路径末段 → 条目） */
export function findSettingsItem(id: string): SettingsItem | undefined {
  return ALL_SETTINGS_ITEMS.find(i => i.id === id)
}

/** 按权限过滤：剔除无权限条目，再剔除空组 */
export function filterGroups(can: (key: string) => boolean): SettingsGroup[] {
  return SETTINGS_GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => can(i.perm)) }))
    .filter(g => g.items.length > 0)
}

/**
 * 用户可访问的首个条目路径；一个都没有时回退 /home。
 * 供 /admin 的动态 redirect 使用。
 */
export function firstAccessibleItemPath(can: (key: string) => boolean): string {
  const groups = filterGroups(can)
  return groups.length ? `/admin/${groups[0].items[0].id}` : '/home'
}

/**
 * 老路径别名 → 新条目 id。
 *
 * 拍平后大多数老路径（/admin/users 等）与条目 id 同名、自动变成真路由，
 * 但这 4 条历史命名与新 id 不同，删掉重定向会直接 404，必须保留。
 */
export const LEGACY_PATH_ALIASES: Record<string, string> = {
  'webhook-settings': 'webhook',
  'ai-scan-tasks': 'ai-scan',
  'menu-export': 'data-export',
  'etl-tasks': 'etl',
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts
```

预期：全部通过。

> 此时全量 `npm run test` 和 `vue-tsc` **还会红** —— `settingsComponents.ts`、`CategoryView.vue`、`SettingsHub.vue`、`auth.ts`、`router/index.ts` 仍在引用旧导出。这是预期的，Task 2/3 会收拾。**不要**为了让它们变绿而在本任务里补回旧导出。

- [ ] **Step 5: 提交**

```bash
git add src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/__tests__/settingsCatalog.test.ts
git commit -m "refactor(settings): 目录数据模型从分类+tab 改形为分组+条目"
```

---

### Task 2: 路由生成、兼容重定向、删除 tab 层

**Files:**
- Modify: `src/router/index.ts`（文件头注释、import、`/admin` 路由块、15 条重定向、`firstAccessibleCategoryPath` 调用）
- Rewrite: `src/router/__tests__/settingsHubRoutes.test.ts`
- Delete: `src/views/admin/hub/SettingsHub.vue`、`src/views/admin/hub/CategoryView.vue`、`src/views/admin/hub/settingsComponents.ts`
- Delete: `src/views/admin/hub/__tests__/SettingsHub.test.ts`、`src/views/admin/hub/__tests__/CategoryView.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `SETTINGS_GROUPS`、`ALL_SETTINGS_ITEMS`、`findSettingsItem`、`firstAccessibleItemPath`、`LEGACY_PATH_ALIASES`
- Produces: 21 条命名为 `Settings_<id>` 的路由，`meta = { title, perm, shell: 'settings' }`；`/admin` 重定向；分类兼容重定向；老路径别名重定向

- [ ] **Step 1: 写失败的测试**

覆盖 `src/router/__tests__/settingsHubRoutes.test.ts`：

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'
import { buildSettingsRoutes, buildSettingsRedirects } from '../settingsRoutes'
import { ALL_SETTINGS_ITEMS } from '@/views/admin/hub/settingsCatalog'

/** 造一个只含设置路由的最小 router，便于断言解析结果 */
function makeRouter(can: (k: string) => boolean) {
  const routes: RouteRecordRaw[] = [
    { path: '/home', name: 'Home', component: { template: '<div/>' } },
    ...buildSettingsRoutes(),
    ...buildSettingsRedirects(() => can),
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

describe('buildSettingsRoutes', () => {
  it('生成 21 条路由', () => {
    expect(buildSettingsRoutes()).toHaveLength(ALL_SETTINGS_ITEMS.length)
  })

  it('每条的 path/name/meta 都正确', () => {
    const routes = buildSettingsRoutes()
    const users = routes.find(r => r.path === '/admin/users')!
    expect(users.name).toBe('Settings_users')
    expect(users.meta).toMatchObject({
      title: '用户管理',
      perm: 'admin.users',
      shell: 'settings',
    })
  })

  it('每条 meta 都带 shell=settings', () => {
    for (const r of buildSettingsRoutes()) {
      expect(r.meta?.shell, String(r.path)).toBe('settings')
    }
  })
})

describe('/admin 重定向', () => {
  it('跳到首个有权限的条目', async () => {
    const r = makeRouter(k => k === 'admin.backup')
    await r.push('/admin')
    await r.isReady()
    expect(r.currentRoute.value.path).toBe('/admin/backup')
  })

  it('无任何权限时回退 /home', async () => {
    const r = makeRouter(() => false)
    await r.push('/admin')
    await r.isReady()
    expect(r.currentRoute.value.path).toBe('/home')
  })
})

describe('分类兼容重定向', () => {
  it('带合法 tab → 跳该条目', async () => {
    const r = makeRouter(() => true)
    await r.push('/admin/access?tab=roles')
    expect(r.currentRoute.value.path).toBe('/admin/roles')
  })

  it('不带 tab → 跳该组首个有权限条目', async () => {
    const r = makeRouter(() => true)
    await r.push('/admin/data-ops')
    expect(r.currentRoute.value.path).toBe('/admin/query')
  })

  it('tab 值非法 → 跳该组首个有权限条目', async () => {
    const r = makeRouter(() => true)
    await r.push('/admin/access?tab=bogus')
    expect(r.currentRoute.value.path).toBe('/admin/users')
  })

  it('该组一个有权限条目都没有 → 跳 /admin 再落到全局首个', async () => {
    const r = makeRouter(k => k === 'admin.backup')
    await r.push('/admin/access')
    expect(r.currentRoute.value.path).toBe('/admin/backup')
  })
})

describe('老路径别名重定向', () => {
  it.each([
    ['/admin/webhook-settings', '/admin/webhook'],
    ['/admin/ai-scan-tasks', '/admin/ai-scan'],
    ['/admin/menu-export', '/admin/data-export'],
    ['/admin/etl-tasks', '/admin/etl'],
  ])('%s → %s', async (from, to) => {
    const r = makeRouter(() => true)
    await r.push(from)
    expect(r.currentRoute.value.path).toBe(to)
  })
})

describe('同名老路径直接成为真路由', () => {
  it.each(['/admin/users', '/admin/roles', '/admin/backup', '/admin/system-settings'])(
    '%s 是真路由而非重定向',
    async (path) => {
      const r = makeRouter(() => true)
      await r.push(path)
      expect(r.currentRoute.value.path).toBe(path)
      expect(r.currentRoute.value.meta.shell).toBe('settings')
    }
  )
})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
npx vitest run src/router/__tests__/settingsHubRoutes.test.ts
```

预期：FAIL，`../settingsRoutes` 不存在。

- [ ] **Step 3: 新建路由生成模块**

创建 `src/router/settingsRoutes.ts`：

```ts
/**
 * 设置中心路由：由目录数据生成，不手写路由表。
 *
 * 拆成独立模块而不是写在 index.ts 里，是为了让路由生成逻辑可以被单测直接
 * 驱动（index.ts 引入了 store、动态路由注册等一堆运行时依赖，不好在测试里
 * 实例化）。
 */
import type { RouteRecordRaw } from 'vue-router'
import {
  SETTINGS_GROUPS,
  ALL_SETTINGS_ITEMS,
  findSettingsItem,
  firstAccessibleItemPath,
  LEGACY_PATH_ALIASES,
} from '@/views/admin/hub/settingsCatalog'

/** 21 条功能路由。路径写全 /admin/<id>，直接挂为 AppLayout 的子路由。 */
export function buildSettingsRoutes(): RouteRecordRaw[] {
  return ALL_SETTINGS_ITEMS.map(item => ({
    path: `/admin/${item.id}`,
    name: `Settings_${item.id}`,
    component: item.component,
    meta: {
      title: item.label,
      perm: item.perm,
      shell: 'settings',
    },
  }))
}

/**
 * 兼容重定向：
 *   1. /admin                    → 首个有权限条目
 *   2. /admin/<分类id>[?tab=x]   → /admin/<x> 或该组首个有权限条目
 *   3. /admin/<老路径别名>       → /admin/<新条目 id>
 *
 * `getCan` 是个惰性取权限判定函数的工厂 —— 重定向在导航时才求值，那时
 * auth store 才一定就绪；测试里可以直接注入一个纯函数。
 */
export function buildSettingsRedirects(
  getCan: () => (key: string) => boolean
): RouteRecordRaw[] {
  const routes: RouteRecordRaw[] = [
    {
      path: '/admin',
      redirect: () => firstAccessibleItemPath(getCan()),
    },
  ]

  for (const group of SETTINGS_GROUPS) {
    routes.push({
      path: `/admin/${group.id}`,
      redirect: (to) => {
        const can = getCan()
        const tab = to.query.tab
        if (typeof tab === 'string' && findSettingsItem(tab)) {
          return `/admin/${tab}`
        }
        const first = group.items.find(i => can(i.perm))
        return first ? `/admin/${first.id}` : '/admin'
      },
    })
  }

  for (const [alias, targetId] of Object.entries(LEGACY_PATH_ALIASES)) {
    routes.push({ path: `/admin/${alias}`, redirect: `/admin/${targetId}` })
  }

  return routes
}
```

> **注意分组 id 与条目 id 不能重名**，否则两条路由会打架。当前 7 个分组 id（`access`/`structure`/`integration`/`ai`/`data-ops`/`sys-ops`/`general`）与 21 个条目 id 无交集 —— Task 1 的测试已断言别名不与条目重名，这里再靠 Step 5 的断言兜一次。

- [ ] **Step 4: 跑测试确认通过**

```bash
npx vitest run src/router/__tests__/settingsHubRoutes.test.ts
```

预期：全部通过。

- [ ] **Step 5: 补一条「分组 id 不与条目 id 冲突」的断言**

追加到 `src/views/admin/hub/__tests__/settingsCatalog.test.ts`：

```ts
import { SETTINGS_GROUPS as _G, ALL_SETTINGS_ITEMS as _I } from '../settingsCatalog'

describe('分组 id 与条目 id 不冲突', () => {
  it('两者无交集（否则 /admin/<分组> 与 /admin/<条目> 路由打架）', () => {
    const groupIds = new Set(_G.map(g => g.id))
    const clash = _I.filter(i => groupIds.has(i.id)).map(i => i.id)
    expect(clash).toEqual([])
  })
})
```

跑：`npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`，预期通过。

- [ ] **Step 6: 接进 `router/index.ts`**

改 5 处：

1. 文件头注释里的路由结构说明，把
```
 * ├── /admin          - 设置中心（管理控制台，左栏 7 分类 + 右侧 tab，仅管理员）
 * │   └── /admin/{access|structure|integration|ai|data-ops|sys-ops|general}
 * ├── /admin/<旧路径>  - 兼容重定向到设置中心对应分类?tab=
```
换成
```
 * ├── /admin/<功能>    - 设置中心（21 个功能各自独立路由，由目录数据生成）
 * ├── /admin          - 重定向到首个有权限的设置功能
 * ├── /admin/<分类|老路径> - 兼容重定向
```

2. 顶部 import：删掉
```ts
import { firstAccessibleCategoryPath } from '@/views/admin/hub/settingsCatalog'
```
换成
```ts
import { buildSettingsRoutes, buildSettingsRedirects } from './settingsRoutes'
```

3. 删掉整个 `/admin` 路由块（`component: SettingsHub` + `redirect` + 7 个 `children`）。

4. 删掉那 15 条 `{ path: 'admin/xxx', redirect: '/admin/yyy?tab=zzz' }`，以及 `admin/trigger-rules`、`admin/dependency-manager`、`admin/factory-reset` 三条独立路由（它们现在由目录生成）。

5. 在 Layout 路由的 `children` 数组里展开生成的路由：

```ts
      ...buildSettingsRoutes(),
      ...buildSettingsRedirects(() => {
        const auth = useAuthStore()
        return auth.can
      }),
```

放在 `dashboard/:id?` 之前、`workflow/*` 之后即可（顺序不影响，静态段优先于动态段匹配）。

> `useAuthStore` 在 `index.ts` 顶部已经 import 了（现有代码用它做 `firstAccessibleCategoryPath`），不用新增 import。若删干净后 TS 报它未使用，说明上面第 5 步没写对。

- [ ] **Step 7: 删除 tab 层的三个文件与两个测试**

```bash
git rm src/views/admin/hub/SettingsHub.vue \
       src/views/admin/hub/CategoryView.vue \
       src/views/admin/hub/settingsComponents.ts \
       src/views/admin/hub/__tests__/SettingsHub.test.ts \
       src/views/admin/hub/__tests__/CategoryView.test.ts
```

- [ ] **Step 8: 类型检查**

```bash
npx vue-tsc --noEmit
```

预期：仍有错误，来自 `src/stores/auth.ts`（还在 import 已删除的 `filterCatalog` / `categoryPerms`）。这是预期的，Task 3 收拾。**其余文件不应有错**；如果有，先修完再进下一步。

- [ ] **Step 9: 提交**

```bash
git add -A src/router src/views/admin/hub
git commit -m "refactor(settings): 由目录生成 21 条扁平路由，删除分类+tab 两层容器"
```

---

### Task 3: 权限判定收敛

**Files:**
- Modify: `src/stores/auth.ts`（import、`ADMIN_PATH_PERMISSION`、`hasRoutePermission`）
- Test: `src/stores/__tests__/authRoutePermission.test.ts`（新建）

**Interfaces:**
- Consumes: Task 1 的 `findSettingsItem`、`filterGroups`
- Produces: `hasRoutePermission(path)` 对 `/admin` 与 `/admin/<id>` 的新判定

**背景**：`src/stores/auth.ts` 顶部有一张 19 条的 `ADMIN_PATH_PERMISSION` 映射表，`hasRoutePermission` 里对 `/admin`、`/admin/<分类>` 有特判分支。拍平后这些塌缩成一次目录查表。

- [ ] **Step 1: 写失败的测试**

创建 `src/stores/__tests__/authRoutePermission.test.ts`：

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// menuStore 会被 hasRoutePermission 的兜底分支用到，这里给个最小桩
vi.mock('@/stores/menu', () => ({
  useMenuStore: () => ({ menuTree: [], getMenuByPath: () => null }),
}))

import { useAuthStore } from '@/stores/auth'

/** 造一个带指定权限集合的登录态 auth store */
function makeAuth(adminKeys: string[]) {
  const auth = useAuthStore()
  auth.$patch({
    user: {
      id: 'u1', username: 'tester', role: 'custom',
      permissions: {
        isSuperuser: false,
        adminKeys,
        defaultPageAccess: 'read',
        pagePerms: {},
      },
    } as never,
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
```

> 如果 `auth.$patch` 造登录态的写法与该 store 的实际 state 形状对不上（`permissions` 的字段名以 `src/types` 里的定义为准），**以实际类型为准调整这个辅助函数**，但每条断言的语义必须保留。先读一眼 `src/stores/auth.ts` 里 `can()` 是怎么读权限的。

- [ ] **Step 2: 跑测试确认失败**

```bash
npx vitest run src/stores/__tests__/authRoutePermission.test.ts
```

预期：`/admin/trigger-rules` 那条会失败（今天它在 `ADMIN_PATH_PERMISSION` 里有映射，所以本来就通过 —— 但 `/admin/nonexistent` 和 `/admin` 的行为会因旧特判而不同）。**记录实际失败项**，据此确认新旧行为差异。

- [ ] **Step 3: 改 `auth.ts`**

1. 顶部 import 改成：
```ts
import { findSettingsItem, filterGroups } from '@/views/admin/hub/settingsCatalog'
```

2. **删掉整张 `ADMIN_PATH_PERMISSION` 常量**（约 18-38 行）。

3. `hasRoutePermission` 里，把这段：
```ts
    // 设置中心：父路由按"是否有任一可见分类"放行；分类路由按该类是否有可见 tab
    if (path === '/admin') return filterCatalog(can).length > 0
    if (path.startsWith('/admin/')) {
      const seg = path.split('/')[2]
      const perms = categoryPerms(seg)
      if (perms.length) return perms.some(k => can(k))
      // seg 非分类 id（旧段 users/menu… 或 factory-reset/trigger-rules）→ 落到下方 ADMIN_PATH_PERMISSION
    }

    // 管理页：按所需能力 key 判定
    const required = ADMIN_PATH_PERMISSION[path]
    if (required) return can(required)
```
换成：
```ts
    // 设置中心：/admin 按「是否有任一可见条目」放行；/admin/<id> 按该条目的能力 key
    if (path === '/admin') return filterGroups(can).length > 0
    if (path.startsWith('/admin/')) {
      const item = findSettingsItem(path.split('/')[2])
      if (item) return can(item.perm)
      // 非条目 id（分组 id / 老路径别名）→ 由路由重定向处理，这里落到下方兜底
    }
```

- [ ] **Step 4: 跑测试确认通过**

```bash
npx vitest run src/stores/__tests__/authRoutePermission.test.ts
npx vue-tsc --noEmit
```

预期：测试全绿；`vue-tsc` **零错误零输出**（此时前两个任务留下的类型错误应全部消失）。

- [ ] **Step 5: 跑全量前端回归**

```bash
npm run test
```

预期：除了 `AppLayout` 相关（Task 4 才做）可能受影响外应当全绿。**若有失败，逐条查清**：是旧测试引用了已删除的模块（该删/该改），还是真的改坏了行为。

- [ ] **Step 6: 提交**

```bash
git add src/stores/auth.ts src/stores/__tests__/authRoutePermission.test.ts
git commit -m "refactor(settings): 权限判定收敛为目录查表，删除 ADMIN_PATH_PERMISSION 映射表"
```

---

### Task 4: 设置侧边栏组件 + 外壳切换

**Files:**
- Create: `src/components/layout/SettingsSideMenu.vue`
- Create: `src/components/layout/__tests__/SettingsSideMenu.test.ts`
- Modify: `src/components/layout/AppLayout.vue`（侧边栏切换 + 面包屑）
- Modify: `src/stores/app.ts`（记住最后一个业务路由）
- Modify: `src/router/index.ts`（守卫里更新最后业务路由）

**Interfaces:**
- Consumes: Task 1 的 `filterGroups`、`SettingsGroup`
- Produces:
  - `SettingsSideMenu.vue` 组件
  - app store 新增 `lastBusinessPath: Ref<string>` 与 `setLastBusinessPath(path: string)`

- [ ] **Step 1: 给 app store 加「最后业务路由」**

在 `src/stores/app.ts` 的 state 区（`initialized` 附近）加：

```ts
  /**
   * 进入设置中心前最后停留的业务路由，供「← 返回工作区」使用。
   * 刻意不持久化：刷新后回退到 /home 即可，不值得为它引入存储。
   */
  const lastBusinessPath = ref<string>('/home')

  function setLastBusinessPath(path: string): void {
    lastBusinessPath.value = path
  }
```

并在文件末尾的 `return { ... }` 里导出 `lastBusinessPath` 与 `setLastBusinessPath`。

- [ ] **Step 2: 在路由守卫里维护它**

`src/router/index.ts` 的 `router.beforeEach` 里，在「6. 设置页面标题」**之前**插入：

```ts
  // 5.5 记住最后一个业务路由，供设置中心的「返回工作区」使用
  if (to.meta.shell !== 'settings' && to.path !== '/login') {
    appStore.setLastBusinessPath(to.fullPath)
  }
```

`appStore` 在守卫里已经取到了（第 3 步），直接用。

- [ ] **Step 3: 写失败的组件测试**

创建 `src/components/layout/__tests__/SettingsSideMenu.test.ts`：

```ts
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingsSideMenu from '../SettingsSideMenu.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never
})

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ path: '/admin/users' }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))

let canImpl: (k: string) => boolean = () => true
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canImpl(k) }),
  useAppStore: () => ({ lastBusinessPath: '/page/orders' }),
}))

function mountMenu() {
  return mount(SettingsSideMenu)
}

describe('SettingsSideMenu', () => {
  it('全权限时渲染 7 个分组标题与 21 个条目链接', () => {
    canImpl = () => true
    const w = mountMenu()
    expect(w.findAll('.settings-menu__group-label')).toHaveLength(7)
    expect(w.findAll('a')).toHaveLength(21 + 1) // +1 是返回工作区
  })

  it('无权限的条目不渲染', () => {
    canImpl = k => k === 'admin.users'
    const w = mountMenu()
    const texts = w.findAll('a').map(a => a.text())
    expect(texts).toContain('用户管理')
    expect(texts).not.toContain('系统备份')
  })

  it('整组无权限时该组标题也不渲染', () => {
    canImpl = k => k === 'admin.users'
    const w = mountMenu()
    const labels = w.findAll('.settings-menu__group-label').map(e => e.text())
    expect(labels).toEqual(['访问控制'])
  })

  it('danger 条目带 danger class', () => {
    canImpl = () => true
    const w = mountMenu()
    const danger = w.find('.settings-menu__item--danger')
    expect(danger.exists()).toBe(true)
    expect(danger.text()).toContain('恢复出厂设置')
  })

  it('点返回工作区跳到 lastBusinessPath', async () => {
    canImpl = () => true
    pushMock.mockClear()
    const w = mountMenu()
    await w.find('.settings-menu__back').trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/page/orders')
  })
})
```

- [ ] **Step 4: 跑测试确认失败**

```bash
npx vitest run src/components/layout/__tests__/SettingsSideMenu.test.ts
```

预期：FAIL，组件不存在。

- [ ] **Step 5: 实现 `SettingsSideMenu.vue`**

创建 `src/components/layout/SettingsSideMenu.vue`：

```vue
<template>
  <div class="settings-menu">
    <button type="button" class="settings-menu__back" @click="goBack">
      <el-icon><ArrowLeft /></el-icon>
      <span>返回工作区</span>
    </button>

    <nav class="settings-menu__nav">
      <div v-for="g in groups" :key="g.id" class="settings-menu__group">
        <div class="settings-menu__group-label">
          <el-icon><component :is="iconOf(g.icon)" /></el-icon>
          <span>{{ g.label }}</span>
        </div>
        <RouterLink
          v-for="item in g.items"
          :key="item.id"
          :to="`/admin/${item.id}`"
          class="settings-menu__item"
          :class="{
            'settings-menu__item--active': route.path === `/admin/${item.id}`,
            'settings-menu__item--danger': item.danger,
          }"
        >
          {{ item.label }}
        </RouterLink>
      </div>
    </nav>
  </div>
</template>

<script setup lang="ts">
/**
 * 设置中心侧边栏 —— 平铺全部有权限的设置功能。
 *
 * 分组标题不可点、不折叠：分类在这里只是视觉分组，不再有路由意义。
 * 一次点击到达任何功能是这次改造的核心目标，任何「再展开一层」的设计都
 * 是在把问题改回去。
 */
import { computed } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import {
  ArrowLeft, Lock, Files, Link, MagicStick, DataLine, Monitor, Setting,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { useAuthStore, useAppStore } from '@/stores'
import { filterGroups } from '@/views/admin/hub/settingsCatalog'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const app = useAppStore()

const groups = computed(() => filterGroups(auth.can))

const ICON_MAP: Record<string, Component> = {
  Lock, Files, Link, MagicStick, DataLine, Monitor, Setting,
}
function iconOf(name: string): Component {
  return ICON_MAP[name] ?? Setting
}

function goBack(): void {
  router.push(app.lastBusinessPath || '/home')
}
</script>

<style scoped lang="scss">
.settings-menu {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  padding: 8px 0 16px;
}

.settings-menu__back {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 12px 12px;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 14px;
  cursor: pointer;

  &:hover { background: var(--el-fill-color-light); color: var(--el-color-primary); }
}

.settings-menu__group { margin-bottom: 14px; }

.settings-menu__group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 22px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  user-select: none;
}

.settings-menu__item {
  display: block;
  padding: 7px 22px 7px 44px;
  color: var(--el-text-color-regular);
  font-size: 14px;
  text-decoration: none;

  &:hover { background: var(--el-fill-color-light); }
}

.settings-menu__item--active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 500;
}

.settings-menu__item--danger {
  color: var(--el-color-danger);

  &:hover { background: var(--el-color-danger-light-9); }
}
</style>
```

> 如果 `@/stores` 的 barrel 没有导出 `useAppStore`，改成从 `@/stores/app` 直接引入，并同步改测试里的 mock 路径。**先看一眼 `src/stores/index.ts` 导出了什么**。

- [ ] **Step 6: 跑测试确认通过**

```bash
npx vitest run src/components/layout/__tests__/SettingsSideMenu.test.ts
```

预期：5 条全过。

- [ ] **Step 7: 接进 `AppLayout.vue`**

1. 模板里把
```vue
    <el-aside :width="sidebarWidth + 'px'" class="app-aside">
      <SideMenu />
    </el-aside>
```
改成
```vue
    <el-aside :width="sidebarWidth + 'px'" class="app-aside">
      <SettingsSideMenu v-if="isSettingsShell" />
      <SideMenu v-else />
    </el-aside>
```

2. script 区加 import 与计算属性（`useRoute` 若已引入则复用）：
```ts
import SettingsSideMenu from './SettingsSideMenu.vue'

/** 设置中心路由用独立外壳：设置侧边栏取代业务侧边栏 */
const isSettingsShell = computed(() => route.meta.shell === 'settings')
```

3. 面包屑：找到现有 `breadcrumbs` 计算属性，让它在 `isSettingsShell` 为真时返回
```ts
[{ path: '/admin', title: '设置中心' }, { path: route.path, title: route.meta.title as string }]
```
形状（**以现有 `breadcrumbs` 元素的实际字段名为准**，先读一眼它现在返回什么）。

- [ ] **Step 8: 类型检查 + 全量回归**

```bash
npx vue-tsc --noEmit
npm run test
```

预期：`vue-tsc` 零输出；前端全绿。若 `AppLayout` 已有测试因侧边栏结构变化而红，按新结构更新它。

- [ ] **Step 9: 提交**

```bash
git add src/components/layout src/stores/app.ts src/router/index.ts
git commit -m "feat(settings): 设置侧边栏取代业务侧边栏，平铺全部功能"
```

---

### Task 5: 修硬编码链接 + 文档更新

**Files:**
- Modify: `src/views/workflow/WorkflowEditor.vue:172`
- Modify: `docs/user-guide/admin/backups.md`、`docs/user-guide/ai/assistant.md`、`docs/user-guide/ai/scan-tasks.md`、`docs/user-guide/admin/roles-rbac.md`

**Interfaces:**
- Consumes: Task 2 产出的新路径
- Produces: 无代码接口

- [ ] **Step 1: 修 WorkflowEditor 的死链**

`src/views/workflow/WorkflowEditor.vue` 第 172 行：
```ts
const LIST_PATH = '/admin/structure?tab=workflows'
```
改成
```ts
const LIST_PATH = '/admin/workflows'
```

- [ ] **Step 2: 确认全仓没有别的硬编码**

```bash
grep -rn "admin/[a-z-]*?tab=" src/ --include=*.vue --include=*.ts
```
预期：无输出（`router/` 下的重定向表已在 Task 2 删除）。有输出就一并修掉。

- [ ] **Step 3: 更新用户文档**

按下表逐条改：

| 文件:行 | 现状 | 改成 |
|---|---|---|
| `docs/user-guide/admin/backups.md:3` | 设置中心 → 系统运维（`/admin/sys-ops`）→ 备份 | 设置中心 → 系统备份（`/admin/backup`） |
| `docs/user-guide/ai/assistant.md:70` | 设置中心 → AI 能力（`/admin/ai`） | 设置中心 → AI 配置（`/admin/ai-settings`） |
| `docs/user-guide/ai/assistant.md:74` | 在 `/admin/ai` 的「外部 MCP 服务」卡片 | 在 `/admin/ai-settings` 的「外部 MCP 服务」卡片 |
| `docs/user-guide/ai/scan-tasks.md:17` | 「系统配置 → 数据工具 → AI 定时任务」(`/admin/ai-scan-tasks`) | 「设置中心 → AI 定时任务」(`/admin/ai-scan`) |
| `docs/user-guide/admin/roles-rbac.md:61` | 侧边栏「系统配置 → 平台管理 → 角色权限」（`/admin/roles`） | 侧边栏「设置中心 → 角色权限」（`/admin/roles`） |
| `docs/user-guide/admin/roles-rbac.md:122` | `hasRoutePermission(path)`：`/admin/*` 按 `ADMIN_PATH_PERMISSION` 映射到能力 key 判定 | `hasRoutePermission(path)`：`/admin/<功能>` 按设置中心目录（`settingsCatalog.ts`）查表拿到该功能的能力 key 判定 |

⚠️ **不要误改**：`docs/user-guide/ai/smart-customer-service.md` 里的 `/admin/kefu/instances`、`GET /admin/kefu/instances` 等是**后端 API 路径**，与前端路由无关，保持原样。`roles-rbac.md:86` / `:158` 里泛指的 `/admin/*` 表述也可保留。

- [ ] **Step 4: 在 CLAUDE.md 记一笔**

在 "Frontend Structure" 一节末尾追加：

```markdown
*   `src/views/admin/hub/settingsCatalog.ts`: 设置中心的**唯一真源**。21 个设置功能按 7 个视觉分组组织，每条带 `id`（路由末段）/`label`/`perm`（`admin.*` 能力键）/`component`（懒加载）。侧边栏渲染（`src/components/layout/SettingsSideMenu.vue`）、路由生成（`src/router/settingsRoutes.ts`）、权限判定（`auth` store 的 `hasRoutePermission`）全部从它派生 —— 新增一个设置功能只需在这里加一条。分组只是视觉分组，**不参与路由**：每个功能都是独立路由 `/admin/<id>`，一次点击到达。进入设置路由时 `AppLayout` 按 `meta.shell === 'settings'` 用设置侧边栏替换业务侧边栏。老路径兼容见 `LEGACY_PATH_ALIASES` 与 `buildSettingsRedirects`。
```

- [ ] **Step 5: 提交**

```bash
git add src/views/workflow/WorkflowEditor.vue docs/ CLAUDE.md
git commit -m "docs(settings): 更新设置中心路径引用，修 WorkflowEditor 返回列表死链"
```

---

### Task 6: Playwright 实跑验证

**Files:**
- Create: `.playwright-mcp/settings-flatten-*.png`（截图证据）

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 可写进 PR 的验证结论

**这是项目 CLAUDE.md 的强制要求**：纯前端导航改造更要实跑，单测覆盖不到「真实浏览器里侧边栏是否真的换了、点击是否真的一次到位」。

- [ ] **Step 1: 起服务**

```bash
netstat -ano | grep -E ":(5173|3002|8080|3001) "
```
若有残留占用，**按 PID 精确 kill**（绝不批量杀 node）。然后：
```bash
npm run dev:all
```

- [ ] **Step 2: 建一个受限测试角色**

用 admin 登录（`admin` / `admin123`），进 `/admin/roles`，新建角色「设置扁平化验证」，**只开** `admin.users` 与 `admin.backup` 两个能力（跨两个分组，能同时验到「组内部分可见」与「整组不可见」）。再建一个测试用户绑定该角色。

- [ ] **Step 3: admin 视角验证**

1. 点侧边栏「设置中心」→ 断言业务菜单树消失、设置侧边栏出现、21 项一屏可见（必要时滚动）
2. 点「系统备份」→ URL 是 `/admin/backup`、面包屑显示「设置中心 / 系统备份」、页面正常渲染
3. 点「← 返回工作区」→ 回到进入设置前那个业务页面
4. 浏览器直接访问 `/admin/roles` 并刷新 → 正常落地、侧边栏是设置态、「返回工作区」回 `/home`
5. 老链接 `/admin/access?tab=users` → 重定向到 `/admin/users`
6. 老链接 `/admin/webhook-settings` → 重定向到 `/admin/webhook`
7. 「恢复出厂设置」在系统运维组最末、样式为危险色

截图存 `.playwright-mcp/settings-flatten-admin.png`。

- [ ] **Step 4: 受限角色视角验证**

用 Step 2 的测试用户登录：

1. 进设置中心 → 只看到「访问控制」组下的「用户管理」和「系统运维」组下的「系统备份」+「恢复出厂设置」
2. **「结构配置」「集成对接」「AI 能力」「数据运维」「通用设置」五个组标题完全不出现**（不是空组，是整组消失）
3. 直接敲 `/admin/etl` → 被守卫拦下、提示「您没有权限访问该页面」、跳回 `/home`
4. 直接敲 `/admin` → 落到 `/admin/users`（首个有权限条目）

截图存 `.playwright-mcp/settings-flatten-limited.png`。

- [ ] **Step 5: 工作流返回链验证**

admin 登录 → 进 `/admin/workflows` → 打开任一工作流编辑器 → 点「返回列表」→ 断言落到 `/admin/workflows` 而非死链。

- [ ] **Step 6: 清理测试角色**

删掉 Step 2 建的测试用户与角色。

- [ ] **Step 7: 构建验证**

```bash
npm run build
```
预期：成功。路由是生成的，确认构建期动态 import 分包没问题。

- [ ] **Step 8: 停服务**

`netstat -ano | grep -E ":(5173|3002) "` 拿 PID，**按 PID 精确 kill**。

- [ ] **Step 9: 提交截图**

```bash
git add .playwright-mcp/settings-flatten-*.png
git commit -m "test(settings): 补设置中心扁平化的 Playwright 实跑截图证据"
```

---

## 自查记录

**Spec 覆盖检查**：设计文档 §3.1 目录改形 → Task 1；§3.2 21 条条目 → Task 1；§3.3 路由生成 → Task 2；§3.4 权限收敛 → Task 3；§3.5 删除清单 → Task 2（三个文件）+ Task 3（映射表）；§3.6 无 KeepAlive → Task 2（不引入即满足）；§4 外壳切换 → Task 4；§5.1 老路径别名 → Task 1（常量）+ Task 2（路由）；§5.2 分类兼容重定向 → Task 2；§5.3 硬编码 → Task 5；§6 测试 → 各任务内嵌；§7 验证 → Task 6；§8 文档 → Task 5。无遗漏。

**需在实现时对齐的实际命名**（计划中已逐处标注，不是占位符）：
- `src/stores/index.ts` 是否导出 `useAppStore`（Task 4 Step 5）
- `AppLayout.vue` 现有 `breadcrumbs` 元素的字段名（Task 4 Step 7）
- `auth` store 的 `permissions` state 形状与 `can()` 的读法（Task 3 Step 1）

**类型一致性**：`SettingsItem.component` 的类型 `() => Promise<Component>` 与 Vue Router 的 `RouteRecordRaw.component` 接受的异步组件工厂签名兼容（Task 2 直接把它赋给 `component`）。`filterGroups` 在 Task 1 定义、Task 3（auth store）与 Task 4（侧边栏）消费，签名一致。`findSettingsItem` 在 Task 1 定义、Task 2（重定向）与 Task 3（权限）消费，签名一致。
