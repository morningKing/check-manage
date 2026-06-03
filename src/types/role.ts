/**
 * 角色与权限相关类型定义
 */

/** 数据页默认权限 */
export type DefaultPageAccess = 'none' | 'read' | 'write'

/** 权限目录项（管理功能开关） */
export interface PermissionCatalogItem {
  key: string
  label: string
  group: string
}

/** 单个数据页 CRUD 配置 */
export interface PagePermission {
  pageId: string
  canRead: boolean
  canCreate: boolean
  canUpdate: boolean
  canDelete: boolean
}

/** 角色（列表项） */
export interface Role {
  id: string
  name: string
  description: string
  isSystem: boolean
  isSuperuser: boolean
  defaultPageAccess: DefaultPageAccess
}

/** 角色详情（编辑器） */
export interface RoleDetail extends Role {
  adminKeys: string[]
  pagePermissions: PagePermission[]
}

/** 后端解析后的当前用户权限集合 */
export interface ResolvedPermissions {
  isSuperuser: boolean
  adminKeys: string[]
  defaultPageAccess: DefaultPageAccess
  pagePerms: Record<string, { read: boolean; create: boolean; update: boolean; delete: boolean }>
}
