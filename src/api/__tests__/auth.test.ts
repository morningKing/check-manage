import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put } from '@/utils/request'
import { login, getCurrentUser, changePassword } from '../auth'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)

describe('Auth API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('login 调用 POST /auth/login', async () => {
    const params = { username: 'admin', password: '123456' }
    const result = { token: 'jwt-token', user: { id: 'u1', username: 'admin' } }
    mockPost.mockResolvedValueOnce(result as any)

    const res = await login(params)
    expect(mockPost).toHaveBeenCalledWith('/auth/login', params)
    expect(res).toEqual(result)
  })

  it('getCurrentUser 调用 GET /auth/me', async () => {
    const user = { id: 'u1', username: 'admin', role: 'admin' }
    mockGet.mockResolvedValueOnce(user as any)

    const res = await getCurrentUser()
    expect(mockGet).toHaveBeenCalledWith('/auth/me')
    expect(res).toEqual(user)
  })

  it('changePassword 调用 PUT /auth/password', async () => {
    const data = { oldPassword: 'old123', newPassword: 'new123' }
    mockPut.mockResolvedValueOnce({ message: '密码修改成功' } as any)

    await changePassword(data)
    expect(mockPut).toHaveBeenCalledWith('/auth/password', data)
  })
})
