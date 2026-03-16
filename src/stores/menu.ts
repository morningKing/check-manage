/**
 * 菜单状态管理 Store
 *
 * 管理应用的菜单配置数据，包括：
 * - 菜单列表的获取和更新
 * - 菜单树结构的构建
 * - 菜单项的增删改操作
 *
 * 性能优化：
 * - 使用浅比较缓存菜单树，避免重复构建
 * - 使用 Map 缓存角色过滤结果，避免重复过滤
 * - 保持对象引用稳定，减少不必要的组件重渲染
 */

import { defineStore } from 'pinia'
import { ref, computed, shallowRef } from 'vue'
import type { MenuItem, UserRole } from '@/types'
import { get, post, put, del } from '@/utils/request'
import { v4 as uuidv4 } from 'uuid'
import router from '@/router'
import { generateRoutesFromMenus, addDynamicRoutes, resetRouter } from '@/router/dynamicRoutes'

/**
 * 菜单 Store
 *
 * 使用 Composition API 风格定义
 */
export const useMenuStore = defineStore('menu', () => {
  // ==================== State ====================

  /**
   * 扁平化菜单列表（原始数据）
   */
  const menuList = ref<MenuItem[]>([])

  /**
   * 数据加载状态
   */
  const loading = ref(false)

  /**
   * 当前激活的菜单ID
   */
  const activeMenuId = ref<string>('')

  /**
   * 上次构建时的菜单列表引用（用于检测变化）
   */
  let lastMenuListRef: MenuItem[] | null = null

  /**
   * 缓存的菜单树（使用 shallowRef 避免深度响应）
   */
  const cachedMenuTree = shallowRef<MenuItem[]>([])

  /**
   * 角色过滤缓存 Map
   * key: role, value: 过滤后的菜单树
   */
  const filteredMenuCache = new Map<UserRole, MenuItem[]>()

  // ==================== Getters ====================

  /**
   * 构建菜单树结构
   *
   * 使用缓存策略：仅当 menuList 引用变化时重新构建
   * 将扁平化的菜单列表转换为嵌套的树形结构
   * 支持1-3级菜单嵌套
   */
  const menuTree = computed<MenuItem[]>(() => {
    // 检查引用是否变化
    if (lastMenuListRef !== menuList.value) {
      lastMenuListRef = menuList.value
      cachedMenuTree.value = buildMenuTree(menuList.value)
      // 清空过滤缓存
      filteredMenuCache.clear()
    }
    return cachedMenuTree.value
  })

  /**
   * 获取所有顶级菜单
   */
  const topLevelMenus = computed<MenuItem[]>(() => {
    return menuList.value
      .filter((item) => !item.parentId)
      .sort((a, b) => a.order - b.order)
  })

  /**
   * 根据ID获取菜单项
   *
   * @param id - 菜单ID
   * @returns 菜单项或undefined
   */
  const getMenuById = computed(() => {
    return (id: string): MenuItem | undefined => {
      return menuList.value.find((item) => item.id === id)
    }
  })

  /**
   * 获取指定菜单的子菜单
   *
   * @param parentId - 父级菜单ID
   * @returns 子菜单列表
   */
  const getChildMenus = computed(() => {
    return (parentId: string): MenuItem[] => {
      return menuList.value
        .filter((item) => item.parentId === parentId)
        .sort((a, b) => a.order - b.order)
    }
  })

  /**
   * 根据路径获取菜单项
   *
   * @param path - 路由路径
   * @returns 菜单项或undefined
   */
  const getMenuByPath = computed(() => {
    return (path: string): MenuItem | undefined => {
      return menuList.value.find((item) => item.path === path)
    }
  })

  // ==================== Actions ====================

  /**
   * 从API获取菜单列表
   */
  async function fetchMenus(): Promise<void> {
    loading.value = true
    try {
      const data = await get<MenuItem[]>('/menus')
      menuList.value = data
    } catch (error) {
      console.error('获取菜单列表失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  /**
   * 添加菜单项
   *
   * @param menu - 菜单数据（不含ID）
   * @returns 创建的菜单项
   */
  async function addMenu(menu: Omit<MenuItem, 'id'>): Promise<MenuItem> {
    const newMenu: MenuItem = {
      ...menu,
      id: `menu-${uuidv4().slice(0, 8)}`
    }

    try {
      const created = await post<MenuItem>('/menus', newMenu)
      menuList.value.push(created)
      syncDynamicRoutes()
      return created
    } catch (error) {
      console.error('添加菜单失败:', error)
      throw error
    }
  }

  /**
   * 更新菜单项
   *
   * @param id - 菜单ID
   * @param menu - 更新的菜单数据
   * @returns 更新后的菜单项
   */
  async function updateMenu(id: string, menu: Partial<MenuItem>): Promise<MenuItem> {
    try {
      const updated = await put<MenuItem>(`/menus/${id}`, { ...menu, id })
      const index = menuList.value.findIndex((item) => item.id === id)
      if (index !== -1) {
        menuList.value[index] = updated
      }
      syncDynamicRoutes()
      return updated
    } catch (error) {
      console.error('更新菜单失败:', error)
      throw error
    }
  }

  /**
   * 删除菜单项
   *
   * 同时删除所有子菜单
   *
   * @param id - 菜单ID
   */
  async function deleteMenu(id: string): Promise<void> {
    try {
      // 获取所有需要删除的菜单ID（包括子菜单）
      const idsToDelete = getDescendantIds(id)
      idsToDelete.push(id)

      // 依次删除
      for (const menuId of idsToDelete) {
        await del(`/menus/${menuId}`)
      }

      // 更新本地状态
      menuList.value = menuList.value.filter((item) => !idsToDelete.includes(item.id))
      syncDynamicRoutes()
    } catch (error) {
      console.error('删除菜单失败:', error)
      throw error
    }
  }

  /**
   * 设置当前激活菜单
   *
   * @param id - 菜单ID
   */
  function setActiveMenu(id: string): void {
    activeMenuId.value = id
  }

  /**
   * 批量更新菜单顺序
   *
   * @param updates - 更新列表 [{id, order}]
   */
  async function updateMenuOrder(updates: { id: string; order: number }[]): Promise<void> {
    try {
      for (const update of updates) {
        await put(`/menus/${update.id}`, {
          ...getMenuById.value(update.id),
          order: update.order
        })
      }
      // 更新本地状态
      updates.forEach((update) => {
        const menu = menuList.value.find((item) => item.id === update.id)
        if (menu) {
          menu.order = update.order
        }
      })
    } catch (error) {
      console.error('更新菜单顺序失败:', error)
      throw error
    }
  }

  // ==================== Helper Functions ====================

  /**
   * 同步动态路由
   *
   * 根据当前菜单配置重新生成并注册路由
   */
  function syncDynamicRoutes(): void {
    resetRouter(router)
    const routes = generateRoutesFromMenus(menuTree.value)
    addDynamicRoutes(router, routes)
  }

  /**
   * 构建菜单树
   *
   * @param list - 扁平化菜单列表
   * @param parentId - 父级ID（用于递归）
   * @returns 树形菜单列表
   */
  function buildMenuTree(list: MenuItem[], parentId: string | null = null): MenuItem[] {
    return list
      .filter((item) => item.parentId === parentId)
      .sort((a, b) => a.order - b.order)
      .map((item) => ({
        ...item,
        children: buildMenuTree(list, item.id)
      }))
  }

  /**
   * 获取所有后代菜单ID
   *
   * @param parentId - 父级菜单ID
   * @returns 后代菜单ID列表
   */
  function getDescendantIds(parentId: string): string[] {
    const descendants: string[] = []
    const children = menuList.value.filter((item) => item.parentId === parentId)

    children.forEach((child) => {
      descendants.push(child.id)
      descendants.push(...getDescendantIds(child.id))
    })

    return descendants
  }

  /**
   * 根据角色获取过滤后的菜单树（带缓存）
   *
   * 使用 Map 缓存过滤结果，避免重复计算
   * 对象引用稳定，防止不必要的组件重渲染
   *
   * @param role - 用户角色
   * @returns 过滤后的菜单树
   */
  function getFilteredMenuTree(role: UserRole | null): MenuItem[] {
    if (!role) return []

    // 检查缓存
    if (filteredMenuCache.has(role)) {
      return filteredMenuCache.get(role)!
    }

    // 计算并缓存
    const filtered = filterMenusByRole(cachedMenuTree.value, role)
    filteredMenuCache.set(role, filtered)
    return filtered
  }

  /**
   * 递归过滤菜单树（基于菜单的 roles 配置）
   *
   * @param menus - 菜单列表
   * @param role - 用户角色
   * @returns 过滤后的菜单列表
   */
  function filterMenusByRole(menus: MenuItem[], role: UserRole): MenuItem[] {
    const result: MenuItem[] = []

    for (const menu of menus) {
      // 检查当前菜单是否对该角色可见
      const menuRoles = menu.roles || ['admin', 'developer', 'guest']
      if (!menuRoles.includes(role)) continue

      // 递归过滤子菜单
      const filteredChildren = menu.children
        ? filterMenusByRole(menu.children, role)
        : []

      // 使用原对象引用，仅当需要修改 children 时创建新对象
      if (menu.children && menu.children.length !== filteredChildren.length) {
        result.push({ ...menu, children: filteredChildren })
      } else if (menu.children && menu.children.length === filteredChildren.length) {
        // 子菜单数量相同，检查引用是否变化
        const childrenChanged = filteredChildren.some((child, i) => child !== menu.children![i])
        if (childrenChanged) {
          result.push({ ...menu, children: filteredChildren })
        } else {
          // 完全使用原对象，保持引用稳定
          result.push(menu)
        }
      } else {
        result.push(menu)
      }
    }

    return result
  }

  // 返回需要暴露的内容
  return {
    // State
    menuList,
    loading,
    activeMenuId,
    // Getters
    menuTree,
    topLevelMenus,
    getMenuById,
    getChildMenus,
    getMenuByPath,
    // Actions
    fetchMenus,
    addMenu,
    updateMenu,
    deleteMenu,
    setActiveMenu,
    updateMenuOrder,
    // 性能优化：带缓存的过滤方法
    getFilteredMenuTree
  }
})
