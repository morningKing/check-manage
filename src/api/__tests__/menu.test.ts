import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { get, post, put, del } from '@/utils/request'
import { setStorage, STORAGE_KEYS } from '@/utils/storage'
import { getMenuList, getMenuById, createMenu, updateMenu, deleteMenu, executeMenuExport } from '../menu'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)

describe('Menu API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getMenuList 调用 GET /menus', async () => {
    const menus = [{ id: 'menu-1', name: '首页' }]
    mockGet.mockResolvedValueOnce(menus as any)

    const res = await getMenuList()
    expect(mockGet).toHaveBeenCalledWith('/menus')
    expect(res).toEqual(menus)
  })

  it('getMenuById 调用 GET /menus/{id}', async () => {
    const menu = { id: 'menu-1', name: '首页' }
    mockGet.mockResolvedValueOnce(menu as any)

    const res = await getMenuById('menu-1')
    expect(mockGet).toHaveBeenCalledWith('/menus/menu-1')
    expect(res).toEqual(menu)
  })

  it('createMenu 调用 POST /menus', async () => {
    const menu = { id: 'menu-new', name: '新菜单', icon: 'Star', path: '/new' }
    mockPost.mockResolvedValueOnce(menu as any)

    const res = await createMenu(menu as any)
    expect(mockPost).toHaveBeenCalledWith('/menus', menu)
    expect(res).toEqual(menu)
  })

  it('updateMenu 调用 PUT /menus/{id}', async () => {
    const updates = { name: '更新菜单' }
    mockPut.mockResolvedValueOnce({ id: 'menu-1', ...updates } as any)

    await updateMenu('menu-1', updates)
    expect(mockPut).toHaveBeenCalledWith('/menus/menu-1', { ...updates, id: 'menu-1' })
  })

  it('deleteMenu 调用 DELETE /menus/{id}', async () => {
    mockDel.mockResolvedValueOnce({} as any)

    await deleteMenu('menu-1')
    expect(mockDel).toHaveBeenCalledWith('/menus/menu-1')
  })
})

describe('executeMenuExport 鉴权头', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // 回归：导出曾从 localStorage.getItem('token') 读取（错误的键），
  // 真正的 JWT 存于 check-manage:token（STORAGE_KEYS.AUTH_TOKEN），
  // 导致请求带空 Bearer 头被后端判为「未登录」(401)。
  it('使用 check-manage:token 下的 JWT 作为 Authorization Bearer 头', async () => {
    setStorage(STORAGE_KEYS.AUTH_TOKEN, 'jwt-abc-123')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['zip-bytes']),
    })
    vi.stubGlobal('fetch', fetchMock)

    await executeMenuExport(['menu-1'], 'script-1', 'main')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer jwt-abc-123')
  })
})
