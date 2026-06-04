/**
 * 角色与权限管理 API 接口（管理员）
 */
import { get, post, put, del } from '@/utils/request'
import type { Role, RoleDetail, RoleOption, PermissionCatalogItem } from '@/types'

/** 获取角色列表 */
export function getRoles() {
  return get<Role[]>('/roles')
}

/** 获取轻量角色选项（id+名称+标记），供下拉选择器使用，仅需登录 */
export function getRoleOptions() {
  return get<RoleOption[]>('/roles/options')
}

/** 设置某角色可见的菜单集合（从角色侧维护 menus.roles） */
export function updateRoleMenuVisibility(id: string, menuIds: string[]) {
  return put<{ message: string }>(`/roles/${id}/menu-visibility`, { menuIds })
}

/** 获取角色详情 */
export function getRole(id: string) {
  return get<RoleDetail>(`/roles/${id}`)
}

/** 获取权限目录（管理功能开关清单） */
export function getPermissionCatalog() {
  return get<PermissionCatalogItem[]>('/roles/catalog')
}

/** 创建角色 */
export function createRole(data: Partial<Role>) {
  return post<{ id: string; name: string }>('/roles', data)
}

/** 更新角色 */
export function updateRole(id: string, data: Partial<RoleDetail>) {
  return put<{ message: string }>(`/roles/${id}`, data)
}

/** 删除角色 */
export function deleteRole(id: string) {
  return del(`/roles/${id}`)
}
