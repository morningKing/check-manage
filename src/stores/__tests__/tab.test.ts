import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTabStore } from '../tab'
import type { TabItem } from '../tab'

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: vi.fn((key: string) => store[key] || null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value }),
    removeItem: vi.fn((key: string) => { delete store[key] }),
    clear: vi.fn(() => { store = {} }),
  }
})()
Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock })

describe('Tab Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
  })

  it('初始状态包含首页标签', () => {
    const store = useTabStore()
    expect(store.tabs).toHaveLength(1)
    expect(store.tabs[0].path).toBe('/home')
    expect(store.tabs[0].closable).toBe(false)
    expect(store.activeTabPath).toBe('/home')
  })

  describe('addTab', () => {
    it('添加新标签并激活', () => {
      const store = useTabStore()
      const tab: TabItem = { path: '/page/1', name: '页面1', closable: true }

      store.addTab(tab)
      expect(store.tabs).toHaveLength(2)
      expect(store.activeTabPath).toBe('/page/1')
    })

    it('已存在的路径只激活不重复添加', () => {
      const store = useTabStore()
      const tab: TabItem = { path: '/page/1', name: '页面1', closable: true }

      store.addTab(tab)
      store.addTab(tab)
      expect(store.tabs).toHaveLength(2) // home + page/1
      expect(store.activeTabPath).toBe('/page/1')
    })

    it('添加多个不同标签', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })

      expect(store.tabs).toHaveLength(3)
      expect(store.activeTabPath).toBe('/page/2')
    })
  })

  describe('removeTab', () => {
    it('关闭非激活标签返回 null', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })
      // 当前激活 /page/2，关闭 /page/1

      const result = store.removeTab('/page/1')
      expect(result).toBeNull()
      expect(store.tabs).toHaveLength(2)
      expect(store.activeTabPath).toBe('/page/2')
    })

    it('关闭当前激活标签自动切换到相邻标签', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })
      // 当前激活 /page/2

      const result = store.removeTab('/page/2')
      expect(result).toBe('/page/1')
      expect(store.activeTabPath).toBe('/page/1')
    })

    it('不存在的路径返回 null', () => {
      const store = useTabStore()
      expect(store.removeTab('/nonexistent')).toBeNull()
    })

    it('不可关闭的标签返回 null', () => {
      const store = useTabStore()
      expect(store.removeTab('/home')).toBeNull()
      expect(store.tabs).toHaveLength(1)
    })
  })

  describe('setActiveTab', () => {
    it('设置激活标签', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })

      store.setActiveTab('/page/1')
      expect(store.activeTabPath).toBe('/page/1')
    })
  })

  describe('moveTab', () => {
    it('拖拽排序', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })
      store.addTab({ path: '/page/3', name: '页面3', closable: true })

      // 将索引1移到索引3
      store.moveTab(1, 3)
      expect(store.tabs[3].path).toBe('/page/1')
    })

    it('相同索引不移动', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      const before = [...store.tabs]

      store.moveTab(0, 0)
      expect(store.tabs.map((t) => t.path)).toEqual(before.map((t) => t.path))
    })

    it('越界索引不移动', () => {
      const store = useTabStore()
      const before = [...store.tabs]

      store.moveTab(-1, 0)
      expect(store.tabs).toHaveLength(before.length)

      store.moveTab(0, 99)
      expect(store.tabs).toHaveLength(before.length)
    })
  })

  describe('removeOtherTabs', () => {
    it('关闭其他可关闭标签', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })
      store.addTab({ path: '/page/3', name: '页面3', closable: true })

      store.removeOtherTabs('/page/2')
      expect(store.tabs).toHaveLength(2) // home + page/2
      expect(store.tabs.map((t) => t.path)).toContain('/home')
      expect(store.tabs.map((t) => t.path)).toContain('/page/2')
    })

    it('当前激活页被关闭时返回 keepPath', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })
      store.setActiveTab('/page/1')

      const result = store.removeOtherTabs('/page/2')
      expect(result).toBe('/page/2')
      expect(store.activeTabPath).toBe('/page/2')
    })

    it('当前激活页保留时返回 null', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.setActiveTab('/page/1')

      const result = store.removeOtherTabs('/page/1')
      expect(result).toBeNull()
    })
  })

  describe('removeAllTabs', () => {
    it('关闭所有可关闭标签', () => {
      const store = useTabStore()
      store.addTab({ path: '/page/1', name: '页面1', closable: true })
      store.addTab({ path: '/page/2', name: '页面2', closable: true })

      const result = store.removeAllTabs()
      expect(result).toBe('/home')
      expect(store.tabs).toHaveLength(1)
      expect(store.tabs[0].path).toBe('/home')
      expect(store.activeTabPath).toBe('/home')
    })
  })

  describe('recentPages', () => {
    it('初始状态为空数组', () => {
      const store = useTabStore()
      expect(store.recentPages).toEqual([])
    })

    it('addRecentPage 添加最近访问记录', () => {
      const store = useTabStore()
      store.addRecentPage({ path: '/page/1', name: '页面1' })

      expect(store.recentPages).toHaveLength(1)
      expect(store.recentPages[0].path).toBe('/page/1')
      expect(store.recentPages[0].name).toBe('页面1')
      expect(store.recentPages[0].visitedAt).toBeDefined()
    })

    it('重复访问同一页面会更新时间并移到头部', () => {
      const store = useTabStore()
      store.addRecentPage({ path: '/page/1', name: '页面1' })
      store.addRecentPage({ path: '/page/2', name: '页面2' })
      store.addRecentPage({ path: '/page/1', name: '页面1' })

      expect(store.recentPages).toHaveLength(2)
      expect(store.recentPages[0].path).toBe('/page/1')
    })

    it('最多保存 10 条记录', () => {
      const store = useTabStore()
      for (let i = 1; i <= 15; i++) {
        store.addRecentPage({ path: `/page/${i}`, name: `页面${i}` })
      }

      expect(store.recentPages).toHaveLength(10)
      expect(store.recentPages[0].path).toBe('/page/15')
    })

    it('getRecentPages 返回最近访问列表', () => {
      const store = useTabStore()
      store.addRecentPage({ path: '/page/1', name: '页面1' })
      store.addRecentPage({ path: '/page/2', name: '页面2' })

      const pages = store.getRecentPages()
      expect(pages).toHaveLength(2)
      expect(pages[0].path).toBe('/page/2')
    })

    it('clearRecentPages 清空最近访问记录', () => {
      const store = useTabStore()
      store.addRecentPage({ path: '/page/1', name: '页面1' })
      store.addRecentPage({ path: '/page/2', name: '页面2' })

      store.clearRecentPages()
      expect(store.recentPages).toHaveLength(0)
    })
  })
})
