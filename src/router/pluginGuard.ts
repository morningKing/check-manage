/**
 * 插件路由门（plugin-architecture spec §5）
 *
 * meta.plugin 的路由在所属插件禁用时拦到 NotFound；
 * store 加载失败放行（fail-open）——后端蓝图门才是硬墙，/plugins 抖动不应锁死导航。
 */

import type { RouteLocationNormalized } from 'vue-router'

export async function pluginRouteAllowed(to: RouteLocationNormalized): Promise<boolean> {
  const pid = to.meta.plugin as string | undefined
  if (!pid) return true
  const { usePluginsStore } = await import('@/stores/plugins')
  const store = usePluginsStore()
  try {
    await store.ensureLoaded()
  } catch {
    return true
  }
  return store.enabled(pid)
}
