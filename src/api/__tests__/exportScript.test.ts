import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { get, post, put, del } from '@/utils/request'
import { getExportScripts, createExportScript, updateExportScript, deleteExportScript, testExportScript } from '../exportScript'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('ExportScript API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getExportScripts 调用 GET /exportScripts', async () => {
    const scripts = [{ id: 's1', name: '脚本1' }]
    mockGet.mockResolvedValueOnce(scripts as any)

    const res = await getExportScripts()
    expect(mockGet).toHaveBeenCalledWith('/exportScripts')
    expect(res).toEqual(scripts)
  })

  it('createExportScript 调用 POST /exportScripts', async () => {
    const data = { name: '新脚本', script: 'print(1)', language: 'python' }
    mockPost.mockResolvedValueOnce({ id: 's2', ...data } as any)

    const res = await createExportScript(data)
    expect(mockPost).toHaveBeenCalledWith('/exportScripts', data)
    expect(res).toEqual({ id: 's2', ...data })
  })

  it('updateExportScript 调用 PUT /exportScripts/{id}', async () => {
    const data = { name: '更新脚本' }
    mockPut.mockResolvedValueOnce({ id: 's1', ...data } as any)

    await updateExportScript('s1', data)
    expect(mockPut).toHaveBeenCalledWith('/exportScripts/s1', data)
  })

  it('deleteExportScript 调用 DELETE /exportScripts/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteExportScript('s1')
    expect(mockDel).toHaveBeenCalledWith('/exportScripts/s1')
  })

  it('testExportScript 调用 POST /exportScripts/{id}/test', async () => {
    const testData = { data: [{ id: '1' }], fields: [], pageName: '测试页' }
    const result = { success: true, preview: '...', filename: 'test.json', contentType: 'application/json', size: 100 }
    mockPost.mockResolvedValueOnce(result as any)

    const res = await testExportScript('s1', testData)
    expect(mockPost).toHaveBeenCalledWith('/exportScripts/s1/test', testData)
    expect(res).toEqual(result)
  })
})
