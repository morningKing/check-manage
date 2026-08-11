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
      // 返回 { path, query: {} } 而非裸字符串：函数式 redirect 返回字符串时
      // vue-router 4 会默认继承原 query，裸字符串会让 ?tab= 之类的历史查询串
      // 残留在地址栏上（下面两处同理，共 3 处）。
      redirect: () => ({ path: firstAccessibleItemPath(getCan()), query: {} }),
    },
  ]

  for (const group of SETTINGS_GROUPS) {
    routes.push({
      path: `/admin/${group.id}`,
      redirect: (to) => {
        const can = getCan()
        const tab = to.query.tab
        const tabItem = typeof tab === 'string' ? findSettingsItem(tab) : undefined
        // tab 必须既存在又是当前用户有权限的条目，否则落到该组首个有权限条目 ——
        // 避免把只有分组内其他能力的用户，用老书签的 ?tab= 指到自己无权限的条目，
        // 白白挨一次守卫拦截再弹回 /home（这本来是兜底分支该负责的体验）。
        if (tabItem && can(tabItem.perm)) {
          return { path: `/admin/${tabItem.id}`, query: {} }
        }
        const first = group.items.find(i => can(i.perm))
        return { path: first ? `/admin/${first.id}` : '/admin', query: {} }
      },
    })
  }

  for (const [alias, targetId] of Object.entries(LEGACY_PATH_ALIASES)) {
    routes.push({ path: `/admin/${alias}`, redirect: `/admin/${targetId}` })
  }

  return routes
}
