/**
 * ETL API 层 - 单元测试
 *
 * 通过 mock request 模块来验证 API 函数调用正确的路径和参数。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock request 模块
vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
} from '../etl'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('ETL API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getEtlTasks 调用 GET /etlTasks', async () => {
    const tasks = [{ id: 'etl-1', name: '任务1' }]
    mockGet.mockResolvedValueOnce(tasks as any)

    const result = await getEtlTasks()
    expect(mockGet).toHaveBeenCalledWith('/etlTasks')
    expect(result).toEqual(tasks)
  })

  it('createEtlTask 调用 POST /etlTasks', async () => {
    const newTask = { name: '新任务', description: '描述' }
    const created = { id: 'etl-2', ...newTask }
    mockPost.mockResolvedValueOnce(created as any)

    const result = await createEtlTask(newTask)
    expect(mockPost).toHaveBeenCalledWith('/etlTasks', newTask)
    expect(result).toEqual(created)
  })

  it('updateEtlTask 调用 PUT /etlTasks/{id}', async () => {
    const updates = { name: '更新名称' }
    mockPut.mockResolvedValueOnce({ id: 'etl-1', ...updates } as any)

    await updateEtlTask('etl-1', updates)
    expect(mockPut).toHaveBeenCalledWith('/etlTasks/etl-1', updates)
  })

  it('deleteEtlTask 调用 DELETE /etlTasks/{id}', async () => {
    mockDel.mockResolvedValueOnce({ ok: true } as any)

    await deleteEtlTask('etl-1')
    expect(mockDel).toHaveBeenCalledWith('/etlTasks/etl-1')
  })

  it('runEtlTask 调用 POST /etlTasks/{id}/run', async () => {
    const runResult = { status: 'success', stepResults: [] }
    mockPost.mockResolvedValueOnce(runResult as any)

    await runEtlTask('etl-1')
    expect(mockPost).toHaveBeenCalledWith('/etlTasks/etl-1/run', { dryRun: false })
  })

  it('runEtlTask dryRun=true 正确传参', async () => {
    mockPost.mockResolvedValueOnce({ status: 'success' } as any)

    await runEtlTask('etl-1', { dryRun: true })
    expect(mockPost).toHaveBeenCalledWith('/etlTasks/etl-1/run', { dryRun: true })
  })

  it('getEtlLogs 调用 GET /etlTasks/{id}/logs', async () => {
    const logs = [{ id: 'log-1', taskId: 'etl-1' }]
    mockGet.mockResolvedValueOnce(logs as any)

    const result = await getEtlLogs('etl-1')
    expect(mockGet).toHaveBeenCalledWith('/etlTasks/etl-1/logs')
    expect(result).toEqual(logs)
  })
})
