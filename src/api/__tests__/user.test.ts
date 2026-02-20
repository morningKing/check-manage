import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import { getUserList, createUser, updateUser, deleteUser } from '../user'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('User API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getUserList 调用 GET /users', async () => {
    const users = [{ id: 'u1', username: 'admin' }]
    mockGet.mockResolvedValueOnce(users as any)

    const res = await getUserList()
    expect(mockGet).toHaveBeenCalledWith('/users')
    expect(res).toEqual(users)
  })

  it('createUser 调用 POST /users', async () => {
    const data = { username: 'newuser', password: '123456', displayName: '新用户', role: 'guest' }
    mockPost.mockResolvedValueOnce({ id: 'u2', ...data } as any)

    const res = await createUser(data)
    expect(mockPost).toHaveBeenCalledWith('/users', data)
    expect(res).toEqual({ id: 'u2', ...data })
  })

  it('updateUser 调用 PUT /users/{id}', async () => {
    const data = { displayName: '更新名称', role: 'developer' }
    mockPut.mockResolvedValueOnce({ id: 'u1', ...data } as any)

    await updateUser('u1', data)
    expect(mockPut).toHaveBeenCalledWith('/users/u1', data)
  })

  it('deleteUser 调用 DELETE /users/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteUser('u1')
    expect(mockDel).toHaveBeenCalledWith('/users/u1')
  })
})
