/**
 * Axios 请求封装 - 单元测试
 *
 * 通过 mock axios 来测试请求函数和拦截器行为。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// 使用 vi.hoisted 让变量在 mock 工厂中可用
const { mockInstance } = vi.hoisted(() => {
  const mockInstance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  }
  return { mockInstance }
})

// Mock element-plus ElMessage
vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

// Mock batch headers
vi.mock('../batch', () => ({
  getBatchHeaders: vi.fn(() => ({})),
}))

// Mock axios
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => mockInstance),
  },
}))

import { get, post, put, del } from '../request'

describe('request 封装', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('get 调用 axios.get', async () => {
    const data = [{ id: 1, name: 'test' }]
    mockInstance.get.mockResolvedValueOnce(data)

    const result = await get('/test')
    expect(mockInstance.get).toHaveBeenCalledWith('/test', { params: undefined })
    expect(result).toEqual(data)
  })

  it('get 传递 params', async () => {
    mockInstance.get.mockResolvedValueOnce([])
    await get('/test', { page: 1 })
    expect(mockInstance.get).toHaveBeenCalledWith('/test', { params: { page: 1 } })
  })

  it('post 调用 axios.post', async () => {
    const payload = { name: 'new' }
    const resp = { id: '1', name: 'new' }
    mockInstance.post.mockResolvedValueOnce(resp)

    const result = await post('/test', payload)
    expect(mockInstance.post).toHaveBeenCalledWith('/test', payload, undefined)
    expect(result).toEqual(resp)
  })

  it('put 调用 axios.put', async () => {
    const payload = { name: 'updated' }
    mockInstance.put.mockResolvedValueOnce({ ok: true })

    await put('/test/1', payload)
    expect(mockInstance.put).toHaveBeenCalledWith('/test/1', payload, undefined)
  })

  it('del 调用 axios.delete', async () => {
    mockInstance.delete.mockResolvedValueOnce({ ok: true })

    await del('/test/1')
    expect(mockInstance.delete).toHaveBeenCalledWith('/test/1', undefined)
  })

  it('请求失败时 reject', async () => {
    mockInstance.get.mockRejectedValueOnce(new Error('Network Error'))
    await expect(get('/fail')).rejects.toThrow('Network Error')
  })
})

describe('请求拦截器注册', () => {
  it('注册了 request 和 response 拦截器', () => {
    expect(mockInstance.interceptors.request.use).toHaveBeenCalled()
    expect(mockInstance.interceptors.response.use).toHaveBeenCalled()
  })

  describe('request 拦截器', () => {
    let requestInterceptor: (config: any) => any

    beforeEach(() => {
      requestInterceptor = mockInstance.interceptors.request.use.mock.calls[0][0]
    })

    it('localStorage 有 token 时注入 Authorization', () => {
      const token = 'test-token-123'
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue(JSON.stringify(token))

      const config = { headers: {} as Record<string, string> }
      const result = requestInterceptor(config)
      expect(result.headers.Authorization).toBe(`Bearer ${token}`)

      vi.restoreAllMocks()
    })

    it('localStorage 无 token 时不注入', () => {
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue(null)

      const config = { headers: {} as Record<string, string> }
      const result = requestInterceptor(config)
      expect(result.headers.Authorization).toBeUndefined()

      vi.restoreAllMocks()
    })
  })

  describe('response 拦截器', () => {
    let responseSuccess: (response: any) => any
    let responseError: (error: any) => any

    beforeEach(() => {
      responseSuccess = mockInstance.interceptors.response.use.mock.calls[0][0]
      responseError = mockInstance.interceptors.response.use.mock.calls[0][1]
    })

    it('成功响应返回 response.data', () => {
      const response = { data: { id: 1 }, config: {} }
      const result = responseSuccess(response)
      expect(result).toEqual({ id: 1 })
    })

    it('blob 响应返回完整 response', () => {
      const response = { data: new Blob(), config: { responseType: 'blob' } }
      const result = responseSuccess(response)
      expect(result).toBe(response)
    })

    it('错误响应 reject', async () => {
      const error = {
        response: { status: 500, data: {} },
        message: 'Server Error',
      }
      await expect(responseError(error)).rejects.toBe(error)
    })
  })
})
