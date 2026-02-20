import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import {
  getValidationScripts,
  createValidationScript,
  updateValidationScript,
  deleteValidationScript,
  testValidationScript,
} from '../validationScript'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('ValidationScript API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getValidationScripts 调用 GET /validationScripts', async () => {
    const scripts = [{ id: 'vs-1', name: '校验1' }]
    mockGet.mockResolvedValueOnce(scripts as any)

    const res = await getValidationScripts()
    expect(mockGet).toHaveBeenCalledWith('/validationScripts')
    expect(res).toEqual(scripts)
  })

  it('createValidationScript 调用 POST /validationScripts', async () => {
    const data = { name: '新校验', script: 'return []' }
    mockPost.mockResolvedValueOnce({ id: 'vs-2', ...data } as any)

    const res = await createValidationScript(data)
    expect(mockPost).toHaveBeenCalledWith('/validationScripts', data)
    expect(res).toEqual({ id: 'vs-2', ...data })
  })

  it('updateValidationScript 调用 PUT /validationScripts/{id}', async () => {
    const data = { name: '更新校验' }
    mockPut.mockResolvedValueOnce({ id: 'vs-1', ...data } as any)

    await updateValidationScript('vs-1', data)
    expect(mockPut).toHaveBeenCalledWith('/validationScripts/vs-1', data)
  })

  it('deleteValidationScript 调用 DELETE /validationScripts/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteValidationScript('vs-1')
    expect(mockDel).toHaveBeenCalledWith('/validationScripts/vs-1')
  })

  it('testValidationScript 调用 POST /validationScripts/{id}/test', async () => {
    const testData = {
      record: { name: '测试' },
      action: 'create',
      fields: [{ fieldName: 'name', label: '名称' }],
      collection: 'testCol',
    }
    const result = { success: true, errors: [], warnings: [], pendingRelations: [] }
    mockPost.mockResolvedValueOnce(result as any)

    const res = await testValidationScript('vs-1', testData as any)
    expect(mockPost).toHaveBeenCalledWith('/validationScripts/vs-1/test', testData)
    expect(res).toEqual(result)
  })
})
