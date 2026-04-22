/**
 * 版本管理相关类型定义 - 项目级版本管理
 *
 * 注意：数据级版本管理已废弃，仅保留项目级版本类型
 */

export type VersionType = 'snapshot' | 'branch'
export type VersionStatus = 'active' | 'merged' | 'archived'
export type MergeStrategy = 'theirs' | 'ours'

/**
 * 版本状态标签
 */
export const VERSION_STATUS_LABELS: Record<string, string> = {
  active: '活跃',
  merged: '已合并',
  archived: '已归档',
}

export const VERSION_STATUS_TAG_TYPES: Record<string, string> = {
  active: 'success',
  merged: 'info',
  archived: 'warning',
}

/**
 * 版本类型标签
 */
export const VERSION_TYPE_LABELS: Record<string, string> = {
  snapshot: '快照',
  branch: '分支',
}

export const VERSION_TYPE_TAG_TYPES: Record<string, string> = {
  snapshot: '',
  branch: 'warning',
}

/**
 * 项目版本（项目级分支/快照）
 */
export interface ProjectVersion {
  id: string
  projectMenuId: string
  name: string
  description?: string
  versionType: VersionType
  status: VersionStatus
  createdBy: string
  createdAt: string
  parentVersion?: string
  recordsCount: number
  isProtected: boolean
  initializedAt?: string
  collections?: ProjectVersionCollection[]
  collectionStats?: Record<string, number>
}

/**
 * 项目版本中的collection信息
 */
export interface ProjectVersionCollection {
  collection: string
  pageId: string
  pageName: string
  menuId: string
}

/**
 * 创建项目版本请求
 */
export interface ProjectVersionFormData {
  projectMenuId: string
  name: string
  description?: string
  versionType?: VersionType
  createdBy: string
  parentVersion?: string
}