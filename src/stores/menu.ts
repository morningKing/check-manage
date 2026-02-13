/**
 * 菜单状态管理 Store
 *
 * 管理应用的菜单配置数据，包括：
 * - 菜单列表的获取和更新
 * - 菜单树结构的构建
 * - 菜单项的增删改操作
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { MenuItem } from '@/types'
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

  // ==================== Getters ====================

  /**
   * 构建菜单树结构
   *
   * 将扁平化的菜单列表转换为嵌套的树形结构
   * 支持1-3级菜单嵌套
   */
  const menuTree = computed<MenuItem[]>(() => {
    return buildMenuTree(menuList.value)
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
    updateMenuOrder
  }
})
