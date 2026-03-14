/**
 * 菜单相关类型定义
 *
 * 定义菜单项的数据结构，支持1-3级嵌套菜单
 */

import type { UserRole } from './user'

/**
 * 菜单项接口
 *
 * @property id - 菜单唯一标识
 * @property name - 菜单显示名称
 * @property icon - 菜单图标名称（Element Plus 图标）
 * @property pageId - 关联的页面配置ID（可选，仅叶子菜单需要）
 * @property parentId - 父级菜单ID（null表示顶级菜单）
 * @property order - 排序序号（升序排列）
 * @property path - 路由路径（可选，用于导航）
 * @property roles - 可见此菜单的角色列表
 * @property children - 子菜单列表（运行时构建，非存储字段）
 * @property exportScriptId - 菜单级导出脚本ID
 * @property exportScriptName - 导出脚本名称（前端展示用）
 */
export interface MenuItem {
  id: string
  name: string
  icon?: string
  pageId?: string | null
  parentId?: string | null
  order: number
  path?: string | null
  roles?: UserRole[]
  children?: MenuItem[]
  exportScriptId?: string | null
  exportScriptName?: string
}

/**
 * 菜单表单数据接口
 *
 * 用于菜单编辑表单的数据结构
 */
export interface MenuFormData {
  id?: string
  name: string
  icon: string
  pageId: string | null
  parentId: string | null
  order: number
  path: string
  roles: UserRole[]
  exportScriptId?: string | null
}

/**
 * 菜单树节点接口
 *
 * 用于 Element Plus Tree 组件的数据结构
 */
export interface MenuTreeNode {
  id: string
  label: string
  icon?: string
  children?: MenuTreeNode[]
  data: MenuItem
}

/**
 * 菜单导出预览 - 页面信息
 */
export interface MenuExportPageInfo {
  collection: string
  pageName: string
  recordCount: number
}

/**
 * 菜单导出预览 - 单个菜单信息
 */
export interface MenuExportPreviewItem {
  menuId: string
  menuName: string
  pages: MenuExportPageInfo[]
  boundScript?: { id: string; name: string } | null
  totalRecords: number
}

/**
 * 菜单导出预览响应
 */
export interface MenuExportPreview {
  menus: MenuExportPreviewItem[]
  totalRecords: number
  availableScripts: Array<{ id: string; name: string; description?: string }>
}

/**
 * 创建空菜单表单数据
 *
 * @returns 初始化的菜单表单数据
 */
export function createEmptyMenuFormData(): MenuFormData {
  return {
    name: '',
    icon: 'Document',
    pageId: null,
    parentId: null,
    order: 1,
    path: '',
    roles: ['admin', 'developer', 'guest'],
    exportScriptId: null
  }
}
