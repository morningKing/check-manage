/**
 * 跨项目依赖 API 调用模块
 */

import request from '@/utils/request'
import type {
  ProjectDependency,
  CreateDependencyRequest,
  UpdateDependencyRequest,
  ValidationResult,
  DeleteProtectionResult,
  ScanRelationsResult,
  DependencyListResponse,
  DependentListResponse,
  MergeDependencyCheckResult,
  MergeOrderResponse,
} from '@/types/crossProjectDependency'

// Re-export types for convenience
export type {
  ProjectDependency,
  CreateDependencyRequest,
  UpdateDependencyRequest,
  ValidationResult,
  DeleteProtectionResult,
  ScanRelationsResult,
  DependencyListResponse,
  DependentListResponse,
  MergeDependencyCheckResult,
  MergeOrderResponse,
}

// ==================== 依赖声明管理 ====================

/**
 * 获取项目的依赖列表
 */
export function getProjectDependencies(
  projectMenuId: string,
  branchId?: string
): Promise<DependencyListResponse> {
  return request.get(`/projects/${projectMenuId}/dependencies`, {
    params: branchId ? { branchId } : undefined,
  })
}

/**
 * 创建依赖声明
 */
export function createProjectDependency(
  projectMenuId: string,
  data: CreateDependencyRequest
): Promise<ProjectDependency> {
  return request.post(`/projects/${projectMenuId}/dependencies`, data)
}

/**
 * 更新依赖声明
 */
export function updateProjectDependency(
  projectMenuId: string,
  dependencyId: string,
  data: UpdateDependencyRequest
): Promise<ProjectDependency> {
  return request.put(`/projects/${projectMenuId}/dependencies/${dependencyId}`, data)
}

/**
 * 解除依赖声明
 */
export function deleteProjectDependency(
  projectMenuId: string,
  dependencyId: string
): Promise<{ success: boolean }> {
  return request.delete(`/projects/${projectMenuId}/dependencies/${dependencyId}`)
}

/**
 * 获取依赖声明详情
 */
export function getDependencyDetail(dependencyId: string): Promise<ProjectDependency> {
  return request.get(`/dependencies/${dependencyId}`)
}

// ==================== 依赖校验 ====================

/**
 * 触发依赖校验
 */
export function validateDependency(dependencyId: string): Promise<ValidationResult> {
  return request.post(`/dependencies/${dependencyId}/validate`)
}

// ==================== 反向查询 ====================

/**
 * 获取依赖于此项目的项目列表
 */
export function getDependentProjects(
  projectMenuId: string,
  branchId?: string
): Promise<DependentListResponse> {
  return request.get(`/projects/${projectMenuId}/dependents`, {
    params: branchId ? { branchId } : undefined,
  })
}

// ==================== 关联关系扫描 ====================

/**
 * 扫描源项目与目标项目之间的关联关系
 */
export function scanProjectRelations(
  sourceProject: string,
  targetProject: string
): Promise<ScanRelationsResult> {
  return request.get(`/projects/${sourceProject}/scan-relations/${targetProject}`)
}

// ==================== 分支删除保护 ====================

/**
 * 检查分支是否可删除
 */
export function checkBranchDeleteProtection(
  projectMenuId: string,
  branchId: string
): Promise<DeleteProtectionResult> {
  return request.get(`/projects/${projectMenuId}/branches/${branchId}/delete-check`)
}

// ==================== 联合合并编排 ====================

/**
 * 检查合并前的依赖状态
 */
export function checkMergeDependencies(
  projectMenuId: string,
  sourceBranch: string
): Promise<MergeDependencyCheckResult> {
  return request.get(`/projects/${projectMenuId}/merge-check`, {
    params: { sourceBranch },
  })
}

/**
 * 获取联合合并的执行顺序
 */
export function getCoordinatedMergeOrder(
  projectMenuId: string,
  sourceBranch: string
): Promise<MergeOrderResponse> {
  return request.get(`/projects/${projectMenuId}/merge-order`, {
    params: { sourceBranch },
  })
}

/**
 * 合并成功后批量更新依赖声明
 */
export function updateDependenciesAfterMerge(
  projectMenuId: string,
  sourceBranch: string
): Promise<{ success: boolean; updatedCount: number }> {
  return request.post(`/projects/${projectMenuId}/update-dependencies-after-merge`, {
    sourceBranch,
  })
}