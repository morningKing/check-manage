/**
 * 菜单 Store 单元测试
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

vi.mock('uuid', () => {
  let counter = 0
  return {
    v4: () => `mock-uuid-${String(++counter).padStart(4, '0')}`,
  }
})

vi.mock('@/router/dynamicRoutes', () => ({
  generateRoutesFromMenus: vi.fn(() => []),
  addDynamicRoutes: vi.fn(),
  resetRouter: vi.fn(),
}))

import { useMenuStore } from '../menu'
import { get, post, put, del } from '@/utils/request'
import { generateRoutesFromMenus, addDynamicRoutes, resetRouter } from '@/router/dynamicRoutes'
import type { MenuItem } from '@/types'

const mockGet = vi.mocked(get)
const mockPost = vi.mocked(post)
const mockPut = vi.mocked(put)
const mockDel = vi.mocked(del)
const mockGenerateRoutes = vi.mocked(generateRoutesFromMenus)
const mockAddRoutes = vi.mocked(addDynamicRoutes)
const mockResetRouter = vi.mocked(resetRouter)

function makeMenu(overrides: Partial<MenuItem>): MenuItem {
  return {
    id: 'menu-1',
    name: '菜单1',
    icon: 'icon-menu',
    order: 0,
    ...overrides,
  } as MenuItem
}

describe('Menu Store', () => {
  let store: ReturnType<typeof useMenuStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useMenuStore()
    vi.clearAllMocks()
  })

  describe('menuTree (computed)', () => {
    it('空数组返回空树', () => {
      store.$patch({ menuList: [] })
      expect(store.menuTree).toEqual([])
    })

    it('单层菜单正确构建', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '菜单1', parentId: null, order: 2 }),
        makeMenu({ id: 'm2', name: '菜单2', parentId: null, order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const tree = store.menuTree
      expect(tree).toHaveLength(2)
      // 按 order 排序
      expect(tree[0].name).toBe('菜单2')
      expect(tree[1].name).toBe('菜单1')
    })

    it('多层嵌套菜单正确构建', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '根菜单', parentId: null, order: 1 }),
        makeMenu({ id: 'm2', name: '子菜单A', parentId: 'm1', order: 2 }),
        makeMenu({ id: 'm3', name: '子菜单B', parentId: 'm1', order: 1 }),
        makeMenu({ id: 'm4', name: '孙菜单', parentId: 'm2', order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const tree = store.menuTree
      expect(tree).toHaveLength(1)
      expect(tree[0].name).toBe('根菜单')
      expect(tree[0].children).toHaveLength(2)
      // 子菜单按 order 排序
      expect(tree[0].children![0].name).toBe('子菜单B')
      expect(tree[0].children![1].name).toBe('子菜单A')
      // 孙菜单
      expect(tree[0].children![1].children).toHaveLength(1)
      expect(tree[0].children![1].children![0].name).toBe('孙菜单')
    })

    it('乱序输入正确构建树', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm3', name: '孙菜单', parentId: 'm2', order: 1 }),
        makeMenu({ id: 'm1', name: '根菜单', parentId: null, order: 1 }),
        makeMenu({ id: 'm2', name: '子菜单', parentId: 'm1', order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const tree = store.menuTree
      expect(tree[0].name).toBe('根菜单')
      expect(tree[0].children![0].name).toBe('子菜单')
      expect(tree[0].children![0].children![0].name).toBe('孙菜单')
    })

    it('孤立节点（parentId 指向不存在的父节点）不会出现在树中', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '根菜单', parentId: null, order: 1 }),
        makeMenu({ id: 'm2', name: '孤立菜单', parentId: 'nonexistent', order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const tree = store.menuTree
      expect(tree).toHaveLength(1)
      expect(tree[0].name).toBe('根菜单')
    })
  })

  describe('topLevelMenus', () => {
    it('只返回顶级菜单', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '顶级A', parentId: undefined, order: 2 }),
        makeMenu({ id: 'm2', name: '顶级B', parentId: null, order: 1 }),
        makeMenu({ id: 'm3', name: '子菜单', parentId: 'm1', order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const top = store.topLevelMenus
      expect(top).toHaveLength(2)
      expect(top[0].name).toBe('顶级B')
      expect(top[1].name).toBe('顶级A')
    })
  })

  describe('getMenuById', () => {
    it('找到菜单返回正确项', () => {
      const menus = [
        makeMenu({ id: 'm1', name: '菜单1' }),
        makeMenu({ id: 'm2', name: '菜单2' }),
      ]
      store.$patch({ menuList: menus })

      expect(store.getMenuById('m1')?.name).toBe('菜单1')
      expect(store.getMenuById('m2')?.name).toBe('菜单2')
    })

    it('找不到返回 undefined', () => {
      store.$patch({ menuList: [makeMenu({ id: 'm1' })] })
      expect(store.getMenuById('nonexistent')).toBeUndefined()
    })
  })

  describe('getChildMenus', () => {
    it('返回指定父节点的所有子菜单', () => {
      const menus: MenuItem[] = [
        makeMenu({ id: 'm1', name: '父菜单' }),
        makeMenu({ id: 'm2', name: '子A', parentId: 'm1', order: 2 }),
        makeMenu({ id: 'm3', name: '子B', parentId: 'm1', order: 1 }),
        makeMenu({ id: 'm4', name: '其他菜单', parentId: 'm0', order: 1 }),
      ]
      store.$patch({ menuList: menus })

      const children = store.getChildMenus('m1')
      expect(children).toHaveLength(2)
      expect(children[0].name).toBe('子B')
      expect(children[1].name).toBe('子A')
    })

    it('无子菜单返回空数组', () => {
      store.$patch({ menuList: [makeMenu({ id: 'm1' })] })
      expect(store.getChildMenus('m1')).toEqual([])
    })
  })

  describe('getMenuByPath', () => {
    it('根据路径找到菜单', () => {
      const menus = [
        makeMenu({ id: 'm1', path: '/dashboard' }),
        makeMenu({ id: 'm2', path: '/settings' }),
      ]
      store.$patch({ menuList: menus })

      expect(store.getMenuByPath('/dashboard')?.id).toBe('m1')
      expect(store.getMenuByPath('/settings')?.id).toBe('m2')
    })

    it('找不到返回 undefined', () => {
      store.$patch({ menuList: [makeMenu({ id: 'm1', path: '/dashboard' })] })
      expect(store.getMenuByPath('/nonexistent')).toBeUndefined()
    })
  })

  describe('fetchMenus', () => {
    it('成功获取菜单列表', async () => {
      const menus = [makeMenu({ id: 'm1' }), makeMenu({ id: 'm2' })]
      mockGet.mockResolvedValueOnce(menus)

      await store.fetchMenus()

      expect(mockGet).toHaveBeenCalledWith('/menus')
      expect(store.menuList).toEqual(menus)
      expect(store.loading).toBe(false)
    })

    it('请求失败抛出错误', async () => {
      const error = new Error('网络错误')
      mockGet.mockRejectedValueOnce(error)

      await expect(store.fetchMenus()).rejects.toThrow('网络错误')
      expect(store.loading).toBe(false)
    })

    it('loading 状态正确更新', async () => {
      mockGet.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve([]), 10)))

      const promise = store.fetchMenus()
      expect(store.loading).toBe(true)

      await promise
      expect(store.loading).toBe(false)
    })
  })

  describe('addMenu', () => {
    it('成功添加菜单并同步路由', async () => {
      const newMenuData = { name: '新菜单', icon: 'icon-new', order: 1 }
      const createdMenu = makeMenu({ id: 'menu-new', ...newMenuData })
      mockPost.mockResolvedValueOnce(createdMenu)

      const result = await store.addMenu(newMenuData as any)

      expect(mockPost).toHaveBeenCalledWith('/menus', expect.objectContaining(newMenuData))
      expect(result).toEqual(createdMenu)
      expect(store.menuList).toContainEqual(createdMenu)
      expect(mockResetRouter).toHaveBeenCalled()
      expect(mockGenerateRoutes).toHaveBeenCalled()
      expect(mockAddRoutes).toHaveBeenCalled()
    })

    it('添加失败抛出错误', async () => {
      mockPost.mockRejectedValueOnce(new Error('添加失败'))

      await expect(store.addMenu({ name: '新菜单' } as any)).rejects.toThrow('添加失败')
    })
  })

  describe('updateMenu', () => {
    it('成功更新菜单并同步路由', async () => {
      store.$patch({
        menuList: [makeMenu({ id: 'm1', name: '旧名称' })],
      })

      const updatedMenu = makeMenu({ id: 'm1', name: '新名称' })
      mockPut.mockResolvedValueOnce(updatedMenu)

      const result = await store.updateMenu('m1', { name: '新名称' })

      expect(mockPut).toHaveBeenCalledWith('/menus/m1', expect.objectContaining({ id: 'm1', name: '新名称' }))
      expect(result).toEqual(updatedMenu)
      expect(store.menuList[0].name).toBe('新名称')
      expect(mockResetRouter).toHaveBeenCalled()
    })

    it('更新不存在的菜单仍调用 API', async () => {
      const updatedMenu = makeMenu({ id: 'm1', name: '新名称' })
      mockPut.mockResolvedValueOnce(updatedMenu)

      await store.updateMenu('m1', { name: '新名称' })

      expect(mockPut).toHaveBeenCalled()
    })
  })

  describe('deleteMenu', () => {
    it('删除叶子节点菜单', async () => {
      store.$patch({
        menuList: [
          makeMenu({ id: 'm1', name: '父菜单' }),
          makeMenu({ id: 'm2', name: '子菜单', parentId: 'm1' }),
        ],
      })
      mockDel.mockResolvedValueOnce({})

      await store.deleteMenu('m2')

      expect(mockDel).toHaveBeenCalledWith('/menus/m2')
      expect(store.menuList).toHaveLength(1)
      expect(store.menuList[0].id).toBe('m1')
    })

    it('删除有子节点的菜单同时删除所有子菜单', async () => {
      store.$patch({
        menuList: [
          makeMenu({ id: 'm1', name: '根菜单' }),
          makeMenu({ id: 'm2', name: '子菜单A', parentId: 'm1' }),
          makeMenu({ id: 'm3', name: '子菜单B', parentId: 'm1' }),
          makeMenu({ id: 'm4', name: '孙菜单', parentId: 'm2' }),
          makeMenu({ id: 'm5', name: '其他菜单' }),
        ],
      })
      mockDel.mockResolvedValue({})

      await store.deleteMenu('m1')

      // 应该删除 m1, m2, m3, m4
      expect(mockDel).toHaveBeenCalledTimes(4)
      expect(store.menuList).toHaveLength(1)
      expect(store.menuList[0].id).toBe('m5')
    })

    it('删除后同步路由', async () => {
      store.$patch({
        menuList: [makeMenu({ id: 'm1' })],
      })
      mockDel.mockResolvedValueOnce({})

      await store.deleteMenu('m1')

      expect(mockResetRouter).toHaveBeenCalled()
      expect(mockGenerateRoutes).toHaveBeenCalled()
      expect(mockAddRoutes).toHaveBeenCalled()
    })

    it('删除失败抛出错误', async () => {
      store.$patch({
        menuList: [makeMenu({ id: 'm1' })],
      })
      mockDel.mockRejectedValueOnce(new Error('删除失败'))

      await expect(store.deleteMenu('m1')).rejects.toThrow('删除失败')
    })
  })

  describe('setActiveMenu', () => {
    it('设置当前激活菜单', () => {
      store.setActiveMenu('m1')
      expect(store.activeMenuId).toBe('m1')
    })
  })

  describe('updateMenuOrder', () => {
    it('批量更新菜单顺序', async () => {
      store.$patch({
        menuList: [
          makeMenu({ id: 'm1', name: '菜单1', order: 1 }),
          makeMenu({ id: 'm2', name: '菜单2', order: 2 }),
        ],
      })
      mockPut.mockResolvedValue({})

      await store.updateMenuOrder([
        { id: 'm1', order: 2 },
        { id: 'm2', order: 1 },
      ])

      expect(mockPut).toHaveBeenCalledTimes(2)
      expect(store.menuList[0].order).toBe(2)
      expect(store.menuList[1].order).toBe(1)
    })

    it('更新失败抛出错误', async () => {
      store.$patch({
        menuList: [makeMenu({ id: 'm1', order: 1 })],
      })
      mockPut.mockRejectedValueOnce(new Error('更新失败'))

      await expect(store.updateMenuOrder([{ id: 'm1', order: 2 }])).rejects.toThrow('更新失败')
    })
  })
})