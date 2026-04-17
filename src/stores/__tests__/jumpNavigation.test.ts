import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

import { useJumpNavigationStore, type SavedFilters, type JumpHistoryEntry } from '../jumpNavigation'
import { useAuthStore } from '../auth'

describe('JumpNavigation Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  describe('initial state', () => {
    it('初始状态为空', () => {
      const store = useJumpNavigationStore()
      expect(store.pendingJump).toBeNull()
      expect(store.jumpStack).toEqual([])
      expect(store.hasJumpHistory).toBe(false)
      expect(store.currentJumpSource).toBeUndefined()
    })
  })

  describe('setJump', () => {
    it('设置跳转意图（无分支时默认main）', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = null  // 无分支

      const store = useJumpNavigationStore()
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      })

      expect(store.pendingJump?.targetCollection).toBe('cases')
      expect(store.pendingJump?.branchId).toBe('main')  // 默认main
      expect(store.pendingJump?.timestamp).toBeDefined()
      expect(store.jumpStack.length).toBe(0)
    })

    it('设置跳转意图并自动填充当前分支', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'branch-v1', branchName: '版本1' }

      const store = useJumpNavigationStore()
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      })

      expect(store.pendingJump?.branchId).toBe('branch-v1')  // 自动填充
      expect(store.jumpStack.length).toBe(0)
    })

    it('设置跳转意图并推入历史栈', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'branch-v1', branchName: '版本1' }

      const store = useJumpNavigationStore()
      const sourceEntry: JumpHistoryEntry = {
        pagePath: '/page/page-clients',
        pageName: '客户管理',
        pageId: 'page-clients',
      }

      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      }, sourceEntry)

      expect(store.pendingJump?.branchId).toBe('branch-v1')
      expect(store.jumpStack).toEqual([sourceEntry])
    })

    it('限制历史栈深度', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'main', branchName: '主分支' }

      const store = useJumpNavigationStore()

      // Push 12 entries (more than MAX_STACK_DEPTH = 10)
      for (let i = 0; i < 12; i++) {
        const sourceEntry: JumpHistoryEntry = {
          pagePath: `/page/page-${i}`,
          pageName: `页面 ${i}`,
          pageId: `page-${i}`,
        }
        store.setJump({
          targetCollection: `collection-${i}`,
          targetRecordId: `record-${i}`,
          jumpType: 'relation',
          sourcePageId: `page-${i}`,
        }, sourceEntry)
      }

      // Stack should have 10 entries (the first 2 should be removed)
      expect(store.jumpStack.length).toBe(10)
      // The oldest entries should be removed
      expect(store.jumpStack[0].pageId).toBe('page-2')
    })

    it('携带筛选状态', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'branch-v1', branchName: '版本1' }

      const store = useJumpNavigationStore()
      const filters: SavedFilters = {
        mongoQuery: { status: 'active' },
        keyword: 'test',
        page: 2,
        pageSize: 20,
      }
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
        sourceFilters: filters,
      })

      expect(store.pendingJump?.sourceFilters).toEqual(filters)
    })
  })

  describe('consumeJump', () => {
    it('消费跳转意图后清除', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = null

      const store = useJumpNavigationStore()
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      })

      const consumed = store.consumeJump()

      expect(consumed?.targetCollection).toBe('cases')
      expect(consumed?.branchId).toBe('main')
      expect(store.pendingJump).toBeNull()
    })

    it('无跳转意图时返回 null', () => {
      const store = useJumpNavigationStore()

      const consumed = store.consumeJump()

      expect(consumed).toBeNull()
    })

    it('branchId 在消费后保留（自动填充值）', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'branch-v1', branchName: '版本1' }

      const store = useJumpNavigationStore()
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      })

      const consumed = store.consumeJump()

      expect(consumed?.branchId).toBe('branch-v1')
    })
  })

  describe('popJump', () => {
    it('弹出历史栈顶条目', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'main', branchName: '主分支' }

      const store = useJumpNavigationStore()
      const sourceEntry1: JumpHistoryEntry = {
        pagePath: '/page/page-1',
        pageName: '页面 1',
        pageId: 'page-1',
      }
      const sourceEntry2: JumpHistoryEntry = {
        pagePath: '/page/page-2',
        pageName: '页面 2',
        pageId: 'page-2',
      }

      store.setJump({ targetCollection: 'a', targetRecordId: '1', jumpType: 'relation', sourcePageId: 'p1' }, sourceEntry1)
      store.setJump({ targetCollection: 'b', targetRecordId: '2', jumpType: 'relation', sourcePageId: 'p2' }, sourceEntry2)

      const popped = store.popJump()

      expect(popped).toEqual(sourceEntry2)
      expect(store.jumpStack).toEqual([sourceEntry1])
    })

    it('栈为空时返回 null', () => {
      const store = useJumpNavigationStore()

      const popped = store.popJump()

      expect(popped).toBeNull()
    })
  })

  describe('clearStack', () => {
    it('清空所有状态', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'main', branchName: '主分支' }

      const store = useJumpNavigationStore()
      const sourceEntry: JumpHistoryEntry = {
        pagePath: '/page/page-clients',
        pageName: '客户管理',
        pageId: 'page-clients',
      }
      store.setJump({
        targetCollection: 'cases',
        targetRecordId: 'case-001',
        jumpType: 'relation',
        sourcePageId: 'page-clients',
      }, sourceEntry)

      store.clearStack()

      expect(store.pendingJump).toBeNull()
      expect(store.jumpStack).toEqual([])
    })
  })

  describe('computed properties', () => {
    it('hasJumpHistory 反映栈状态', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'main', branchName: '主分支' }

      const store = useJumpNavigationStore()
      expect(store.hasJumpHistory).toBe(false)

      store.setJump(
        { targetCollection: 'a', targetRecordId: '1', jumpType: 'relation', sourcePageId: 'p1' },
        { pagePath: '/p1', pageName: 'P1', pageId: 'p1' }
      )
      expect(store.hasJumpHistory).toBe(true)
    })

    it('currentJumpSource 返回栈顶条目', () => {
      const authStore = useAuthStore()
      authStore.currentBranch = { branchId: 'main', branchName: '主分支' }

      const store = useJumpNavigationStore()
      expect(store.currentJumpSource).toBeUndefined()

      const sourceEntry: JumpHistoryEntry = {
        pagePath: '/page/page-clients',
        pageName: '客户管理',
        pageId: 'page-clients',
      }
      store.setJump(
        { targetCollection: 'a', targetRecordId: '1', jumpType: 'relation', sourcePageId: 'p1' },
        sourceEntry
      )

      expect(store.currentJumpSource).toEqual(sourceEntry)
    })
  })
})