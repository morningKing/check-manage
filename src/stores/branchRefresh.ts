/**
 * 分支刷新状态管理 Store
 *
 * 用于协调跨 Collection 的分支切换刷新。
 * 当用户切换分支涉及多个 Collection 时，通知所有相关的页面刷新数据。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 分支刷新 Store
 *
 * 使用 Composition API 风格定义
 */
export const useBranchRefreshStore = defineStore('branchRefresh', () => {
  // ==================== State ====================

  /**
   * 待刷新的 Collection 列表
   * 当分支切换成功后，设置此列表，各 DynamicPage 组件监听并刷新
   */
  const pendingRefreshCollections = ref<string[]>([])

  /**
   * 刷新时间戳，用于触发 Vue 响应式更新
   */
  const refreshTimestamp = ref<number>(0)

  // ==================== Actions ====================

  /**
   * 请求刷新指定的 Collections
   *
   * @param collections - 需要刷新的 Collection 列表
   */
  function requestRefresh(collections: string[]): void {
    pendingRefreshCollections.value = collections
    refreshTimestamp.value = Date.now()
  }

  /**
   * 清除待刷新列表
   */
  function clearRefresh(): void {
    pendingRefreshCollections.value = []
  }

  /**
   * 检查指定 Collection 是否需要刷新
   *
   * @param collection - Collection 名称
   * @returns 是否需要刷新
   */
  function needsRefresh(collection: string): boolean {
    return pendingRefreshCollections.value.includes(collection)
  }

  // 返回需要暴露的内容
  return {
    // State
    pendingRefreshCollections,
    refreshTimestamp,
    // Actions
    requestRefresh,
    clearRefresh,
    needsRefresh,
  }
})