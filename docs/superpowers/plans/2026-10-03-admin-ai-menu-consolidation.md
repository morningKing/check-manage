# 后台 AI 能力菜单合并精简 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 设置中心「AI 能力」组 8 个条目合并为 4 个域入口（页内 tab），7 个现有视图组件零改动，5 条旧路径重定向不断链。

**Architecture:** `settingsCatalog.ts`（菜单/路由/权限唯一真源）扩展 `perm: string|string[]`（any-of）与 `tabs` 声明；新增通用 `SettingsTabShell.vue`（读 catalog tabs + 权限过滤 + query 深链）+ 3 个两行壳包装；路由生成器消费 `SETTINGS_REDIRECTS`；`auth.hasRoutePermission` 支持 any-of。

**Tech Stack:** Vue 3 + vue-router 4 + Element Plus（el-tabs）+ Pinia + vitest + Playwright。

**Spec:** `docs/superpowers/specs/2026-10-03-admin-ai-menu-consolidation-design.md`

## Global Constraints

- 7 个现有视图组件（AiSettings/AiOpencodeRuntime/AiSkillManager/AiSkillOpt/AiBatchAdmin/AiSessionAdmin/AiOrchestrationManager）**文件内容零改动**。
- 权限 key 全部沿用现值：`admin.ai_settings` / `admin.ai_runtime_read` / `admin.ai_chat_admin` / `admin.ai_orchestration_admin` / `admin.ai_scan`；后端零改动。
- tab id 契约（spec §1）：`ai-settings`=`model`/`runtime`，`ai-skills`=`square`/`fit`，`ai-execution`=`batches`/`sessions`/`orchestrations`。
- catalog 是唯一真源：tab 清单、重定向表都只声明在 `settingsCatalog.ts`，壳组件与路由生成器从它派生，不硬编码。
- 现有测试基线：`settingsCatalog.test.ts` 断言「7 组 26 条」→ 合并后 **7 组 22 条**（-5 旧 id +1 ai-execution）。
- 前端测试命令：`npm run test`（vitest）；e2e：`npx playwright test <spec>`。

---

### Task 1: catalog schema 扩展——any-of 权限 + tabs 类型（不改数据）

**Files:**
- Modify: `src/views/admin/hub/settingsCatalog.ts`
- Test: `src/views/admin/hub/__tests__/settingsCatalog.test.ts`

**Interfaces:**
- Produces: `interface SettingsTab { id; label; perm: string; component }`；`SettingsItem.perm: string | string[]`、`SettingsItem.tabs?: SettingsTab[]`；`itemPerms(perm): string[]`；`canItem(perm, can): boolean`（`filterGroups` 改用）。后续 Task 2/3/4 全部依赖这两个函数名。

- [ ] **Step 1: 写失败测试**（追加到 `settingsCatalog.test.ts` 末尾）

```ts
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: FAIL —— `itemPerms` 未导出

- [ ] **Step 3: 最小实现**（settingsCatalog.ts）

```ts
export interface SettingsTab {
  id: string
  label: string
  perm: string
  component: () => Promise<Component>
}

export interface SettingsItem {
  // ...既有字段不动，仅扩两处：
  perm: string | string[]
  /** 页内 tab（域入口条目用）；不声明即纯单页条目 */
  tabs?: SettingsTab[]
}

export function itemPerms(perm: string | string[]): string[] {
  return Array.isArray(perm) ? perm : [perm]
}

export function canItem(perm: string | string[], can: (key: string) => boolean): boolean {
  return itemPerms(perm).some(can)
}
```

`filterGroups` 内过滤改为一行：

```ts
items: g.items.filter(i => canItem(i.perm, can)),
```

- [ ] **Step 4: 既有断言适配**——`'每条都有非空 label / perm / icon / component'` 里 perm 正则改为遍历归一化：

```ts
for (const p of itemPerms(it.perm)) expect(p, it.id).toMatch(/^admin\./)
```

- [ ] **Step 5: 跑全文件确认绿**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: PASS（数据未动，仍 26 条）

- [ ] **Step 6: Commit**

```bash
git add src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/__tests__/settingsCatalog.test.ts
git commit -m "feat(settings-hub): catalog schema 支持 any-of 权限与页内 tab 声明"
```

---

### Task 2: SettingsTabShell 通用页签壳 + 3 个域壳包装

**Files:**
- Create: `src/views/admin/hub/SettingsTabShell.vue`
- Create: `src/views/admin/hub/AiConfigHub.vue`、`AiSkillHub.vue`、`AiExecutionHub.vue`
- Test: `src/views/admin/hub/__tests__/SettingsTabShell.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `findSettingsItem` / `canItem`；`useAuthStore().can`。
- Produces: `<SettingsTabShell item-id="ai-settings" />`——被 3 个壳包装引用，Task 3 的 catalog `component` 字段指向这 3 个壳。

- [ ] **Step 1: 写失败测试**（mock catalog 与 auth，注入 fake tabs）

```ts
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/views/admin/hub/settingsCatalog', async (orig) => {
  const actual = await orig<typeof import('@/views/admin/hub/settingsCatalog')>()
  return {
    ...actual,
    findSettingsItem: (id: string) => id === 'ai-settings' ? {
      id, label: 'AI 配置',
      tabs: [
        { id: 'model', label: '模型与密钥', perm: 'admin.ai_settings',
          component: () => Promise.resolve({ template: '<div class="pane-model"/>' }) },
        { id: 'runtime', label: '运行时', perm: 'admin.ai_runtime_read',
          component: () => Promise.resolve({ template: '<div class="pane-runtime"/>' }) },
      ],
    } : undefined,
  }
})
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ can: (k: string) => k === 'admin.ai_settings' }),
}))

import SettingsTabShell from '../SettingsTabShell.vue'

function makeRouter() {
  return createRouter({ history: createMemoryHistory(),
    routes: [{ path: '/admin/:id', component: { template: '<div/>' } }] })
}

async function mountShell(query = {}) {
  setActivePinia(createPinia())
  const router = makeRouter()
  router.push({ path: '/admin/ai-settings', query }); await router.isReady()
  const w = mount(SettingsTabShell, { props: { itemId: 'ai-settings' },
    global: { plugins: [router] } })
  await flushPromises()
  return w
}

describe('SettingsTabShell', () => {
  it('无权限的 tab 不渲染', async () => {
    const w = await mountShell()                    // 只有 ai_settings
    expect(w.text()).toContain('模型与密钥')
    expect(w.text()).not.toContain('运行时')
  })
  it('?tab= 定位对应页签', async () => {
    const w = await mountShell({ tab: 'model' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('模型与密钥')
  })
  it('非法 tab 回退首个可见 tab 并回写 query', async () => {
    const w = await mountShell({ tab: 'bogus' })
    expect((w.find('.el-tabs__item.is-active').element as HTMLElement).textContent).toContain('模型与密钥')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/views/admin/hub/__tests__/SettingsTabShell.test.ts`
Expected: FAIL —— `SettingsTabShell.vue` 不存在

- [ ] **Step 3: 实现 SettingsTabShell.vue**

```vue
<template>
  <el-tabs v-if="visibleTabs.length" v-model="activeTab" class="settings-tab-shell">
    <el-tab-pane v-for="t in visibleTabs" :key="t.id" :name="t.id" :label="t.label" lazy>
      <keep-alive>
        <component :is="components[t.id]" />
      </keep-alive>
    </el-tab-pane>
  </el-tabs>
  <el-empty v-else description="没有可用的功能页签" />
</template>

<script setup lang="ts">
import { computed, ref, watch, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { findSettingsItem } from '@/views/admin/hub/settingsCatalog'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ itemId: string }>()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tabs = computed(() => findSettingsItem(props.itemId)?.tabs ?? [])
const visibleTabs = computed(() => tabs.value.filter(t => auth.can(t.perm)))

// defineAsyncComponent 包装必须对同一 tab 恒定（setup 期一次构建），
// 否则 keep-alive 永远命中不了缓存、切 tab 即重挂载重取数。
const components: Record<string, ReturnType<typeof defineAsyncComponent>> = {}
for (const t of tabs.value) components[t.id] = defineAsyncComponent(t.component)

function normalize(tab: unknown): string {
  const id = typeof tab === 'string' ? tab : ''
  if (visibleTabs.value.some(t => t.id === id)) return id
  return visibleTabs.value[0]?.id ?? ''
}
const activeTab = ref(normalize(route.query.tab))

// 浏览器前进/后退或外部跳转带 ?tab= 时跟随
watch(() => route.query.tab, v => { activeTab.value = normalize(v) })
// 页签切换回写地址栏（replace 不留历史），保证深链可收藏
watch(activeTab, v => {
  if (route.query.tab !== v) router.replace({ query: { ...route.query, tab: v } })
})
</script>
```

- [ ] **Step 4: 3 个壳包装**（每个仅此内容，itemId 不同）

```vue
<!-- AiConfigHub.vue -->
<template>
  <SettingsTabShell item-id="ai-settings" />
</template>
<script setup lang="ts">
import SettingsTabShell from './SettingsTabShell.vue'
</script>
```

`AiSkillHub.vue` → `item-id="ai-skills"`；`AiExecutionHub.vue` → `item-id="ai-execution"`。

- [ ] **Step 5: 跑测试确认绿**

Run: `npx vitest run src/views/admin/hub/__tests__/SettingsTabShell.test.ts`
Expected: PASS（3 例）

- [ ] **Step 6: Commit**

```bash
git add src/views/admin/hub/SettingsTabShell.vue src/views/admin/hub/AiConfigHub.vue src/views/admin/hub/AiSkillHub.vue src/views/admin/hub/AiExecutionHub.vue src/views/admin/hub/__tests__/SettingsTabShell.test.ts
git commit -m "feat(settings-hub): SettingsTabShell 通用页签壳——权限过滤+query 深链+keep-alive 懒挂载"
```

---

### Task 3: AI 组条目重定义 8→4 + SETTINGS_REDIRECTS

**Files:**
- Modify: `src/views/admin/hub/settingsCatalog.ts`（AI 组 items + 新增 `SETTINGS_REDIRECTS`）
- Test: `src/views/admin/hub/__tests__/settingsCatalog.test.ts`

**Interfaces:**
- Consumes: Task 1 schema、Task 2 的 3 个壳组件路径。
- Produces: `SETTINGS_REDIRECTS: Record<string, { path: string; tab: string }>`（Task 4 路由生成器消费）；条目 id `ai-execution`；5 个旧 id 从条目中移除。

- [ ] **Step 1: 写失败测试**（settingsCatalog.test.ts 追加 + 改造）

```ts
import { SETTINGS_REDIRECTS } from '../settingsCatalog'

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
    expect(Object.keys(SETTINGS_REDIRECTS).sort())
      .toEqual(['ai-batches', 'ai-orchestrations', 'ai-opencode', 'ai-sessions', 'ai-skillopt'])
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
```

并把既 `'共 7 组 26 条'` 用例改为 `toHaveLength(22)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: FAIL —— 仍 26 条、无 SETTINGS_REDIRECTS

- [ ] **Step 3: 重写 AI 组**（settingsCatalog.ts）

```ts
{ id: 'ai', label: 'AI 能力', icon: 'MagicStick', items: [
  { id: 'ai-settings', label: 'AI 配置', perm: ['admin.ai_settings', 'admin.ai_runtime_read'], icon: 'Setting',
    component: () => import('@/views/admin/hub/AiConfigHub.vue'),
    tabs: [
      { id: 'model', label: '模型与密钥', perm: 'admin.ai_settings',
        component: () => import('@/views/admin/AiSettings.vue') },
      { id: 'runtime', label: '运行时', perm: 'admin.ai_runtime_read',
        component: () => import('@/views/admin/AiOpencodeRuntime.vue') },
    ] },
  { id: 'ai-scan', label: 'AI 定时巡检', perm: 'admin.ai_scan', icon: 'Timer',
    component: () => import('@/views/admin/AiScanTaskManager.vue') },
  { id: 'ai-skills', label: 'AI 技能', perm: ['admin.ai_settings', 'admin.ai_chat_admin'], icon: 'MagicStick',
    component: () => import('@/views/admin/hub/AiSkillHub.vue'),
    tabs: [
      { id: 'square', label: '技能广场', perm: 'admin.ai_settings',
        component: () => import('@/views/admin/AiSkillManager.vue') },
      { id: 'fit', label: '拟合优化', perm: 'admin.ai_chat_admin',
        component: () => import('@/views/admin/AiSkillOpt.vue') },
    ] },
  { id: 'ai-execution', label: 'AI 执行中心', perm: ['admin.ai_chat_admin', 'admin.ai_orchestration_admin'], icon: 'Tickets',
    component: () => import('@/views/admin/hub/AiExecutionHub.vue'),
    tabs: [
      { id: 'batches', label: '批量执行', perm: 'admin.ai_chat_admin',
        component: () => import('@/views/admin/AiBatchAdmin.vue') },
      { id: 'sessions', label: '会话审计', perm: 'admin.ai_chat_admin',
        component: () => import('@/views/admin/AiSessionAdmin.vue') },
      { id: 'orchestrations', label: '编排管理', perm: 'admin.ai_orchestration_admin',
        component: () => import('@/views/admin/AiOrchestrationManager.vue') },
    ] },
] },
```

文件尾部追加：

```ts
/**
 * 合并前的 AI 旧条目路径 → 域入口对应 tab。
 * 与 LEGACY_PATH_ALIASES 的差异：目标不是「条目 id」而是「条目内 tab」，故
 * 单独一张表、由 settingsRoutes 消费为带 query 的重定向。
 */
export const SETTINGS_REDIRECTS: Record<string, { path: string; tab: string }> = {
  'ai-opencode': { path: '/admin/ai-settings', tab: 'runtime' },
  'ai-skillopt': { path: '/admin/ai-skills', tab: 'fit' },
  'ai-batches': { path: '/admin/ai-execution', tab: 'batches' },
  'ai-sessions': { path: '/admin/ai-execution', tab: 'sessions' },
  'ai-orchestrations': { path: '/admin/ai-execution', tab: 'orchestrations' },
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `npx vitest run src/views/admin/hub/__tests__/settingsCatalog.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/__tests__/settingsCatalog.test.ts
git commit -m "feat(settings-hub): AI 组 8 条目并 4 域入口（配置/技能/执行中心 tab 化）+ 旧路径重定向表"
```

---

### Task 4: 路由重定向消费 + 守卫 any-of

**Files:**
- Modify: `src/router/settingsRoutes.ts`、`src/stores/auth.ts`（`hasRoutePermission` 内约 132-135 行）
- Test: `src/router/__tests__/settingsHubRoutes.test.ts`、`src/stores/__tests__/authRoutePermission.test.ts`

**Interfaces:**
- Consumes: Task 3 `SETTINGS_REDIRECTS`；Task 1 `canItem`/`itemPerms`。
- Produces: 5 条 `/admin/<旧id>` → `{ path, query: { tab } }` 路由；`meta.perm` 支持 `string | string[]`。

- [ ] **Step 1: 写失败测试**

settingsHubRoutes.test.ts 追加：

```ts
describe('AI 旧路径重定向', () => {
  it.each([
    ['/admin/ai-opencode', '/admin/ai-settings', 'runtime'],
    ['/admin/ai-skillopt', '/admin/ai-skills', 'fit'],
    ['/admin/ai-batches', '/admin/ai-execution', 'batches'],
    ['/admin/ai-sessions', '/admin/ai-execution', 'sessions'],
    ['/admin/ai-orchestrations', '/admin/ai-execution', 'orchestrations'],
  ])('%s → %s?tab=%s', async (from, path, tab) => {
    const r = makeRouter(() => true)
    await r.push(from)
    expect(r.currentRoute.value.path).toBe(path)
    expect(r.currentRoute.value.query).toMatchObject({ tab })
  })
})

describe('域入口路由', () => {
  it('ai-execution meta.perm 为 any-of 数组', () => {
    const r = buildSettingsRoutes().find(x => x.path === '/admin/ai-execution')!
    expect(r.meta?.perm).toEqual(['admin.ai_chat_admin', 'admin.ai_orchestration_admin'])
  })
})
```

authRoutePermission.test.ts 追加：

```ts
it('/admin/<域入口> 任一权限放行', () => {
  const auth = makeAuth(['admin.ai_orchestration_admin'])
  expect(auth.hasRoutePermission('/admin/ai-execution')).toBe(true)
})
it('/admin/<域入口> 全无权限拒绝', () => {
  const auth = makeAuth(['admin.users'])
  expect(auth.hasRoutePermission('/admin/ai-execution')).toBe(false)
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/router/__tests__/settingsHubRoutes.test.ts src/stores/__tests__/authRoutePermission.test.ts`
Expected: FAIL —— 旧路径 push 后无匹配路由；meta.perm 非数组；any-of 拒绝

- [ ] **Step 3: settingsRoutes.ts 消费重定向**（`buildSettingsRedirects` 内、LEGACY 循环之前）

```ts
import { SETTINGS_REDIRECTS } from '@/views/admin/hub/settingsCatalog'  // 并入既有 import

for (const [alias, target] of Object.entries(SETTINGS_REDIRECTS)) {
  routes.push({
    path: `/admin/${alias}`,
    redirect: () => ({ path: target.path, query: { tab: target.tab } }),
  })
}
```

- [ ] **Step 4: auth.ts any-of**（`hasRoutePermission` 的设置中心分支替换）

```ts
if (path.startsWith('/admin/')) {
  const raw = meta?.perm
  const metaPerm: string | string[] | undefined =
    Array.isArray(raw) ? (raw as string[]) : typeof raw === 'string' ? raw : undefined
  const perm = metaPerm ?? findSettingsItem(path.split('/')[2])?.perm
  if (perm) return Array.isArray(perm) ? perm.some(k => can(k)) : can(perm)
  // 非条目 id（分组 id / 老路径别名）→ 由路由重定向处理，这里落到下方兜底
}
```

- [ ] **Step 5: 跑两个测试文件确认绿**

Run: `npx vitest run src/router/__tests__/settingsHubRoutes.test.ts src/stores/__tests__/authRoutePermission.test.ts`
Expected: PASS（注意 settingsHubRoutes 既有「生成 22 条路由」用例引用 `ALL_SETTINGS_ITEMS.length`，动态自适应无需改）

- [ ] **Step 6: 前端全量单测回归**

Run: `npm run test`
Expected: 全绿（若 auth.test.ts 有按条目 perm 断言的用例，一并按 any-of 适配）

- [ ] **Step 7: Commit**

```bash
git add src/router/settingsRoutes.ts src/stores/auth.ts src/router/__tests__/settingsHubRoutes.test.ts src/stores/__tests__/authRoutePermission.test.ts
git commit -m "feat(settings-hub): 路由消费 AI 旧路径重定向 + 守卫支持 any-of 权限"
```

---

### Task 5: e2e 导航冒烟 + 相关回归 + 文档

**Files:**
- Create: `e2e/ai-full/ai-admin-nav.spec.ts`
- Modify: `docs/user-guide/` 中引用 `/admin/ai-orchestrations`、`/admin/ai-skillopt` 等旧路径的文档措辞（重定向生效后链接不断，仅更新表述与截图路径说明）

- [ ] **Step 1: 写 e2e**（沿用 `e2e/ai-full/helpers.ts` 的 `gotoWithAuth`）

```ts
import { test, expect } from '@playwright/test'
import { gotoWithAuth } from './helpers'

test.setTimeout(120_000)

const CASES = [
  ['/admin/ai-batches', '/admin/ai-execution', 'batches', '批量执行'],
  ['/admin/ai-sessions', '/admin/ai-execution', 'sessions', '会话审计'],
  ['/admin/ai-orchestrations', '/admin/ai-execution', 'orchestrations', '编排管理'],
  ['/admin/ai-opencode', '/admin/ai-settings', 'runtime', '运行时'],
  ['/admin/ai-skillopt', '/admin/ai-skills', 'fit', '拟合优化'],
] as const

for (const [from, path, tab, label] of CASES) {
  test(`旧路径 ${from} 重定向到 ${path}?tab=${tab}`, async ({ page }) => {
    await gotoWithAuth(page, from)
    await expect(page).toHaveURL(new RegExp(`${path.replace(/\//g, '\\/')}\\?tab=${tab}`))
    await expect(page.locator('.el-tabs__item.is-active')).toContainText(label)
  })
}

test('AI 能力组侧边栏收敛为 4 项', async ({ page }) => {
  await gotoWithAuth(page, '/admin/ai-execution')
  const group = page.locator('.settings-menu__group', { hasText: 'AI 能力' })
  await expect(group.locator('a', { hasText: 'AI 配置' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 定时巡检' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 技能' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 执行中心' })).toHaveCount(1)
  await expect(group.locator('a', { hasText: 'AI 批量执行' })).toHaveCount(0)
})
```

（侧栏结构：`SettingsSideMenu.vue` → `.settings-menu__group` / `.settings-menu__item`。）

- [ ] **Step 2: 跑新 spec**

Run: `npx playwright test e2e/ai-full/ai-admin-nav.spec.ts`
Expected: 6 例 PASS

- [ ] **Step 3: 相关既有 e2e 回归**

Run: `npx playwright test e2e/ai-full/ai-governance-audit.spec.ts e2e/ai-full/ai-skillopt-page.spec.ts`
Expected: PASS（`/admin/ai-batches`、`/admin/ai-sessions`、`/admin/ai-skillopt` 经重定向落对 tab，页面内容断言不受影响）

- [ ] **Step 4: 全量 e2e 回归**（spec §5：现有 43 例 + 本任务新增 6 例）

Run: `npx playwright test e2e/ai-full/`
Expected: 全绿；模型行为类用例（ai-mcp-tools、ai-verifier-gate）如遇单例波动按 17 号报告 §4.3 定性，复跑通过即可

- [ ] **Step 5: 文档措辞更新**

`grep -rn "admin/ai-orchestrations\|admin/ai-skillopt\|admin/ai-opencode\|admin/ai-batches\|admin/ai-sessions" docs/user-guide/`，命中处改为域入口表述（如「/admin/ai-execution 的『编排管理』页签」）；`docs/user-guide/` 内菜单截图如显示旧 8 项侧栏则重截（涉及编排指导文档的 3 张截图见 c57236f）。

- [ ] **Step 6: Commit**

```bash
git add e2e/ai-full/ai-admin-nav.spec.ts docs/user-guide/
git commit -m "test(e2e): AI 域入口导航与旧路径重定向冒烟 + user-guide 措辞更新"
```
