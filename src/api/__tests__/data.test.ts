import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import { getDataList, getDataById, createData, updateData, deleteData, batchDeleteData } from '../data'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('Data API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getDataList 调用 GET {endpoint}', async () => {
    const data = [{ id: 'rec-1' }]
    mockGet.mockResolvedValueOnce(data as any)

    const res = await getDataList('/testData', { page: 1 })
    expect(mockGet).toHaveBeenCalledWith('/testData', { page: 1 })
    expect(res).toEqual(data)
  })

  it('getDataList 不传 params 时只传 endpoint', async () => {
    mockGet.mockResolvedValueOnce([] as any)

    await getDataList('/testData')
    expect(mockGet).toHaveBeenCalledWith('/testData', undefined)
  })

  it('getDataById 调用 GET {endpoint}/{id}', async () => {
    const record = { id: 'rec-1', name: '数据1' }
    mockGet.mockResolvedValueOnce(record as any)

    const res = await getDataById('/testData', 'rec-1')
    expect(mockGet).toHaveBeenCalledWith('/testData/rec-1')
    expect(res).toEqual(record)
  })

  it('createData 调用 POST {endpoint}', async () => {
    const data = { name: '新数据' }
    mockPost.mockResolvedValueOnce({ id: 'rec-2', ...data } as any)

    const res = await createData('/testData', data)
    expect(mockPost).toHaveBeenCalledWith('/testData', data)
    expect(res).toEqual({ id: 'rec-2', ...data })
  })

  it('updateData 调用 PUT {endpoint}/{id}', async () => {
    const updates = { name: '更新数据' }
    mockPut.mockResolvedValueOnce({ id: 'rec-1', ...updates } as any)

    await updateData('/testData', 'rec-1', updates)
    expect(mockPut).toHaveBeenCalledWith('/testData/rec-1', { ...updates, id: 'rec-1' })
  })

  it('deleteData 调用 DELETE {endpoint}/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteData('/testData', 'rec-1')
    expect(mockDel).toHaveBeenCalledWith('/testData/rec-1')
  })

  it('batchDeleteData 并行调用 DELETE', async () => {
    mockDel.mockResolvedValue({} as any)

    const ids = ['rec-1', 'rec-2', 'rec-3']
    await batchDeleteData('/testData', ids)

    expect(mockDel).toHaveBeenCalledTimes(3)
    expect(mockDel).toHaveBeenCalledWith('/testData/rec-1')
    expect(mockDel).toHaveBeenCalledWith('/testData/rec-2')
    expect(mockDel).toHaveBeenCalledWith('/testData/rec-3')
  })

  it('batchDeleteData 空数组不调用 DELETE', async () => {
    await batchDeleteData('/testData', [])
    expect(mockDel).not.toHaveBeenCalled()
  })
})
