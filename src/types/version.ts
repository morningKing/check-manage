/**
 * 版本管理相关类型定义
 */

import type { DiffResult } from './backup'

export type VersionType = 'snapshot' | 'branch'
export type VersionStatus = 'active' | 'merged' | 'archived'
export type MergeStrategy = 'theirs' | 'ours'

/**
 * 集合版本
 */
export interface CollectionVersion {
  id: string
  collection: string
  name: string
  description?: string
  versionType: VersionType
  parentVersion?: string
  status: VersionStatus
  dataHash: string
  recordsCount: number
  relationsCount: number
  createdBy: string
  createdAt: string
  mergedAt?: string
  mergedBy?: string
  mergedInto?: string
  isProtected: boolean
  lockedBy?: string
  lockedAt?: string
}

/**
 * 创建版本请求
 */
export interface CreateVersionRequest {
  collection: string
  name: string
  description?: string
  versionType?: VersionType
  parentVersion?: string
}

/**
 * 版本对比请求
 */
export interface DiffVersionRequest {
  collection: string
  baseVersion: string  // 'current' 或版本 ID
  targetVersion: string
}

/**
 * 版本合并请求
 */
export interface MergeVersionRequest {
  sourceVersion: string
  targetVersion?: string  // 目前只支持 'current'
  strategy?: MergeStrategy
}

/**
 * 版本合并结果
 */
export interface MergeResult {
  success: boolean
  summary: {
    recordsCreated: number
    recordsUpdated: number
    recordsDeleted: number
  }
}

/**
 * 版本恢复结果
 */
export interface RestoreResult {
  success: boolean
  recordsCount: number
  relationsCount: number
}

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
 * 部分合并决策
 */
export interface PartialMergeDecisions {
  added_record_ids: string[]
  removed_record_ids: string[]
  modified_records: ModifiedRecordDecision[]
}

/**
 * 修改记录的字段决策
 */
export interface ModifiedRecordDecision {
  record_id: string
  field_values: Record<string, any>
}

/**
 * 部分合并请求
 */
export interface PartialMergeRequest {
  source_version_id: string
  target_branch: string
  decisions: PartialMergeDecisions
}

/**
 * 部分合并响应
 */
export interface PartialMergeResponse {
  success: boolean
  merged_count: number
  message: string
}

/**
 * 合并步骤
 */
export type MergeStep = 'overview' | 'records' | 'fields'

/**
 * 合并决策状态
 */
export interface MergeDecisions {
  addedRecords: Set<string>
  removedRecords: Set<string>
  modifiedRecords: Map<string, {
    recordId: string
    fieldDecisions: Map<string, 'source' | 'target'>
  }>
}

/**
 * 合并状态
 */
export interface MergeState {
  step: MergeStep
  sourceVersion: CollectionVersion | null
  targetBranch: string
  diffResult: DiffResult | null
  decisions: MergeDecisions
}