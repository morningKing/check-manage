import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import { getPageConfigList, getPageConfigById, createPageConfig, updatePageConfig, deletePageConfig } from '../page'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('Page Config API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getPageConfigList 调用 GET /pageConfigs', async () => {
    const configs = [{ id: 'page-1', name: '测试页' }]
    mockGet.mockResolvedValueOnce(configs as any)

    const res = await getPageConfigList()
    expect(mockGet).toHaveBeenCalledWith('/pageConfigs')
    expect(res).toEqual(configs)
  })

  it('getPageConfigById 调用 GET /pageConfigs/{id}', async () => {
    const config = { id: 'page-1', name: '测试页' }
    mockGet.mockResolvedValueOnce(config as any)

    const res = await getPageConfigById('page-1')
    expect(mockGet).toHaveBeenCalledWith('/pageConfigs/page-1')
    expect(res).toEqual(config)
  })

  it('createPageConfig 调用 POST /pageConfigs', async () => {
    const config = { name: '新页面', description: '描述', apiEndpoint: '/test', fields: [] }
    mockPost.mockResolvedValueOnce({ id: 'page-2', ...config } as any)

    const res = await createPageConfig(config as any)
    expect(mockPost).toHaveBeenCalledWith('/pageConfigs', config)
    expect(res).toEqual({ id: 'page-2', ...config })
  })

  it('updatePageConfig 调用 PUT /pageConfigs/{id}', async () => {
    const updates = { name: '更新页面' }
    mockPut.mockResolvedValueOnce({ id: 'page-1', ...updates } as any)

    await updatePageConfig('page-1', updates)
    expect(mockPut).toHaveBeenCalledWith('/pageConfigs/page-1', { ...updates, id: 'page-1' })
  })

  it('deletePageConfig 调用 DELETE /pageConfigs/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deletePageConfig('page-1')
    expect(mockDel).toHaveBeenCalledWith('/pageConfigs/page-1')
  })
})
