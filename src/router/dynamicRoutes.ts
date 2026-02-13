/**
 * 动态路由生成工具
 *
 * 职责：
 * - 根据菜单配置生成动态路由
 * - 支持嵌套菜单的路由生成
 * - 提供路由添加和移除方法
 *
 * 注意：
 * 当前项目使用静态路由 + 动态页面参数的方式
 * 此文件为扩展预留，可用于完全动态的路由生成场景
 */

import type { Router, RouteRecordRaw } from 'vue-router'
import type { MenuItem } from '@/types'

/**
 * 根据菜单配置生成路由
 *
 * @param menus - 菜单配置列表
 * @returns 路由配置数组
 */
export function generateRoutesFromMenus(menus: MenuItem[]): RouteRecordRaw[] {
  const routes: RouteRecordRaw[] = []

  menus.forEach((menu) => {
    // 只处理有路径和页面ID的菜单（叶子菜单）
    if (menu.path && menu.pageId) {
      const route: RouteRecordRaw = {
        path: menu.path,
        name: `Dynamic_${menu.id}`,
        component: () => import('@/views/dynamic/DynamicPage.vue'),
        props: {
          pageId: menu.pageId
        },
        meta: {
          title: menu.name,
          icon: menu.icon,
          menuId: menu.id
        }
      }
      routes.push(route)
    }

    // 递归处理子菜单
    if (menu.children && menu.children.length > 0) {
      routes.push(...generateRoutesFromMenus(menu.children))
    }
  })

  return routes
}

/**
 * 添加动态路由
 *
 * @param router - 路由实例
 * @param routes - 要添加的路由配置
 * @param parentName - 父路由名称（默认为 Layout）
 */
export function addDynamicRoutes(
  router: Router,
  routes: RouteRecordRaw[],
  parentName: string = 'Layout'
): void {
  routes.forEach((route) => {
    router.addRoute(parentName, route)
  })
}

/**
 * 移除动态路由
 *
 * @param router - 路由实例
 * @param routeNames - 要移除的路由名称列表
 */
export function removeDynamicRoutes(router: Router, routeNames: string[]): void {
  routeNames.forEach((name) => {
    if (router.hasRoute(name)) {
      router.removeRoute(name)
    }
  })
}

/**
 * 重置路由
 *
 * 移除所有动态添加的路由，恢复到初始状态
 *
 * @param router - 路由实例
 */
export function resetRouter(router: Router): void {
  const routes = router.getRoutes()
  routes.forEach((route) => {
    // 移除以 Dynamic_ 开头的路由（动态生成的）
    if (route.name && String(route.name).startsWith('Dynamic_')) {
      router.removeRoute(route.name)
    }
  })
}

/**
 * 根据路径查找匹配的菜单
 *
 * @param menus - 菜单列表
 * @param path - 路由路径
 * @returns 匹配的菜单项或 undefined
 */
export function findMenuByPath(menus: MenuItem[], path: string): MenuItem | undefined {
  for (const menu of menus) {
    if (menu.path === path) {
      return menu
    }
    if (menu.children && menu.children.length > 0) {
      const found = findMenuByPath(menu.children, path)
      if (found) return found
    }
  }
  return undefined
}

/**
 * 获取菜单的完整路径（面包屑）
 *
 * @param menus - 菜单列表
 * @param menuId - 目标菜单ID
 * @returns 从根到目标菜单的路径数组
 */
export function getMenuBreadcrumb(
  menus: MenuItem[],
  menuId: string
): MenuItem[] {
  const result: MenuItem[] = []

  function findPath(items: MenuItem[], targetId: string, path: MenuItem[]): boolean {
    for (const item of items) {
      const newPath = [...path, item]
      if (item.id === targetId) {
        result.push(...newPath)
        return true
      }
      if (item.children && item.children.length > 0) {
        if (findPath(item.children, targetId, newPath)) {
          return true
        }
      }
    }
    return false
  }

  findPath(menus, menuId, [])
  return result
}
