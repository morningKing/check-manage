/**
 * 标签页状态管理 Store
 *
 * 管理工作区标签页的状态，包括：
 * - 已打开的标签页列表
 * - 当前激活的标签页
 * - 标签页的增删改和排序
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 标签页项
 */
export interface TabItem {
  /** 路由路径（唯一标识） */
  path: string
  /** 显示名称 */
  name: string
  /** 图标名称 */
  icon?: string
  /** 是否可关闭（首页不可关闭） */
  closable: boolean
}

/**
 * 标签页 Store
 */
export const useTabStore = defineStore('tab', () => {
  // ==================== State ====================

  /**
   * 已打开的标签页列表
   *
   * 初始包含首页标签（不可关闭）
   */
  const tabs = ref<TabItem[]>([
    { path: '/home', name: '首页', closable: false }
  ])

  /**
   * 当前激活的标签页路径
   */
  const activeTabPath = ref('/home')

  // ==================== Actions ====================

  /**
   * 添加标签页
   *
   * 如果路径已存在则仅激活，否则添加并激活
   */
  function addTab(tab: TabItem): void {
    const exists = tabs.value.find(t => t.path === tab.path)
    if (!exists) {
      tabs.value.push(tab)
    }
    activeTabPath.value = tab.path
  }

  /**
   * 关闭标签页
   *
   * 如果关闭的是当前激活页，自动切换到相邻标签
   * 返回需要导航到的路径（如果需要切换），否则返回 null
   */
  function removeTab(path: string): string | null {
    const index = tabs.value.findIndex(t => t.path === path)
    if (index === -1) return null

    const tab = tabs.value[index]
    if (!tab.closable) return null

    tabs.value.splice(index, 1)

    // 如果关闭的是当前激活标签，切换到相邻标签
    if (activeTabPath.value === path) {
      const newIndex = Math.min(index, tabs.value.length - 1)
      const newTab = tabs.value[newIndex]
      activeTabPath.value = newTab.path
      return newTab.path
    }

    return null
  }

  /**
   * 设置激活标签页
   */
  function setActiveTab(path: string): void {
    activeTabPath.value = path
  }

  /**
   * 拖拽排序：移动标签页位置
   */
  function moveTab(fromIndex: number, toIndex: number): void {
    if (fromIndex === toIndex) return
    if (fromIndex < 0 || fromIndex >= tabs.value.length) return
    if (toIndex < 0 || toIndex >= tabs.value.length) return

    const [moved] = tabs.value.splice(fromIndex, 1)
    tabs.value.splice(toIndex, 0, moved)
  }

  /**
   * 关闭其他标签页（保留首页和指定标签）
   *
   * 返回需要导航到的路径（如果当前激活页被关闭）
   */
  function removeOtherTabs(keepPath: string): string | null {
    tabs.value = tabs.value.filter(t => !t.closable || t.path === keepPath)
    if (!tabs.value.find(t => t.path === activeTabPath.value)) {
      activeTabPath.value = keepPath
      return keepPath
    }
    return null
  }

  /**
   * 关闭所有可关闭的标签页
   *
   * 返回需要导航到的路径
   */
  function removeAllTabs(): string {
    tabs.value = tabs.value.filter(t => !t.closable)
    activeTabPath.value = '/home'
    return '/home'
  }

  return {
    tabs,
    activeTabPath,
    addTab,
    removeTab,
    setActiveTab,
    moveTab,
    removeOtherTabs,
    removeAllTabs
  }
})
