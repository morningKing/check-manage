import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock request 模块 - 在 vi.mock 内部定义 mock 函数
vi.mock('@/utils/request', () => {
  const mockGet = vi.fn()
  const mockPost = vi.fn()
  const mockPut = vi.fn()
  const mockDelete = vi.fn()
  return {
    default: {
      get: mockGet,
      post: mockPost,
      put: mockPut,
      delete: mockDelete,
    },
    get: mockGet,
    post: mockPost,
    put: mockPut,
    del: mockDelete,
  }
})

import request from '@/utils/request'
import {
  getProjectDependencies,
  createProjectDependency,
  updateProjectDependency,
  deleteProjectDependency,
  validateDependency,
  getDependentProjects,
  scanProjectRelations,
  checkBranchDeleteProtection,
  checkMergeDependencies,
  getCoordinatedMergeOrder,
  updateDependenciesAfterMerge,
} from '../crossProjectDependency'

const mockGet = vi.mocked(request.get)
const mockPost = vi.mocked(request.post)
const mockPut = vi.mocked(request.put)
const mockDelete = vi.mocked(request.delete)

describe('CrossProjectDependency API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ==================== 依赖声明管理 ====================

  describe('依赖声明管理', () => {
    it('getProjectDependencies 调用 GET /projects/:id/dependencies', async () => {
      const result = { dependencies: [], total: 0 }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getProjectDependencies('project-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/dependencies', {
        params: undefined,
      })
      expect(res).toEqual(result)
    })

    it('getProjectDependencies 支持分支参数', async () => {
      const result = { dependencies: [], total: 0 }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getProjectDependencies('project-1', 'branch-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/dependencies', {
        params: { branchId: 'branch-1' },
      })
      expect(res).toEqual(result)
    })

    it('createProjectDependency 调用 POST /projects/:id/dependencies', async () => {
      const data = {
        sourceBranch: 'main',
        targetProject: 'project-2',
        targetBranch: 'main',
        relationType: 'track-main' as const,
      }
      const result = { id: 'dep-1', ...data }
      mockPost.mockResolvedValueOnce(result as any)

      const res = await createProjectDependency('project-1', data)
      expect(mockPost).toHaveBeenCalledWith('/projects/project-1/dependencies', data)
      expect(res).toEqual(result)
    })

    it('updateProjectDependency 调用 PUT /projects/:id/dependencies/:depId', async () => {
      const data = { targetBranch: 'feat-new' }
      const result = { id: 'dep-1', targetBranch: 'feat-new' }
      mockPut.mockResolvedValueOnce(result as any)

      const res = await updateProjectDependency('project-1', 'dep-1', data)
      expect(mockPut).toHaveBeenCalledWith('/projects/project-1/dependencies/dep-1', data)
      expect(res).toEqual(result)
    })

    it('deleteProjectDependency 调用 DELETE /projects/:id/dependencies/:depId', async () => {
      mockDelete.mockResolvedValueOnce({ success: true })

      await deleteProjectDependency('project-1', 'dep-1')
      expect(mockDelete).toHaveBeenCalledWith('/projects/project-1/dependencies/dep-1')
    })

    it('validateDependency 调用 POST /dependencies/:id/validate', async () => {
      const result = { isValid: true, errors: [], warnings: [] }
      mockPost.mockResolvedValueOnce(result as any)

      const res = await validateDependency('dep-1')
      expect(mockPost).toHaveBeenCalledWith('/dependencies/dep-1/validate')
      expect(res).toEqual(result)
    })
  })

  // ==================== 反向查询 ====================

  describe('反向查询', () => {
    it('getDependentProjects 调用 GET /projects/:id/dependents', async () => {
      const result = { dependents: [], total: 0 }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getDependentProjects('project-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/dependents', {
        params: undefined,
      })
      expect(res).toEqual(result)
    })

    it('getDependentProjects 支持分支参数', async () => {
      const result = { dependents: [], total: 0 }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getDependentProjects('project-1', 'branch-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/dependents', {
        params: { branchId: 'branch-1' },
      })
      expect(res).toEqual(result)
    })
  })

  // ==================== 关联关系扫描 ====================

  describe('关联关系扫描', () => {
    it('scanProjectRelations 调用 GET /projects/:source/scan-relations/:target', async () => {
      const result = { relations: [], total: 0 }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await scanProjectRelations('project-1', 'project-2')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/scan-relations/project-2')
      expect(res).toEqual(result)
    })
  })

  // ==================== 分支删除保护 ====================

  describe('分支删除保护', () => {
    it('checkBranchDeleteProtection 调用 GET /projects/:id/branches/:branchId/delete-check', async () => {
      const result = { canDelete: true, dependentProjects: [], blockingDependencies: [] }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await checkBranchDeleteProtection('project-1', 'branch-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/branches/branch-1/delete-check')
      expect(res).toEqual(result)
    })
  })

  // ==================== 联合合并编排 ====================

  describe('联合合并编排', () => {
    it('checkMergeDependencies 调用 GET /projects/:id/merge-check', async () => {
      const result = {
        canMerge: true,
        blockingDependencies: [],
        readyDependencies: [],
        trackMainDependencies: [],
        readOnlyDependencies: [],
      }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await checkMergeDependencies('project-1', 'branch-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/merge-check', {
        params: { sourceBranch: 'branch-1' },
      })
      expect(res).toEqual(result)
    })

    it('checkMergeDependencies 返回阻塞依赖', async () => {
      const result = {
        canMerge: false,
        blockingDependencies: [
          {
            id: 'dep-1',
            targetProject: 'project-2',
            targetBranch: 'branch-feat',
            relationType: 'read-write',
            reason: '目标分支尚未合并',
          },
        ],
        readyDependencies: [],
        trackMainDependencies: [],
        readOnlyDependencies: [],
      }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await checkMergeDependencies('project-1', 'branch-1')
      expect(res.canMerge).toBe(false)
      expect(res.blockingDependencies.length).toBe(1)
    })

    it('getCoordinatedMergeOrder 调用 GET /projects/:id/merge-order', async () => {
      const result = {
        mergeOrder: [
          {
            order: 1,
            projectMenuId: 'project-1',
            projectName: '项目A',
            sourceBranch: 'branch-1',
            sourceBranchName: '特性分支',
            dependencyCount: 0,
          },
        ],
        total: 1,
      }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getCoordinatedMergeOrder('project-1', 'branch-1')
      expect(mockGet).toHaveBeenCalledWith('/projects/project-1/merge-order', {
        params: { sourceBranch: 'branch-1' },
      })
      expect(res).toEqual(result)
    })

    it('getCoordinatedMergeOrder 返回多个合并顺序', async () => {
      const result = {
        mergeOrder: [
          {
            order: 1,
            projectMenuId: 'project-2',
            projectName: '项目B',
            sourceBranch: 'branch-feat',
            sourceBranchName: '特性分支',
            dependencyCount: 0,
          },
          {
            order: 2,
            projectMenuId: 'project-1',
            projectName: '项目A',
            sourceBranch: 'branch-1',
            sourceBranchName: '开发分支',
            dependencyCount: 1,
          },
        ],
        total: 2,
      }
      mockGet.mockResolvedValueOnce(result as any)

      const res = await getCoordinatedMergeOrder('project-1', 'branch-1')
      expect(res.total).toBe(2)
      expect(res.mergeOrder.length).toBe(2)
    })

    it('updateDependenciesAfterMerge 调用 POST /projects/:id/update-dependencies-after-merge', async () => {
      const result = { success: true, updatedCount: 2 }
      mockPost.mockResolvedValueOnce(result)

      const res = await updateDependenciesAfterMerge('project-1', 'branch-1')
      expect(mockPost).toHaveBeenCalledWith('/projects/project-1/update-dependencies-after-merge', {
        sourceBranch: 'branch-1',
      })
      expect(res).toEqual(result)
    })

    it('updateDependenciesAfterMerge 返回更新数量', async () => {
      const result = { success: true, updatedCount: 0 }
      mockPost.mockResolvedValueOnce(result)

      const res = await updateDependenciesAfterMerge('project-1', 'branch-1')
      expect(res.updatedCount).toBe(0)
    })
  })
})