/**
 * 跳转导航状态管理
 *
 * 管理关联/引用/引用选择字段的跨页跳转:
 * - pendingJump: 跳转意图（router.push 前设置，目标页消费）
 * - jumpStack: 跳转历史栈（支持返回导航）
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/** 跳转意图 — 发起跳转时设置，目标页到达后消费 */
export interface JumpIntent {
  targetCollection: string
  targetRecordId: string
  jumpType: 'relation' | 'reference' | 'quote' | 'graph'
  sourcePageId: string
  sourceFilters?: SavedFilters
  timestamp: number
}

/** 保存的筛选状态 — 用于返回时恢复 */
export interface SavedFilters {
  mongoQuery: Record<string, any> | null
  keyword: string
  page: number
  pageSize: number
}

/** 跳转历史条目 — 记录来源页面信息 */
export interface JumpHistoryEntry {
  pagePath: string
  pageName: string
  pageId: string
  filters?: SavedFilters
}

const MAX_STACK_DEPTH = 10

export const useJumpNavigationStore = defineStore('jumpNavigation', () => {
  // ==================== State ====================

  const pendingJump = ref<JumpIntent | null>(null)
  const jumpStack = ref<JumpHistoryEntry[]>([])

  // ==================== Computed ====================

  const hasJumpHistory = computed(() => jumpStack.value.length > 0)
  const currentJumpSource = computed(() =>
    jumpStack.value.length > 0 ? jumpStack.value[jumpStack.value.length - 1] : undefined
  )

  // ==================== Actions ====================

  /**
   * 设置跳转意图并推入来源页面到历史栈
   */
  function setJump(intent: JumpIntent, sourceEntry?: JumpHistoryEntry): void {
    pendingJump.value = intent
    if (sourceEntry) {
      jumpStack.value.push(sourceEntry)
      // 限制栈深度
      if (jumpStack.value.length > MAX_STACK_DEPTH) {
        jumpStack.value.shift()
      }
    }
  }

  /**
   * 消费跳转意图（一次性，读后清除）
   */
  function consumeJump(): JumpIntent | null {
    const jump = pendingJump.value
    pendingJump.value = null
    return jump
  }

  /**
   * 弹出跳转历史栈顶条目（用于返回导航）
   */
  function popJump(): JumpHistoryEntry | null {
    return jumpStack.value.pop() ?? null
  }

  /**
   * 清空跳转历史栈
   */
  function clearStack(): void {
    jumpStack.value = []
    pendingJump.value = null
  }

  return {
    pendingJump,
    jumpStack,
    hasJumpHistory,
    currentJumpSource,
    setJump,
    consumeJump,
    popJump,
    clearStack,
  }
})
