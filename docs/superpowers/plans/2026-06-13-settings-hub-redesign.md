# 设置中心（管理控制台）重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「数据工具」「系统配置」两个顶级菜单（16 叶、最深 3 级）合并为单一「设置中心」master-detail 外壳——左侧 7 类领域导航、右侧内容区，高相关功能用页内 tab 复用现有 16 个管理页，统一紧凑骨架。

**Architecture:** 纯前端重组 + 一处菜单数据迁移。新建一个**纯数据目录模块** `settingsCatalog.ts`（7 类 × tab，每 tab 带权限 key）、一个**异步组件注册表** `settingsComponents.ts`、一个**通用右栏容器** `CategoryView.vue`（按权限过滤 tab、`?tab=` 记忆、动态挂载现有管理组件）、一个**外壳** `SettingsHub.vue`（左栏分类 + `<router-view>`）。路由把 `/admin/*` 收为 `/admin` 父路由的子路由并保留旧路径重定向。现有 16 个管理页组件一律不改。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + Element Plus + Vue Router + Pinia + Vitest（前端）/ Python + pytest（菜单迁移）。

设计依据：`docs/superpowers/specs/2026-06-13-settings-hub-redesign-design.md`

---

## 既有锚点（实现前必读）

**权限映射**（`src/stores/auth.ts`）：
- `can(key: string): boolean`（`auth.ts:81`）——超管恒 true，否则查 `permissions.adminKeys`。
- `hasRoutePermission(path)`（`auth.ts:141`）——`/home`、`/` 放行；查 `ADMIN_PATH_PERMISSION[path]`（`auth.ts:17`）→ `can(required)`；否则查 menuStore.roles。
- `ADMIN_PATH_PERMISSION`（`auth.ts:17-37`）现有 16 条旧路径→key 映射（保留不删）。

**权限 key（`server/utils/permissions.py` `PERMISSION_CATALOG`）**，本计划用到：
`admin.users` `admin.roles` `admin.menus` `admin.page_configs` `admin.api_keys` `admin.ai_settings` `admin.export_scripts` `admin.validation_scripts` `admin.etl_tasks` `admin.ai_scan` `admin.query` `admin.webhooks` `admin.operation_logs` `admin.backup` `admin.system_config`。注：数据导出页 `DataMigrationPage`（路由 `/admin/menu-export`）权限 key = `admin.menus`（见 `auth.ts:19`）。

**16 个复用组件**（`src/views/admin/`，全部不改内部）：`UserManager` `RoleManager` `MenuManager` `PageConfigManager` `ApiKeyManager` `WebhookSettings` `AiSettings` `AiScanTaskManager` `QueryConsole` `DataMigrationPage` `EtlTaskManager` `ExportScriptManager` `ValidationScriptManager` `OperationLog` `BackupManager` `SystemSettings`。

**路由**（`src/router/index.ts`）：现 `staticRoutes[1].children` 平级含 16 个 `admin/*` 子路由 + 隐藏 `admin/factory-reset` + `dashboard/:id?` 等。`/admin/trigger-rules`、`/admin/dependency-manager` 不并入设置中心（保持原样，靠上下文入口访问）。

**菜单种子**（`server/seed_data.py` `MENUS`，`menu-3-b` 数据工具 / `menu-3` 系统配置子树）。侧边栏由 DB 菜单树渲染（`SideMenu.vue`/`MenuItem.vue`）。`src/api/menu.ts:89 getAvailableExportMenus` 的"过滤静态菜单"仅作用于导出选单，与侧边栏无关。

**目录/tab/权限/旧路径 全表**（贯穿全计划，务必一致）：

| 分类 id | 分类名 | icon | tab id | tab 名 | 权限 key | 复用组件 | 旧路径 |
|--------|-------|------|--------|-------|---------|---------|-------|
| access | 访问控制 | Lock | users | 用户管理 | admin.users | UserManager | /admin/users |
| access | | | roles | 角色权限 | admin.roles | RoleManager | /admin/roles |
| structure | 结构配置 | Files | menu | 菜单管理 | admin.menus | MenuManager | /admin/menu |
| structure | | | page-config | 页面配置 | admin.page_configs | PageConfigManager | /admin/page-config |
| integration | 集成对接 | Link | api-keys | Open API | admin.api_keys | ApiKeyManager | /admin/api-keys |
| integration | | | webhook | Webhook | admin.webhooks | WebhookSettings | /admin/webhook-settings |
| ai | AI 能力 | MagicStick | ai-settings | AI 配置 | admin.ai_settings | AiSettings | /admin/ai-settings |
| ai | | | ai-scan | AI 定时任务 | admin.ai_scan | AiScanTaskManager | /admin/ai-scan-tasks |
| data-ops | 数据运维 | DataLine | query | 数据查询 | admin.query | QueryConsole | /admin/query |
| data-ops | | | data-export | 数据导出 | admin.menus | DataMigrationPage | /admin/menu-export |
| data-ops | | | etl | ETL 管理 | admin.etl_tasks | EtlTaskManager | /admin/etl-tasks |
| data-ops | | | export-scripts | 导出脚本 | admin.export_scripts | ExportScriptManager | /admin/export-scripts |
| data-ops | | | validation-scripts | 校验脚本 | admin.validation_scripts | ValidationScriptManager | /admin/validation-scripts |
| sys-ops | 系统运维 | Monitor | operation-log | 操作日志 | admin.operation_logs | OperationLog | /admin/operation-log |
| sys-ops | | | backup | 系统备份 | admin.backup | BackupManager | /admin/backup |
| general | 通用设置 | Setting | system-settings | 系统设置 | admin.system_config | SystemSettings | /admin/system-settings |

---

## Task 1: 设置目录纯数据模块 + 过滤逻辑（TDD）

**Files:**
- Create: `src/views/admin/hub/settingsCatalog.ts`
- Test: `src/views/admin/hub/__tests__/settingsCatalog.test.ts`

纯数据 + 纯函数，无 Vue 组件 import（这样 auth store 可廉价引入做路由鉴权）。

- [ ] **Step 1: 写失败测试**

`src/views/admin/hub/__tests__/settingsCatalog.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import {
  SETTINGS_CATALOG,
  filterCatalog,
  categoryPerms,
  resolveActiveTab,
} from '../settingsCatalog'

describe('settingsCatalog', () => {
  it('共 7 个分类，tab 总数 16', () => {
    expect(SETTINGS_CATALOG).toHaveLength(7)
    const tabCount = SETTINGS_CATALOG.reduce((n, c) => n + c.tabs.length, 0)
    expect(tabCount).toBe(16)
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
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: FAIL，`Cannot find module '../settingsCatalog'`。

- [ ] **Step 3: 写实现**

`src/views/admin/hub/settingsCatalog.ts`:
```ts
/**
 * 设置中心目录（纯数据，无 Vue 组件依赖）。
 * 7 个领域分类，每个分类含若干 tab；tab 携带权限 key，用于按角色过滤。
 * 组件挂载映射另见 settingsComponents.ts（避免本模块引入组件，便于 auth store 廉价引用）。
 */
export interface SettingsTabMeta {
  /** tab 标识，同时用于路由 ?tab= 与组件注册表 key */
  id: string
  label: string
  /** 所需管理能力 key（admin.*） */
  perm: string
}

export interface SettingsCategoryMeta {
  /** 分类标识，对应路由 /admin/<id> */
  id: string
  label: string
  /** Element Plus 图标组件名 */
  icon: string
  tabs: SettingsTabMeta[]
}

export const SETTINGS_CATALOG: SettingsCategoryMeta[] = [
  { id: 'access', label: '访问控制', icon: 'Lock', tabs: [
    { id: 'users', label: '用户管理', perm: 'admin.users' },
    { id: 'roles', label: '角色权限', perm: 'admin.roles' },
  ] },
  { id: 'structure', label: '结构配置', icon: 'Files', tabs: [
    { id: 'menu', label: '菜单管理', perm: 'admin.menus' },
    { id: 'page-config', label: '页面配置', perm: 'admin.page_configs' },
  ] },
  { id: 'integration', label: '集成对接', icon: 'Link', tabs: [
    { id: 'api-keys', label: 'Open API', perm: 'admin.api_keys' },
    { id: 'webhook', label: 'Webhook', perm: 'admin.webhooks' },
  ] },
  { id: 'ai', label: 'AI 能力', icon: 'MagicStick', tabs: [
    { id: 'ai-settings', label: 'AI 配置', perm: 'admin.ai_settings' },
    { id: 'ai-scan', label: 'AI 定时任务', perm: 'admin.ai_scan' },
  ] },
  { id: 'data-ops', label: '数据运维', icon: 'DataLine', tabs: [
    { id: 'query', label: '数据查询', perm: 'admin.query' },
    { id: 'data-export', label: '数据导出', perm: 'admin.menus' },
    { id: 'etl', label: 'ETL 管理', perm: 'admin.etl_tasks' },
    { id: 'export-scripts', label: '导出脚本', perm: 'admin.export_scripts' },
    { id: 'validation-scripts', label: '校验脚本', perm: 'admin.validation_scripts' },
  ] },
  { id: 'sys-ops', label: '系统运维', icon: 'Monitor', tabs: [
    { id: 'operation-log', label: '操作日志', perm: 'admin.operation_logs' },
    { id: 'backup', label: '系统备份', perm: 'admin.backup' },
  ] },
  { id: 'general', label: '通用设置', icon: 'Setting', tabs: [
    { id: 'system-settings', label: '系统设置', perm: 'admin.system_config' },
  ] },
]

/** 按权限过滤：剔除无权限 tab，再剔除空分类 */
export function filterCatalog(
  can: (key: string) => boolean
): SettingsCategoryMeta[] {
  return SETTINGS_CATALOG
    .map(c => ({ ...c, tabs: c.tabs.filter(t => can(t.perm)) }))
    .filter(c => c.tabs.length > 0)
}

/** 某分类下全部 tab 的权限 key（分类不存在则空数组） */
export function categoryPerms(categoryId: string): string[] {
  const c = SETTINGS_CATALOG.find(x => x.id === categoryId)
  return c ? c.tabs.map(t => t.perm) : []
}

/** 决定当前激活 tab：query 命中则用之，否则取首个；无 tab 返回 '' */
export function resolveActiveTab(
  tabs: Array<{ id: string }>,
  queryTab: string | undefined
): string {
  if (tabs.length === 0) return ''
  if (queryTab && tabs.some(t => t.id === queryTab)) return queryTab
  return tabs[0].id
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: PASS（7 项）。

- [ ] **Step 5: 提交**

```bash
git add src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/__tests__/settingsCatalog.test.ts
git commit -m "feat(settings-hub): 设置中心目录纯数据模块 + 过滤/解析纯函数"
```

---

## Task 2: 异步组件注册表

**Files:**
- Create: `src/views/admin/hub/settingsComponents.ts`

把 tab id 映射到现有管理组件（异步加载）。单独成文件，使 Task 1 的目录保持纯数据。

- [ ] **Step 1: 写实现**

`src/views/admin/hub/settingsComponents.ts`:
```ts
/**
 * 设置中心 tab id → 现有管理页组件（异步）。
 * 这些组件内部逻辑一律复用、不改。
 */
import { defineAsyncComponent, type Component } from 'vue'

export const SETTINGS_TAB_COMPONENTS: Record<string, Component> = {
  users: defineAsyncComponent(() => import('@/views/admin/UserManager.vue')),
  roles: defineAsyncComponent(() => import('@/views/admin/RoleManager.vue')),
  menu: defineAsyncComponent(() => import('@/views/admin/MenuManager.vue')),
  'page-config': defineAsyncComponent(() => import('@/views/admin/PageConfigManager.vue')),
  'api-keys': defineAsyncComponent(() => import('@/views/admin/ApiKeyManager.vue')),
  webhook: defineAsyncComponent(() => import('@/views/admin/WebhookSettings.vue')),
  'ai-settings': defineAsyncComponent(() => import('@/views/admin/AiSettings.vue')),
  'ai-scan': defineAsyncComponent(() => import('@/views/admin/AiScanTaskManager.vue')),
  query: defineAsyncComponent(() => import('@/views/admin/QueryConsole.vue')),
  'data-export': defineAsyncComponent(() => import('@/views/admin/DataMigrationPage.vue')),
  etl: defineAsyncComponent(() => import('@/views/admin/EtlTaskManager.vue')),
  'export-scripts': defineAsyncComponent(() => import('@/views/admin/ExportScriptManager.vue')),
  'validation-scripts': defineAsyncComponent(() => import('@/views/admin/ValidationScriptManager.vue')),
  'operation-log': defineAsyncComponent(() => import('@/views/admin/OperationLog.vue')),
  backup: defineAsyncComponent(() => import('@/views/admin/BackupManager.vue')),
  'system-settings': defineAsyncComponent(() => import('@/views/admin/SystemSettings.vue')),
}
```

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: 无报错（每个路径都指向现有 `.vue`）。

- [ ] **Step 3: 提交**

```bash
git add src/views/admin/hub/settingsComponents.ts
git commit -m "feat(settings-hub): tab→现有管理组件异步注册表"
```

---

## Task 3: 通用右栏容器 CategoryView.vue

**Files:**
- Create: `src/views/admin/hub/CategoryView.vue`
- Test: `src/views/admin/hub/__tests__/CategoryView.test.ts`

读取当前分类（来自路由 `meta.categoryId`），按权限过滤 tab，下划线式 `el-tabs`，`?tab=` 双向记忆，动态挂载组件；单 tab 时隐藏 tab 头。

- [ ] **Step 1: 写实现**

`src/views/admin/hub/CategoryView.vue`:
```vue
<template>
  <div class="category-view">
    <el-tabs
      v-if="visibleTabs.length > 1"
      v-model="activeTab"
      class="hub-tabs"
      @tab-change="onTabChange"
    >
      <el-tab-pane
        v-for="t in visibleTabs"
        :key="t.id"
        :label="t.label"
        :name="t.id"
      />
    </el-tabs>
    <div class="category-content">
      <KeepAlive>
        <component :is="currentComponent" v-if="currentComponent" :key="activeTab" />
      </KeepAlive>
      <el-empty v-if="visibleTabs.length === 0" description="无可用功能" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores'
import { SETTINGS_CATALOG, resolveActiveTab } from './settingsCatalog'
import { SETTINGS_TAB_COMPONENTS } from './settingsComponents'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

/** 当前分类 id（路由 meta 注入；回退到路径末段） */
const categoryId = computed(
  () => (route.meta.categoryId as string) || route.path.split('/')[2] || ''
)

/** 当前分类按权限过滤后的可见 tab */
const visibleTabs = computed(() => {
  const cat = SETTINGS_CATALOG.find(c => c.id === categoryId.value)
  if (!cat) return []
  return cat.tabs.filter(t => auth.can(t.perm))
})

const activeTab = ref('')

/** 同步 activeTab：依据 ?tab= 与可见 tab */
watch(
  [visibleTabs, () => route.query.tab],
  () => {
    activeTab.value = resolveActiveTab(
      visibleTabs.value,
      route.query.tab as string | undefined
    )
  },
  { immediate: true }
)

const currentComponent = computed(
  () => (activeTab.value ? SETTINGS_TAB_COMPONENTS[activeTab.value] : null)
)

function onTabChange(name: string | number): void {
  router.replace({ query: { ...route.query, tab: String(name) } })
}
</script>

<style scoped lang="scss">
.category-view { display: flex; flex-direction: column; height: 100%; }
/* 下划线瘦身式 tab，复用上一轮风格基调 */
.hub-tabs {
  :deep(.el-tabs__header) { margin: 0 0 12px; }
  :deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--el-border-color-lighter); }
}
.category-content { flex: 1; min-height: 0; }
</style>
```

- [ ] **Step 2: 写测试**

`src/views/admin/hub/__tests__/CategoryView.test.ts`:
```ts
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

// stub 子组件注册表，避免拉起真实管理页
vi.mock('../settingsComponents', () => ({
  SETTINGS_TAB_COMPONENTS: {
    users: { template: '<div class="stub-users">U</div>' },
    roles: { template: '<div class="stub-roles">R</div>' },
  },
}))

const routeMock = { meta: { categoryId: 'access' }, path: '/admin/access', query: {} as Record<string, any> }
const replaceMock = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ replace: replaceMock }),
}))

const canRef = ref<(k: string) => boolean>(() => true)
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canRef.value(k) }),
}))

import CategoryView from '../CategoryView.vue'

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any
})

const stubs = {
  'el-tabs': { template: '<div class="el-tabs"><slot /></div>' },
  'el-tab-pane': { template: '<div class="el-tab-pane" :data-name="name">{{ label }}</div>', props: ['name', 'label'] },
  'el-empty': { template: '<div class="el-empty" />' },
}

describe('CategoryView', () => {
  it('超管：access 分类渲染两个 tab，默认挂载首个(users)组件', () => {
    canRef.value = () => true
    routeMock.query = {}
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.findAll('.el-tab-pane')).toHaveLength(2)
    expect(w.find('.stub-users').exists()).toBe(true)
  })

  it('?tab=roles 时挂载 roles 组件', () => {
    canRef.value = () => true
    routeMock.query = { tab: 'roles' }
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.find('.stub-roles').exists()).toBe(true)
  })

  it('仅 admin.users 权限：只剩 1 个 tab，不渲染 tab 头，直接挂 users', () => {
    canRef.value = (k: string) => k === 'admin.users'
    routeMock.query = {}
    const w = mount(CategoryView, { global: { stubs } })
    expect(w.find('.el-tabs').exists()).toBe(false)
    expect(w.find('.stub-users').exists()).toBe(true)
  })
})
```

- [ ] **Step 3: 运行测试**

Run: `npx vitest run src/views/admin/hub/__tests__/CategoryView.test.ts`
Expected: PASS（3 项）。若 mock 路径告警，确认 `vi.mock` 写在 import 之前。

- [ ] **Step 4: 提交**

```bash
git add src/views/admin/hub/CategoryView.vue src/views/admin/hub/__tests__/CategoryView.test.ts
git commit -m "feat(settings-hub): 通用右栏容器 CategoryView（权限过滤 tab + ?tab 记忆 + 动态挂载）"
```

---

## Task 4: 设置中心外壳 SettingsHub.vue

**Files:**
- Create: `src/views/admin/hub/SettingsHub.vue`
- Test: `src/views/admin/hub/__tests__/SettingsHub.test.ts`

左栏分类导航（按权限过滤）+ 顶部标题/面包屑 + 右侧 `<router-view>`。

- [ ] **Step 1: 写实现**

`src/views/admin/hub/SettingsHub.vue`:
```vue
<template>
  <div class="settings-hub">
    <aside class="hub-rail">
      <div class="hub-rail__title">设置中心</div>
      <nav class="hub-rail__nav">
        <RouterLink
          v-for="c in categories"
          :key="c.id"
          :to="`/admin/${c.id}`"
          class="hub-rail__item"
          :class="{ active: activeCategory === c.id }"
        >
          <el-icon><component :is="iconOf(c.icon)" /></el-icon>
          <span>{{ c.label }}</span>
        </RouterLink>
      </nav>
    </aside>
    <section class="hub-main">
      <header class="hub-main__head">
        <span class="hub-crumb">设置中心</span>
        <el-icon class="hub-crumb__sep"><ArrowRight /></el-icon>
        <span class="hub-crumb hub-crumb--current">{{ currentLabel }}</span>
      </header>
      <div class="hub-main__body">
        <router-view />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import * as ElIcons from '@element-plus/icons-vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores'
import { filterCatalog } from './settingsCatalog'

const route = useRoute()
const auth = useAuthStore()

const categories = computed(() => filterCatalog(auth.can))

const activeCategory = computed(
  () => (route.meta.categoryId as string) || route.path.split('/')[2] || ''
)
const currentLabel = computed(
  () => categories.value.find(c => c.id === activeCategory.value)?.label || ''
)

function iconOf(name: string) {
  return (ElIcons as Record<string, any>)[name] || ElIcons.Setting
}
</script>

<style scoped lang="scss">
.settings-hub { display: flex; height: 100%; min-height: 0; }

.hub-rail {
  width: 200px; flex-shrink: 0;
  border-right: 1px solid var(--el-border-color-lighter);
  background: var(--app-shell-bg, #f7f8fa);
  display: flex; flex-direction: column;
}
.hub-rail__title {
  padding: 14px 16px 8px; font-size: 13px; font-weight: 600;
  color: var(--el-text-color-secondary);
}
.hub-rail__nav { display: flex; flex-direction: column; padding: 4px 8px; gap: 2px; }
.hub-rail__item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: 6px;
  font-size: 13px; color: var(--el-text-color-regular);
  text-decoration: none; border-left: 2px solid transparent;
  transition: background-color .15s, color .15s;
  &:hover { background: var(--el-fill-color-light); color: var(--el-text-color-primary); }
  &.active {
    background: var(--app-shell-active-bg, #eceef5);
    color: var(--el-text-color-primary); font-weight: 500;
    border-left-color: var(--el-color-primary);
  }
}

.hub-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.hub-main__head {
  display: flex; align-items: center; gap: 6px;
  padding: 12px 16px; border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px; color: var(--el-text-color-secondary);
}
.hub-crumb__sep { font-size: 12px; }
.hub-crumb--current { color: var(--el-text-color-primary); font-weight: 500; }
.hub-main__body { flex: 1; min-height: 0; overflow: auto; padding: 16px; }

:global(html.dark) .hub-rail { background: var(--app-shell-bg); }
</style>
```

- [ ] **Step 2: 写测试**

`src/views/admin/hub/__tests__/SettingsHub.test.ts`:
```ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

const routeMock = { meta: { categoryId: 'access' }, path: '/admin/access' }
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  RouterLink: { template: '<a><slot /></a>' },
}))

const canRef = ref<(k: string) => boolean>(() => true)
vi.mock('@/stores', () => ({
  useAuthStore: () => ({ can: (k: string) => canRef.value(k) }),
}))

import SettingsHub from '../SettingsHub.vue'

const stubs = {
  RouterLink: { template: '<a class="rail-link"><slot /></a>' },
  'router-view': { template: '<div class="rv" />' },
  'el-icon': { template: '<i><slot /></i>' },
}

describe('SettingsHub', () => {
  it('超管：左栏渲染 7 个分类链接', () => {
    canRef.value = () => true
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(7)
  })

  it('仅 admin.users：左栏只剩访问控制 1 类', () => {
    canRef.value = (k: string) => k === 'admin.users'
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(1)
    expect(w.text()).toContain('访问控制')
  })

  it('无任何管理权限：左栏 0 个分类', () => {
    canRef.value = () => false
    const w = mount(SettingsHub, { global: { stubs } })
    expect(w.findAll('.rail-link')).toHaveLength(0)
  })
})
```

- [ ] **Step 3: 运行测试**

Run: `npx vitest run src/views/admin/hub/__tests__/SettingsHub.test.ts`
Expected: PASS（3 项）。

- [ ] **Step 4: 提交**

```bash
git add src/views/admin/hub/SettingsHub.vue src/views/admin/hub/__tests__/SettingsHub.test.ts
git commit -m "feat(settings-hub): 外壳 SettingsHub（左栏分类按权限过滤 + 面包屑 + router-view）"
```

---

## Task 5: 路由改造 + 旧路径重定向 + 鉴权

**Files:**
- Modify: `src/router/index.ts`
- Modify: `src/stores/auth.ts`（`hasRoutePermission` 增加分类路径处理）
- Test: `src/router/__tests__/settingsHubRoutes.test.ts`

把 16 个旧 `admin/*` 路由（除 trigger-rules / dependency-manager / factory-reset 保留原样）替换为：`/admin` 父路由（SettingsHub）+ 7 个分类子路由（CategoryView）+ 15 条旧路径 → 新「分类?tab」重定向。

- [ ] **Step 1: 改 `src/router/index.ts`**

在 `staticRoutes` 的布局 `children` 数组里：
1. **删除**这 15 个现有子路由块：`admin/menu`、`admin/page-config`、`admin/users`、`admin/roles`、`admin/ai-scan-tasks`、`admin/operation-log`、`admin/backup`、`admin/export-scripts`、`admin/api-keys`、`admin/validation-scripts`、`admin/etl-tasks`、`admin/query`、`admin/ai-settings`、`admin/webhook-settings`、`admin/system-settings`。
   **保留**：`admin/menu-export`（改为重定向，见下）、`admin/trigger-rules`、`admin/dependency-manager`、`admin/factory-reset`、`dashboard/:id?`、`home`、`ai-chat`、`page/:pageId`。
2. **新增** `/admin` 父路由与 7 子路由（插入到 `page/:pageId` 之后、`dashboard` 之前任意位置）：
```ts
      // 设置中心（管理控制台）：左栏分类 + 右侧 tab 容器
      {
        path: 'admin',
        component: () => import('@/views/admin/hub/SettingsHub.vue'),
        redirect: '/admin/access',
        children: [
          { path: 'access', name: 'SettingsAccess', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '访问控制', categoryId: 'access' } },
          { path: 'structure', name: 'SettingsStructure', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '结构配置', categoryId: 'structure' } },
          { path: 'integration', name: 'SettingsIntegration', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '集成对接', categoryId: 'integration' } },
          { path: 'ai', name: 'SettingsAi', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: 'AI 能力', categoryId: 'ai' } },
          { path: 'data-ops', name: 'SettingsDataOps', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '数据运维', categoryId: 'data-ops' } },
          { path: 'sys-ops', name: 'SettingsSysOps', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '系统运维', categoryId: 'sys-ops' } },
          { path: 'general', name: 'SettingsGeneral', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '通用设置', categoryId: 'general' } },
        ],
      },
```
3. **新增** 15 条旧路径重定向（紧接上面，仍在布局 `children` 内）：
```ts
      // 旧管理路径 → 设置中心（保深链/书签）
      { path: 'admin/users', redirect: '/admin/access?tab=users' },
      { path: 'admin/roles', redirect: '/admin/access?tab=roles' },
      { path: 'admin/menu', redirect: '/admin/structure?tab=menu' },
      { path: 'admin/page-config', redirect: '/admin/structure?tab=page-config' },
      { path: 'admin/api-keys', redirect: '/admin/integration?tab=api-keys' },
      { path: 'admin/webhook-settings', redirect: '/admin/integration?tab=webhook' },
      { path: 'admin/ai-settings', redirect: '/admin/ai?tab=ai-settings' },
      { path: 'admin/ai-scan-tasks', redirect: '/admin/ai?tab=ai-scan' },
      { path: 'admin/query', redirect: '/admin/data-ops?tab=query' },
      { path: 'admin/menu-export', redirect: '/admin/data-ops?tab=data-export' },
      { path: 'admin/etl-tasks', redirect: '/admin/data-ops?tab=etl' },
      { path: 'admin/export-scripts', redirect: '/admin/data-ops?tab=export-scripts' },
      { path: 'admin/validation-scripts', redirect: '/admin/data-ops?tab=validation-scripts' },
      { path: 'admin/operation-log', redirect: '/admin/sys-ops?tab=operation-log' },
      { path: 'admin/backup', redirect: '/admin/sys-ops?tab=backup' },
      { path: 'admin/system-settings', redirect: '/admin/general' },
```
（注意：原 `admin/menu-export` 子路由块被这条重定向取代；`MenuExport` 命名路由不再需要。`trigger-rules`/`dependency-manager`/`factory-reset` 块原样保留。）

- [ ] **Step 2: 改 `src/stores/auth.ts` 的 `hasRoutePermission`**

在文件顶部 import 区加：
```ts
import { filterCatalog, categoryPerms } from '@/views/admin/hub/settingsCatalog'
```
在 `hasRoutePermission` 内，把 `/home`、`/` 放行之后、`ADMIN_PATH_PERMISSION` 查询之前，插入分类路径处理：
```ts
    // 设置中心：父路由按"是否有任一可见分类"放行；分类路由按该类是否有可见 tab
    if (path === '/admin') return filterCatalog(can).length > 0
    if (path.startsWith('/admin/')) {
      const seg = path.split('/')[2]
      const perms = categoryPerms(seg)
      if (perms.length) return perms.some(k => can(k))
      // seg 非分类 id（如 users/menu 等旧段或 factory-reset/trigger-rules）→ 落到下方 ADMIN_PATH_PERMISSION
    }
```
（`categoryPerms('users')` 返回 `[]` → 不拦截，继续走既有 `ADMIN_PATH_PERMISSION['/admin/users']` 逻辑，旧路径重定向仍受原 key 守卫。）

- [ ] **Step 3: 写测试**

`src/router/__tests__/settingsHubRoutes.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'

// 仅验证重定向表与父子结构，使用最小 stub 组件
const Stub = { template: '<div />' }
const routes = [
  { path: '/admin', component: Stub, redirect: '/admin/access', children: [
    { path: 'access', component: Stub, meta: { categoryId: 'access' } },
    { path: 'data-ops', component: Stub, meta: { categoryId: 'data-ops' } },
    { path: 'general', component: Stub, meta: { categoryId: 'general' } },
  ] },
  { path: '/admin/users', redirect: '/admin/access?tab=users' },
  { path: '/admin/query', redirect: '/admin/data-ops?tab=query' },
  { path: '/admin/system-settings', redirect: '/admin/general' },
]

describe('设置中心路由', () => {
  it('/admin 重定向到 /admin/access', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin'); await r.isReady()
    expect(r.currentRoute.value.path).toBe('/admin/access')
  })
  it('旧 /admin/users → /admin/access?tab=users', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin/users')
    expect(r.currentRoute.value.path).toBe('/admin/access')
    expect(r.currentRoute.value.query.tab).toBe('users')
  })
  it('旧 /admin/system-settings → /admin/general', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/admin/system-settings')
    expect(r.currentRoute.value.path).toBe('/admin/general')
  })
})
```

- [ ] **Step 4: 运行 + 全量回归**

Run: `npx vitest run src/router/__tests__/settingsHubRoutes.test.ts`
Expected: PASS（3 项）。
Run: `npx vue-tsc --noEmit`
Expected: 无报错。
Run: `npx vitest run src/`
Expected: 全绿（既有 + 新增）。

- [ ] **Step 5: 提交**

```bash
git add src/router/index.ts src/stores/auth.ts src/router/__tests__/settingsHubRoutes.test.ts
git commit -m "feat(settings-hub): /admin 父路由 + 7 分类子路由 + 旧路径重定向 + 分类鉴权"
```

---

## Task 6: 菜单数据（种子 + 幂等迁移）

**Files:**
- Modify: `server/seed_data.py`（`MENUS`）
- Create: `server/migrations/2026_06_13_settings_hub_menu.py`
- Test: `server/tests/test_settings_hub_menu_migration.py`

侧边栏由 DB 菜单渲染，需把原两棵树塌缩为单个「设置中心」。

- [ ] **Step 1: 改 `server/seed_data.py`**

在 `MENUS` 列表中：删除 `menu-3-b`（数据工具）及其子项（`menu-3-6`、`menu-3-8`、`menu-3-16`）、`menu-3`（系统配置）及其全部后代（`menu-3-a`、`menu-3-1..3,7,11,15`、`menu-3-c`、`menu-3-4,5` 等）。新增一条（放在原系统配置位置，`order` 取 4）：
```python
    {"id": "menu-settings", "name": "设置中心", "icon": "Setting", "pageId": None, "parentId": None, "order": 4, "path": "/admin", "roles": ["admin"]},
```

- [ ] **Step 2: 写幂等迁移脚本**

`server/migrations/2026_06_13_settings_hub_menu.py`:
```python
"""幂等迁移：把"数据工具"+"系统配置"菜单子树塌缩为单一"设置中心"。

可重复执行：每次先删旧子树，再 upsert 设置中心菜单。
用法：python -m server.migrations.2026_06_13_settings_hub_menu
"""
from utils.db import get_db

# 旧两棵树的根 id（其后代按 parent_id 递归删除）
OLD_ROOT_IDS = ["menu-3", "menu-3-b"]
SETTINGS_MENU = {
    "id": "menu-settings", "name": "设置中心", "icon": "Setting",
    "page_id": None, "parent_id": None, "order": 4,
    "path": "/admin", "roles": ["admin"],
}


def _collect_descendants(cur, root_ids):
    """广度优先收集 root_ids 及其全部后代 id。"""
    to_delete, frontier = set(root_ids), list(root_ids)
    while frontier:
        cur.execute("SELECT id FROM menus WHERE parent_id = ANY(%s)", (frontier,))
        children = [r[0] for r in cur.fetchall()]
        new = [c for c in children if c not in to_delete]
        to_delete.update(new)
        frontier = new
    return list(to_delete)


def run():
    import json
    with get_db() as conn:
        cur = conn.cursor()
        ids = _collect_descendants(cur, OLD_ROOT_IDS)
        if ids:
            cur.execute("DELETE FROM menus WHERE id = ANY(%s)", (ids,))
        # upsert 设置中心
        cur.execute("DELETE FROM menus WHERE id = %s", (SETTINGS_MENU["id"],))
        cur.execute(
            """INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (SETTINGS_MENU["id"], SETTINGS_MENU["name"], SETTINGS_MENU["icon"],
             SETTINGS_MENU["page_id"], SETTINGS_MENU["parent_id"], SETTINGS_MENU["order"],
             SETTINGS_MENU["path"], json.dumps(SETTINGS_MENU["roles"])),
        )
        conn.commit()
    return {"deleted": ids, "inserted": SETTINGS_MENU["id"]}


if __name__ == "__main__":
    print(run())
```
（注：`menus.roles` 为 JSONB，按本仓库既有写法用 `json.dumps`；若本仓库 `menus` 表列名/类型不同，以实际 schema 为准——实现时先 `\d menus` 确认 `roles`/`"order"` 列。）

- [ ] **Step 3: 写测试**

`server/tests/test_settings_hub_menu_migration.py`:
```python
import importlib

mig = importlib.import_module("migrations.2026_06_13_settings_hub_menu")


def test_migration_idempotent(db_conn):
    """运行两次，结果一致：无旧两棵树、存在唯一设置中心。"""
    mig.run()
    first = mig.run()  # 第二次不应报错
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM menus WHERE id IN ('menu-3','menu-3-b')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM menus WHERE id = 'menu-settings'")
        assert cur.fetchone()[0] == 1
    assert first["inserted"] == "menu-settings"
```
（若 `server/tests/conftest.py` 无 `db_conn` fixture，则改用本仓库既有的 DB fixture 名——实现时先看 `server/tests/conftest.py` 暴露了什么。）

- [ ] **Step 4: 运行测试**

Run（Windows，匹配 `npm run test:server` 约定）：
```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_settings_hub_menu_migration.py -v
```
Expected: PASS。若依赖真实库连接，按本仓库测试约定（`server/config.py` 指向 `casemanage`）准备测试库。

- [ ] **Step 5: 应用迁移到本地运行库（开发可见）**

```bash
cd server && python -m migrations.2026_06_13_settings_hub_menu
```
Expected: 打印 `{'deleted': [...], 'inserted': 'menu-settings'}`。

- [ ] **Step 6: 复核侧边栏静态过滤**

确认 `src/api/menu.ts` 与 `src/stores/menu.ts` 不会把 `path:/admin` 的「设置中心」菜单当作"静态页面"过滤掉侧边栏渲染（`getAvailableExportMenus` 的过滤仅作用于导出选单，不影响侧边栏；如发现侧边栏渲染另有白/黑名单，按需放行 `menu-settings`）。

- [ ] **Step 7: 提交**

```bash
git add server/seed_data.py server/migrations/2026_06_13_settings_hub_menu.py server/tests/test_settings_hub_menu_migration.py
git commit -m "feat(settings-hub): 菜单种子塌缩为设置中心 + 幂等迁移脚本"
```

---

## Task 7: 视觉验证 + 全量回归

**Files:** 无代码改动（除非验证发现问题）

- [ ] **Step 1: 起本地服务**

worktree 的 `npm run dev`（前序会话已在 `http://localhost:5173`）；后端按需 `npm run server`。确认已执行 Task 6 Step 5 的迁移，侧边栏出现「设置中心」。

- [ ] **Step 2: Playwright 截图核对**

导航并逐一核对：
1. 侧边栏顶级菜单只剩单一「设置中心」（原"数据工具""系统配置"两棵树消失）。
2. 进入 `/admin`：自动落到「访问控制」；左栏 7 个分类、激活态左缘主色条；右侧「用户管理 | 角色权限」下划线 tab。
3. 切到「数据运维」：右侧 5 个 tab（数据查询/数据导出/ETL/导出脚本/校验脚本），切换不整页跳转。
4. 「通用设置」：单 tab、无 tab 头，直接渲染系统设置。
5. 深链：直接访问 `/admin/data-ops?tab=etl` 命中 ETL；旧 `/admin/users` 重定向到 `/admin/access?tab=users`。
6. 暗色模式、紧凑模式下骨架正常。

- [ ] **Step 3: 全量回归**

Run: `npx vitest run src/`
Expected: 全绿。
Run: `npx vue-tsc --noEmit`
Expected: 无报错。
（如本地具备后端测试环境）Run: `npm run test:server` Expected: 全绿。

- [ ] **Step 4: 提交（若有微调）**

```bash
git add -A
git commit -m "fix(settings-hub): 视觉验证微调"
```
（无微调则跳过。）

---

## Self-Review（作者自检）

**Spec 覆盖：**
- 架构（SettingsHub 外壳 + 复用现有页）→ Task 4 + Task 3，✓
- 7 类领域导航 + tab 合并（按全表）→ Task 1 目录 + Task 3 容器 + Task 4 外壳，✓
- 路由收编 `/admin` + 旧路径重定向 → Task 5，✓
- 菜单数据塌缩为单一「设置中心」+ 幂等迁移 → Task 6，✓
- 页面样式/密度（左栏紧凑、下划线 tab、统一骨架、密度令牌、暗色）→ Task 3/4 的 `<style>`（外壳与 tab 已含；右侧内容沿用全局 `html.compact-mode` 令牌，无需逐页改），✓
- RBAC 保持（逐 tab 权限 key、无权限隐藏、分类路由鉴权）→ Task 1 `filterCatalog`/`categoryPerms` + Task 3/4 过滤 + Task 5 `hasRoutePermission`，✓
- 不在范围（不改 16 页内部、不动 trigger-rules/dependency-manager、不改后端逻辑）→ Task 2 仅注册、Task 5 明确保留两路由，✓

**占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码。两处"以实际 schema/fixture 为准"是**核对指令**（明确要看哪个文件、确认什么），非占位。

**类型/命名一致性：** `SETTINGS_CATALOG`/`filterCatalog`/`categoryPerms`/`resolveActiveTab`（Task 1）、`SETTINGS_TAB_COMPONENTS`（Task 2）、分类 id 与 tab id（全表）在 Task 3/4/5 引用一致；旧路径→`?tab=` 的 tab id 与目录 tab id 完全对应（users/roles/menu/page-config/api-keys/webhook/ai-settings/ai-scan/query/data-export/etl/export-scripts/validation-scripts/operation-log/backup/system-settings）。`categoryId` 经路由 `meta` 注入、被 CategoryView 与 SettingsHub 同名读取。

**潜在风险（实现时留意）：**
- `menus` 表 `roles`/`"order"` 列名与 JSONB 写法以实际 schema 为准（Task 6 Step 2 已标注先 `\d menus`）。
- 后端测试 fixture 名以 `server/tests/conftest.py` 实际为准（Task 6 Step 3 已标注）。
- auth store import 目录模块（`@/views/admin/hub/settingsCatalog`）为纯数据、无组件 import，不引入循环/重依赖。
