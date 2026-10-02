/**
 * orchestration API 封装单测（P3-A7）
 *
 * 锁定与后端 server/routes/ai_orchestrations.py 的契约：
 * - 路径前缀 /ai/orchestrations（内部域，JWT）
 * - 列表集合键 definitions / runs；run 详情按 id 内嵌 steps
 * - runId 做 URL 编码
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
}))

import { get, post } from '@/utils/request'
import { listDefinitions, publishDefinition, listRuns, getRun } from '../orchestration'

const mockedGet = vi.mocked(get)
const mockedPost = vi.mocked(post)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('listDefinitions', () => {
  it('GET /ai/orchestrations/definitions', async () => {
    mockedGet.mockResolvedValueOnce({
      definitions: [{ id: 'orch_a', version: 2, name: 'A', description: null, publishedAt: null }],
    })
    await listDefinitions()
    expect(mockedGet).toHaveBeenCalledWith('/ai/orchestrations/definitions')
  })
})

describe('publishDefinition', () => {
  it('POST /ai/orchestrations/definitions，body 原样透传', async () => {
    mockedPost.mockResolvedValueOnce({ id: 'orch_a', version: 3 })
    const body = { name: 'A', nodes: [{ id: 'n1' }], edges: [] }
    const res = await publishDefinition(body)
    expect(mockedPost).toHaveBeenCalledWith('/ai/orchestrations/definitions', body)
    expect(res).toEqual({ id: 'orch_a', version: 3 })
  })
})

describe('listRuns', () => {
  it('GET /ai/orchestrations/runs', async () => {
    mockedGet.mockResolvedValueOnce({ runs: [] })
    await listRuns()
    expect(mockedGet).toHaveBeenCalledWith('/ai/orchestrations/runs')
  })
})

describe('getRun', () => {
  it('GET /ai/orchestrations/runs/<id>，runId 做 URL 编码', async () => {
    mockedGet.mockResolvedValueOnce({
      id: 'run_1', definition_id: 'orch_a', definition_version: 1, status: 'running',
      requested_by: 'u', error_code: null, createdAt: null, steps: [],
    })
    await getRun('run_1')
    expect(mockedGet).toHaveBeenCalledWith('/ai/orchestrations/runs/run_1')

    await getRun('run/x 1')
    expect(mockedGet).toHaveBeenCalledWith('/ai/orchestrations/runs/run%2Fx%201')
  })
})
