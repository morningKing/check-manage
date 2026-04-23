import request from '@/utils/request'
import type { ProjectVersion, ProjectVersionFormData } from '@/types/version'

export interface ProjectCollection {
  collection: string
  pageId: string
  pageName: string
  menuId: string
}

export interface CurrentBranch {
  branchId: string
  branchName: string
}

export interface SwitchBranchResult {
  branchId: string
  branchName: string
  affectedCollections: string[]
  collectionsCount: number
}

export interface ProjectVersionListResult {
  items: ProjectVersion[]
  total: number
  page: number
  pageSize: number
}

// 版本对比相关类型
export interface CollectionDiff {
  collection: string
  pageName: string
  added: Record<string, any>[]
  removed: Record<string, any>[]
  modified: ModifiedRecord[]
  unchangedCount: number
}

export interface ModifiedRecord {
  id: string
  record: Record<string, unknown>
  oldRecord: Record<string, unknown>
  fields: ChangedField[]
}

export interface ChangedField {
  fieldName: string
  oldValue: unknown
  newValue: unknown
}

export interface DiffResult {
  collections: CollectionDiff[]
  totalAdded: number
  totalRemoved: number
  totalModified: number
  totalUnchanged: number
}

// 版本合并相关类型
export interface MergeResult {
  success: boolean
  collections: {
    collection: string
    pageName: string
    recordsCreated: number
    recordsUpdated: number
    recordsDeleted: number
  }[]
}

// 版本恢复相关类型
export interface RestoreResult {
  success: boolean
  recordsCount: number
  relationsCount: number
}

// 删除影响报告相关类型
export interface DeleteImpactResult {
  versionInfo: {
    id: string
    projectMenuId: string
    name: string
    versionType: string
    status: string
    recordsCount: number
    isProtected: boolean
  }
  collections: {
    collection: string
    pageName: string
    recordCount: number
    records: { id: string; displayName: string }[]
    hasMore: boolean
  }[]
  usersOnBranch: string[]
  hasChildVersions: boolean
  childCount: number
  warningMessage: string
  canDelete: boolean
}

export function getProjectCollections(projectMenuId: string): Promise<{ collections: ProjectCollection[]; total: number }> {
  return request.get(`/project-versions/${projectMenuId}/collections`)
}

export function getCurrentProjectBranch(projectMenuId: string): Promise<CurrentBranch> {
  return request.get(`/project-versions/${projectMenuId}/current-branch`)
}

export function setCurrentProjectBranch(projectMenuId: string, branchId: string): Promise<{ branchId: string; success: boolean }> {
  return request.put(`/project-versions/${projectMenuId}/current-branch`, { branchId })
}

export function createProjectVersion(data: ProjectVersionFormData): Promise<ProjectVersion> {
  return request.post('/project-versions', data)
}

export function listProjectVersions(projectMenuId: string, page = 1, pageSize = 20): Promise<ProjectVersionListResult> {
  return request.get(`/project-versions/${projectMenuId}`, { params: { page, pageSize } })
}

export function switchProjectBranch(versionId: string, projectMenuId: string): Promise<SwitchBranchResult> {
  return request.post(`/project-versions/${versionId}/switch`, { projectMenuId })
}

export function getProjectVersionDetail(versionId: string): Promise<ProjectVersion> {
  return request.get(`/project-versions/${versionId}`)
}

export function deleteProjectVersion(versionId: string): Promise<{ success: boolean }> {
  return request.delete(`/project-versions/${versionId}`)
}

// 新增：版本对比
export function diffProjectVersions(
  projectMenuId: string,
  baseVersion: string,
  targetVersion: string
): Promise<DiffResult> {
  return request.post('/project-versions/diff', {
    projectMenuId,
    baseVersion,
    targetVersion,
  })
}

// 新增：版本合并
export function mergeProjectVersion(
  versionId: string,
  projectMenuId: string,
  targetBranch: string = 'current',
  strategy: 'theirs' | 'ours' = 'theirs'
): Promise<MergeResult> {
  return request.post('/project-versions/merge', {
    versionId,
    projectMenuId,
    targetBranch,
    strategy,
  })
}

// 新增：版本恢复
export function restoreProjectVersion(
  versionId: string,
  projectMenuId: string
): Promise<RestoreResult> {
  return request.post(`/project-versions/${versionId}/restore`, { projectMenuId })
}

// 新增：切换到主分支
export function switchToMainProjectBranch(projectMenuId: string): Promise<SwitchBranchResult> {
  return request.post(`/project-versions/${projectMenuId}/switch-main`)
}

// 新增：获取删除影响报告
export function getProjectVersionDeleteImpact(versionId: string): Promise<DeleteImpactResult> {
  return request.get(`/project-versions/${versionId}/delete-impact`)
}

// 锁定相关类型
export interface LockResult {
  success: boolean
  isLocked: boolean
  lockedAt?: string
  lockedBy?: string
}

// 新增：锁定分支
export function lockProjectVersion(versionId: string, reason?: string): Promise<LockResult> {
  return request.post(`/project-versions/${versionId}/lock`, { reason })
}

// 新增：解锁分支
export function unlockProjectVersion(versionId: string): Promise<LockResult> {
  return request.post(`/project-versions/${versionId}/unlock`)
}