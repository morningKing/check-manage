import { describe, it, expect, vi } from 'vitest'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'
import { buildSettingsRoutes, buildSettingsRedirects } from '../settingsRoutes'
import { ALL_SETTINGS_ITEMS } from '@/views/admin/hub/settingsCatalog'

// 本文件会真的触发路由懒加载组件（不像旧的 SettingsHub/CategoryView 测试
// 那样把组件整体 stub 掉）。好几个设置页组件经由 `@/components/common`
// 桶文件间接拉入体量很大的 ExcelView.vue（Univer.js 电子表格），其顶层
// 代码引用 jsdom 没有的 Canvas API（Path2D 等），首次转换/求值实测要
// 20s+，在并行跑全量测试时甚至能顶到 45s+，导致这个纯路由单测跟着变慢
// 变抖。这里只测路由跳转是否正确，压根不需要真的渲染这些页面组件，所以
// 直接把桶文件的每个具名导出都换成最小 stub——不改任何组件内部逻辑，纯粹
// 是测试环境隔离（同 CLAUDE.md 里 ResizeObserver 的做法）。
vi.mock('@/components/common', () => {
  const Stub = { template: '<div />' }
  return new Proxy({}, { get: () => Stub })
})

// 首次真实导航仍要懒加载/转换本文件涉及的设置页组件本身（21 个 .vue 文件
// + 各自的 api/types 依赖），实测首次约 6s，全量测试并行跑时会更久；调大
// 本文件超时留够余量，避免和机器负载相关的抖动。
vi.setConfig({ testTimeout: 20000 })

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
