/**
 * 跨项目依赖类型定义
 */

// ==================== 依赖类型常量 ====================

export type DependencyRelationType = 'track-main' | 'read-write' | 'read-only'

export type ValidationStatus = 'valid' | 'broken' | 'warning' | 'unknown'

export type EventSeverity = 'critical' | 'warning' | 'info'

export type EventType = 'schema_change' | 'branch_delete' | 'version_merge' | 'validation_fail'

// ==================== 依赖类型标签 ====================

export const DEPENDENCY_TYPE_LABELS: Record<DependencyRelationType, string> = {
  'track-main': '跟随主干',
  'read-write': '配套分支',
  'read-only': '精确钉住',
}

export const DEPENDENCY_TYPE_TAG_TYPES: Record<DependencyRelationType, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  'track-main': 'success',
  'read-write': 'warning',
  'read-only': 'info',
}

export const VALIDATION_STATUS_LABELS: Record<ValidationStatus, string> = {
  valid: '有效',
  broken: '断裂',
  warning: '警告',
  unknown: '未校验',
}

export const VALIDATION_STATUS_TAG_TYPES: Record<ValidationStatus, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  valid: 'success',
  broken: 'danger',
  warning: 'warning',
  unknown: 'info',
}

// ==================== 核心数据结构 ====================

/**
 * 项目依赖声明
 */
export interface ProjectDependency {
  id: string
  sourceProject: string
  sourceBranch: string
  targetProject: string
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion?: string
  isValidated: boolean
  validationError?: string
  declaredBy: string
  declaredAt: string
  updatedAt: string
  // 关联的项目信息（运行时填充）
  sourceProjectName?: string
  targetProjectName?: string
  relations?: DependencyRelation[]
}

/**
 * 依赖涉及的关联关系
 */
export interface DependencyRelation {
  id: string
  dependencyId: string
  sourceCollection: string
  sourceField: string
  targetCollection: string
  estimatedRecords: number
  validationStatus: ValidationStatus
  validationDetail?: string
  validatedAt?: string
  controlType?: string
}

/**
 * 依赖变更事件
 */
export interface DependencyEvent {
  id: string
  eventType: EventType
  sourceProject?: string
  sourceBranch?: string
  affectedDependencies: string[]
  severity: EventSeverity
  message: string
  createdAt: string
  resolvedAt?: string
  resolvedBy?: string
}

// ==================== API请求/响应类型 ====================

/**
 * 创建依赖声明请求
 */
export interface CreateDependencyRequest {
  sourceBranch: string
  targetProject: string
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion?: string
}

/**
 * 更新依赖声明请求
 */
export interface UpdateDependencyRequest {
  targetBranch?: string
  relationType?: DependencyRelationType
  pinnedVersion?: string
}

/**
 * 依赖校验结果
 */
export interface ValidationResult {
  isValid: boolean
  errors: string[]
  warnings: string[]
  relationValidations: Array<{
    relationId: string
    sourceCollection: string
    sourceField: string
    status: ValidationStatus
    detail?: string
  }>
}

/**
 * 分支删除保护检查结果
 */
export interface DeleteProtectionResult {
  canDelete: boolean
  dependentProjects: Array<{
    projectId: string
    projectName: string
    branchId: string
  }>
  blockingDependencies: ProjectDependency[]
}

/**
 * 关联关系扫描结果
 */
export interface ScanRelationsResult {
  relations: Array<{
    sourceCollection: string
    sourceField: string
    targetCollection: string
    controlType: string
  }>
  total: number
}

/**
 * 依赖列表响应
 */
export interface DependencyListResponse {
  dependencies: ProjectDependency[]
  total: number
}

/**
 * 依赖方列表响应（反向查询）
 */
export interface DependentListResponse {
  dependents: ProjectDependency[]
  total: number
}

// ==================== 联合合并类型 ====================

/**
 * 合并依赖检查结果
 */
export interface MergeDependencyCheckResult {
  canMerge: boolean
  blockingDependencies: BlockingDependency[]
  readyDependencies: ReadyDependency[]
  trackMainDependencies: TrackMainDependency[]
  readOnlyDependencies: ReadOnlyDependency[]
}

/**
 * 阻塞依赖
 */
export interface BlockingDependency {
  id: string
  targetProject: string
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion?: string
  targetProjectName?: string
  reason: string
}

/**
 * 就绪依赖
 */
export interface ReadyDependency {
  id: string
  targetProject: string
  targetBranch: string
  relationType: DependencyRelationType
  pinnedVersion?: string
  targetProjectName?: string
}

/**
 * track-main 依赖
 */
export interface TrackMainDependency {
  id: string
  targetProject: string
  targetBranch: string
  relationType: 'track-main'
  pinnedVersion?: string
  targetProjectName?: string
}

/**
 * read-only 依赖
 */
export interface ReadOnlyDependency {
  id: string
  targetProject: string
  targetBranch: string
  relationType: 'read-only'
  pinnedVersion?: string
  targetProjectName?: string
}

/**
 * 联合合并顺序
 */
export interface CoordinatedMergeOrder {
  order: number
  projectMenuId: string
  projectName: string
  sourceBranch: string
  sourceBranchName: string
  dependencyCount: number
}

/**
 * 联合合并顺序响应
 */
export interface MergeOrderResponse {
  mergeOrder: CoordinatedMergeOrder[]
  total: number
}