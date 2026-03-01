/**
 * 动态路由工具单元测试
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Router, RouteRecordRaw } from 'vue-router'
import type { MenuItem } from '@/types'
import {
  generateRoutesFromMenus,
  addDynamicRoutes,
  removeDynamicRoutes,
  resetRouter,
  findMenuByPath,
  getMenuBreadcrumb,
} from '../dynamicRoutes'

function makeMenu(overrides: Partial<MenuItem>): MenuItem {
  return {
    id: 'menu-1',
    name: '菜单1',
    icon: 'icon-menu',
    order: 0,
    ...overrides,
  } as MenuItem
}

function makeMockRouter(): Router {
  const routes: Map<string, RouteRecordRaw> = new Map()

  return {
    addRoute: vi.fn((parentName: string, route: RouteRecordRaw) => {
      routes.set(route.name as string, route)
    }),
    removeRoute: vi.fn((name: string) => {
      routes.delete(name)
    }),
    hasRoute: vi.fn((name: string) => routes.has(name)),
    getRoutes: vi.fn(() =>
      Array.from(routes.entries()).map(([name, route]) => ({ ...route, name }))
    ),
  } as unknown as Router
}

describe('dynamicRoutes', () => {
  describe('generateRoutesFromMenus', () => {
    it('空数组返回空数组', () => {
      expect(generateRoutesFromMenus([])).toEqual([])
    })

    it('单层菜单生成路由', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '仪表盘', path: '/dashboard', pageId: 'page-1' }),
        makeMenu({ id: 'm2', name: '设置', path: '/settings', pageId: 'page-2' }),
      ]

      const routes = generateRoutesFromMenus(menus)

      expect(routes).toHaveLength(2)
      expect(routes[0].path).toBe('/dashboard')
      expect(routes[0].name).toBe('Dynamic_m1')
      expect(routes[0].meta?.title).toBe('仪表盘')
      expect(routes[0].meta?.menuId).toBe('m1')
      expect(routes[1].path).toBe('/settings')
    })

    it('多层嵌套菜单只生成叶子节点路由', () => {
      const menus: MenuItem[] = [
        makeMenu({
          id: 'm1',
          name: '系统管理',
          path: '/system',
          pageId: undefined,
          children: [
            makeMenu({ id: 'm2', name: '用户管理', path: '/system/users', pageId: 'page-1' }),
            makeMenu({
              id: 'm3',
              name: '配置管理',
              path: '/system/config',
              pageId: undefined,
              children: [
                makeMenu({ id: 'm4', name: '参数设置', path: '/system/config/params', pageId: 'page-2' }),
              ],
            }),
          ],
        }),
      ]

      const routes = generateRoutesFromMenus(menus)

      // 只有 m2 和 m4 有 pageId，所以只生成 2 个路由
      expect(routes).toHaveLength(2)
      expect(routes[0].path).toBe('/system/users')
      expect(routes[0].name).toBe('Dynamic_m2')
      expect(routes[1].path).toBe('/system/config/params')
      expect(routes[1].name).toBe('Dynamic_m4')
    })

    it('没有 path 的菜单不生成路由', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '菜单', path: undefined, pageId: 'page-1' }),
        makeMenu({ id: 'm2', name: '菜单2', path: '/test', pageId: 'page-2' }),
      ]

      const routes = generateRoutesFromMenus(menus)

      expect(routes).toHaveLength(1)
      expect(routes[0].name).toBe('Dynamic_m2')
    })

    it('没有 pageId 的菜单不生成路由', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '菜单', path: '/test', pageId: undefined }),
        makeMenu({ id: 'm2', name: '菜单2', path: '/test2', pageId: 'page-1' }),
      ]

      const routes = generateRoutesFromMenus(menus)

      expect(routes).toHaveLength(1)
      expect(routes[0].name).toBe('Dynamic_m2')
    })

    it('路由 meta 属性正确设置', () => {
      const menus: MenuItem[] = [
        makeMenu({
          id: 'm1',
          name: '仪表盘',
          icon: 'icon-dashboard',
          path: '/dashboard',
          pageId: 'page-1',
        }),
      ]

      const routes = generateRoutesFromMenus(menus)

      expect(routes[0].meta).toEqual({
        title: '仪表盘',
        icon: 'icon-dashboard',
        menuId: 'm1',
      })
    })
  })

  describe('addDynamicRoutes', () => {
    it('添加路由到默认父路由', () => {
      const router = makeMockRouter()
      const routes: RouteRecordRaw[] = [
        { path: '/test1', name: 'Dynamic_m1', component: {} as any },
        { path: '/test2', name: 'Dynamic_m2', component: {} as any },
      ]

      addDynamicRoutes(router, routes)

      expect(router.addRoute).toHaveBeenCalledTimes(2)
      expect(router.addRoute).toHaveBeenCalledWith('Layout', routes[0])
      expect(router.addRoute).toHaveBeenCalledWith('Layout', routes[1])
    })

    it('添加路由到指定父路由', () => {
      const router = makeMockRouter()
      const routes: RouteRecordRaw[] = [
        { path: '/test', name: 'Dynamic_m1', component: {} as any },
      ]

      addDynamicRoutes(router, routes, 'CustomLayout')

      expect(router.addRoute).toHaveBeenCalledWith('CustomLayout', routes[0])
    })
  })

  describe('removeDynamicRoutes', () => {
    it('移除存在的路由', () => {
      const router = makeMockRouter()
      ;(router.hasRoute as any).mockReturnValue(true)

      removeDynamicRoutes(router, ['Dynamic_m1', 'Dynamic_m2'])

      expect(router.removeRoute).toHaveBeenCalledTimes(2)
      expect(router.removeRoute).toHaveBeenCalledWith('Dynamic_m1')
      expect(router.removeRoute).toHaveBeenCalledWith('Dynamic_m2')
    })

    it('忽略不存在的路由', () => {
      const router = makeMockRouter()
      ;(router.hasRoute as any).mockReturnValue(false)

      removeDynamicRoutes(router, ['Dynamic_m1'])

      expect(router.removeRoute).not.toHaveBeenCalled()
    })
  })

  describe('resetRouter', () => {
    it('移除所有 Dynamic_ 开头的路由', () => {
      const router = makeMockRouter()
      ;(router.getRoutes as any).mockReturnValue([
        { name: 'Dynamic_m1', path: '/test1' },
        { name: 'Dynamic_m2', path: '/test2' },
        { name: 'StaticRoute', path: '/static' },
        { name: 'Layout', path: '/' },
      ])

      resetRouter(router)

      expect(router.removeRoute).toHaveBeenCalledTimes(2)
      expect(router.removeRoute).toHaveBeenCalledWith('Dynamic_m1')
      expect(router.removeRoute).toHaveBeenCalledWith('Dynamic_m2')
    })

    it('没有动态路由时不移除', () => {
      const router = makeMockRouter()
      ;(router.getRoutes as any).mockReturnValue([
        { name: 'Layout', path: '/' },
        { name: 'Login', path: '/login' },
      ])

      resetRouter(router)

      expect(router.removeRoute).not.toHaveBeenCalled()
    })
  })

  describe('findMenuByPath', () => {
    it('找到匹配的菜单', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', path: '/dashboard' }),
        makeMenu({ id: 'm2', path: '/settings' }),
      ]

      const result = findMenuByPath(menus, '/settings')

      expect(result?.id).toBe('m2')
    })

    it('在子菜单中查找', () => {
      const menus: MenuItem[] = [
        makeMenu({
          id: 'm1',
          path: '/system',
          children: [
            makeMenu({ id: 'm2', path: '/system/users' }),
            makeMenu({ id: 'm3', path: '/system/roles' }),
          ],
        }),
      ]

      const result = findMenuByPath(menus, '/system/roles')

      expect(result?.id).toBe('m3')
    })

    it('找不到返回 undefined', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', path: '/dashboard' }),
      ]

      const result = findMenuByPath(menus, '/nonexistent')

      expect(result).toBeUndefined()
    })

    it('空数组返回 undefined', () => {
      expect(findMenuByPath([], '/any')).toBeUndefined()
    })
  })

  describe('getMenuBreadcrumb', () => {
    it('返回从根到目标的完整路径', () => {
      const menus: MenuItem[] = [
        makeMenu({
          id: 'm1',
          name: '系统管理',
          children: [
            makeMenu({
              id: 'm2',
              name: '用户管理',
              children: [makeMenu({ id: 'm3', name: '用户列表' })],
            }),
          ],
        }),
      ]

      const breadcrumb = getMenuBreadcrumb(menus, 'm3')

      expect(breadcrumb).toHaveLength(3)
      expect(breadcrumb[0].name).toBe('系统管理')
      expect(breadcrumb[1].name).toBe('用户管理')
      expect(breadcrumb[2].name).toBe('用户列表')
    })

    it('顶级菜单返回单元素数组', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '仪表盘' }),
        makeMenu({ id: 'm2', name: '设置' }),
      ]

      const breadcrumb = getMenuBreadcrumb(menus, 'm2')

      expect(breadcrumb).toHaveLength(1)
      expect(breadcrumb[0].name).toBe('设置')
    })

    it('找不到目标返回空数组', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '仪表盘' }),
      ]

      const breadcrumb = getMenuBreadcrumb(menus, 'nonexistent')

      expect(breadcrumb).toEqual([])
    })

    it('空菜单列表返回空数组', () => {
      expect(getMenuBreadcrumb([], 'any')).toEqual([])
    })

    it('多分支树正确追踪路径', () => {
      const menus: MenuItem[] = [
        makeMenu({
          id: 'm1',
          name: '系统管理',
          children: [
            makeMenu({
              id: 'm2',
              name: '用户管理',
              children: [makeMenu({ id: 'm4', name: '用户列表' })],
            }),
            makeMenu({
              id: 'm3',
              name: '角色管理',
              children: [makeMenu({ id: 'm5', name: '角色列表' })],
            }),
          ],
        }),
      ]

      const breadcrumb = getMenuBreadcrumb(menus, 'm5')

      expect(breadcrumb).toHaveLength(3)
      expect(breadcrumb[0].name).toBe('系统管理')
      expect(breadcrumb[1].name).toBe('角色管理')
      expect(breadcrumb[2].name).toBe('角色列表')
    })
  })
})