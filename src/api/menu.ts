/**
 * 菜单相关 API 接口
 *
 * 封装菜单配置的 CRUD 操作
 */

import { get, post, put, del } from '@/utils/request'
import type { MenuItem } from '@/types'

/**
 * 获取菜单列表
 */
export function getMenuList() {
  return get<MenuItem[]>('/menus')
}

/**
 * 获取菜单详情
 *
 * @param id - 菜单ID
 */
export function getMenuById(id: string) {
  return get<MenuItem>(`/menus/${id}`)
}

/**
 * 创建菜单
 *
 * @param menu - 菜单数据
 */
export function createMenu(menu: Omit<MenuItem, 'children'>) {
  return post<MenuItem>('/menus', menu)
}

/**
 * 更新菜单
 *
 * @param id - 菜单ID
 * @param menu - 更新的菜单数据
 */
export function updateMenu(id: string, menu: Partial<MenuItem>) {
  return put<MenuItem>(`/menus/${id}`, { ...menu, id })
}

/**
 * 删除菜单
 *
 * @param id - 菜单ID
 */
export function deleteMenu(id: string) {
  return del(`/menus/${id}`)
}
