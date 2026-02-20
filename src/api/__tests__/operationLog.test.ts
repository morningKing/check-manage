import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  default: {
    get: vi.fn(),
  },
}))

import { get, del } from '@/utils/request'
import { getOperationLogs, deleteOperationLog } from '../operationLog'

const mockGet = vi.mocked(get)
const mockDel = vi.mocked(del)

describe('OperationLog API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getOperationLogs 调用 GET /operationLogs', async () => {
    const query = { page: 1, pageSize: 20 }
    const result = { items: [], total: 0 }
    mockGet.mockResolvedValueOnce(result as any)

    const res = await getOperationLogs(query as any)
    expect(mockGet).toHaveBeenCalledWith('/operationLogs', query)
    expect(res).toEqual(result)
  })

  it('getOperationLogs 带筛选参数', async () => {
    const query = { page: 1, pageSize: 10, action: 'create', targetType: 'menu' }
    mockGet.mockResolvedValueOnce({ items: [], total: 0 } as any)

    await getOperationLogs(query as any)
    expect(mockGet).toHaveBeenCalledWith('/operationLogs', query)
  })

  it('deleteOperationLog 调用 DELETE /operationLogs/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteOperationLog('log-1')
    expect(mockDel).toHaveBeenCalledWith('/operationLogs/log-1')
  })
})
