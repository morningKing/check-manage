import request from '@/utils/request'
import type { ProjectVersion, ProjectVersionFormData } from '@/types/version'

export interface BranchOption {
  id: string
  name: string
  projectMenuId?: string
  projectName?: string
  status?: string
}

export interface ProjectCollection {
  collection: string
  pageId: string
  pageName: string
  menuId: string
}

export interface CurrentBranch {
  branchId: string
  branchName: string
  mainLocked?: boolean
  mainLockedBy?: string
  currentLocked?: boolean
  currentLockedBy?: string
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

// 详细合并请求类型
export interface CollectionMergeDecision {
  collection: string
  added: string[]
  removed: string[]
  modified: {
    recordId: string
    fieldDecisions: {
      fieldName: string
      useSource: boolean
    }[]
  }[]
}

export interface MergePayload {
  versionId: string
  targetBranch: string
  collections: CollectionMergeDecision[]
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

export function getAllBranches(): Promise<BranchOption[]> {
  return request.get('/project-versions/all-branches')
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
  return request.get(`/project-versions/detail/${versionId}`)
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

// Main 分支锁定相关类型
export interface MainLockResult {
  success: boolean
  isLocked: boolean
  lockedAt?: string
  lockedBy?: string
  branchId: string
}

// 新增：锁定 main 分支
export function lockMainBranch(projectMenuId: string, reason?: string): Promise<MainLockResult> {
  return request.post(`/project-versions/main/${projectMenuId}/lock`, { reason })
}

// 合并历史相关类型
export interface MergeRecord {
  id: string
  sourceVersionId: string
  sourceVersionName: string
  targetBranchId: string
  targetBranchName: string
  strategy: string
  mergedBy: string
  mergedAt: string
  recordsCreated: number
  recordsUpdated: number
  recordsDeleted: number
  description?: string
}

export interface MergeHistoryResult {
  mergeRecords: MergeRecord[]
  total: number
}

export interface ProjectMergeRecordsResult {
  mergeRecords: MergeRecord[]
  total: number
  page: number
  pageSize: number
}

// 新增：获取版本的合并历史
export function getVersionMergeHistory(versionId: string): Promise<MergeHistoryResult> {
  return request.get(`/project-versions/${versionId}/merge-history`)
}

// 新增：获取项目的所有合并记录
export function getProjectMergeRecords(projectMenuId: string, page = 1, pageSize = 20): Promise<ProjectMergeRecordsResult> {
  return request.get(`/merge-records/${projectMenuId}`, { params: { page, pageSize } })
}

// 新增：解锁 main 分支
export function unlockMainBranch(projectMenuId: string): Promise<MainLockResult> {
  return request.post(`/project-versions/main/${projectMenuId}/unlock`)
}

// 新增：详细合并（支持按记录/字段选择）
export function mergeProjectVersionDetailed(
  payload: MergePayload,
  projectMenuId: string,
  skipDependencyCheck = false
): Promise<MergeResult> {
  return request.post('/project-versions/merge-detailed', {
    ...payload,
    projectMenuId,
    skipDependencyCheck,
  })
}