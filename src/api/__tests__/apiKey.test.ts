import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import { getApiKeyList, createApiKey, toggleApiKey, deleteApiKey } from '../apiKey'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('ApiKey API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getApiKeyList 调用 GET /apiKeys', async () => {
    const keys = [{ id: 'ak-1', name: '密钥1' }]
    mockGet.mockResolvedValueOnce(keys as any)

    const res = await getApiKeyList()
    expect(mockGet).toHaveBeenCalledWith('/apiKeys')
    expect(res).toEqual(keys)
  })

  it('createApiKey 调用 POST /apiKeys', async () => {
    const data = { name: '新密钥' }
    mockPost.mockResolvedValueOnce({ id: 'ak-2', key: 'cm_xxx', ...data } as any)

    const res = await createApiKey(data)
    expect(mockPost).toHaveBeenCalledWith('/apiKeys', data)
    expect(res).toEqual({ id: 'ak-2', key: 'cm_xxx', ...data })
  })

  it('toggleApiKey 调用 PUT /apiKeys/{id}', async () => {
    mockPut.mockResolvedValueOnce({ id: 'ak-1', isActive: false } as any)

    await toggleApiKey('ak-1', false)
    expect(mockPut).toHaveBeenCalledWith('/apiKeys/ak-1', { isActive: false })
  })

  it('toggleApiKey 启用密钥', async () => {
    mockPut.mockResolvedValueOnce({ id: 'ak-1', isActive: true } as any)

    await toggleApiKey('ak-1', true)
    expect(mockPut).toHaveBeenCalledWith('/apiKeys/ak-1', { isActive: true })
  })

  it('deleteApiKey 调用 DELETE /apiKeys/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteApiKey('ak-1')
    expect(mockDel).toHaveBeenCalledWith('/apiKeys/ak-1')
  })
})
