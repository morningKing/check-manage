/**
 * 版本管理 API 接口
 */
import { get, post, del } from '@/utils/request'
import type {
  CollectionVersion,
  CreateVersionRequest,
  DiffVersionRequest,
  MergeVersionRequest,
  MergeResult,
  RestoreResult,
  PartialMergeRequest,
  PartialMergeResponse,
  PaginatedVersionsResponse,
} from '@/types'
import type { DiffResult } from '@/types'

/**
 * 获取版本列表（支持分页和搜索）
 * 重载签名用于 TypeScript 类型推断
 */

// 无分页时返回数组
export function getVersions(
  collection?: string,
  status?: string
): Promise<CollectionVersion[]>

// 有分页时返回分页响应
export function getVersions(
  collection?: string,
  status?: string,
  page?: number,
  pageSize?: number,
  keyword?: string
): Promise<PaginatedVersionsResponse>

// 实现
export function getVersions(
  collection?: string,
  status?: string,
  page?: number,
  pageSize?: number,
  keyword?: string
): Promise<CollectionVersion[] | PaginatedVersionsResponse> {
  const params: Record<string, string | number> = {}
  if (collection) params.collection = collection
  if (status) params.status = status
  if (page) params.page = page
  if (pageSize) params.pageSize = pageSize
  if (keyword) params.keyword = keyword

  // If pagination requested, expect paginated response
  if (page) {
    return get<PaginatedVersionsResponse>('/versions', params)
  }

  // Backward compatible: return array
  return get<CollectionVersion[]>('/versions', params)
}

/**
 * 获取当前工作分支
 */
export interface UserBranch {
  branchId: string | null
  branchName: string
}

export function getCurrentBranch(collection: string) {
  return get<UserBranch>('/versions/user-branch', { collection })
}

/**
 * 创建版本快照
 */
export function createVersion(data: CreateVersionRequest) {
  return post<CollectionVersion>('/versions', data)
}

/**
 * 获取版本详情
 */
export function getVersion(id: string) {
  return get<CollectionVersion>(`/versions/${id}`)
}

/**
 * 删除版本
 */
export function deleteVersion(id: string) {
  return del(`/versions/${id}`)
}

/**
 * 对比两个版本
 */
export function diffVersions(data: DiffVersionRequest) {
  return post<DiffResult>('/versions/diff', data)
}

/**
 * 合并版本到当前数据
 */
export function mergeVersion(data: MergeVersionRequest) {
  return post<MergeResult>('/versions/merge', data)
}

/**
 * 从版本恢复数据
 */
export function restoreVersion(id: string) {
  return post<RestoreResult>(`/versions/${id}/restore`, {})
}

/**
 * 切换到指定版本
 */
export interface SwitchResult {
  success: boolean
  branchId: string
  branchName: string
  recordsInBranch: number
  initialized: boolean
  affectedCollections?: string[]
}

export function switchToVersion(id: string) {
  return post<SwitchResult>(`/versions/${id}/switch`, {})
}

/**
 * 切换到主分支
 */
export function switchToMainBranch(collection: string) {
  return post<SwitchResult>('/versions/switch-main', { collection })
}

/**
 * 部分合并版本
 */
export function partialMergeVersion(data: PartialMergeRequest) {
  return post<PartialMergeResponse>('/versions/partial-merge', data)
}