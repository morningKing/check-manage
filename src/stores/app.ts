/**
 * 应用全局状态管理 Store
 *
 * 管理应用级别的全局状态，包括：
 * - 侧边栏折叠状态
 * - 应用初始化状态
 * - 全局加载状态
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getStorage, setStorage, STORAGE_KEYS } from '@/utils/storage'
import { useMenuStore } from './menu'

/**
 * 应用 Store
 *
 * 使用 Composition API 风格定义
 */
export const useAppStore = defineStore('app', () => {
  // ==================== State ====================

  /**
   * 侧边栏是否折叠
   */
  const sidebarCollapsed = ref<boolean>(
    getStorage(STORAGE_KEYS.SIDEBAR_COLLAPSED, false)
  )

  /**
   * 应用是否已初始化
   */
  const initialized = ref(false)

  /**
   * 全局加载状态
   */
  const globalLoading = ref(false)

  /**
   * 全局加载提示文本
   */
  const loadingText = ref('')

  // ==================== Getters ====================

  /**
   * 侧边栏宽度
   *
   * 根据折叠状态返回不同宽度
   */
  const sidebarWidth = computed(() => {
    return sidebarCollapsed.value ? 64 : 240
  })

  // ==================== Actions ====================

  /**
   * 切换侧边栏折叠状态
   */
  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value
    setStorage(STORAGE_KEYS.SIDEBAR_COLLAPSED, sidebarCollapsed.value)
  }

  /**
   * 设置侧边栏折叠状态
   *
   * @param collapsed - 是否折叠
   */
  function setSidebarCollapsed(collapsed: boolean): void {
    sidebarCollapsed.value = collapsed
    setStorage(STORAGE_KEYS.SIDEBAR_COLLAPSED, collapsed)
  }

  /**
   * 显示全局加载
   *
   * @param text - 加载提示文本
   */
  function showLoading(text: string = '加载中...'): void {
    globalLoading.value = true
    loadingText.value = text
  }

  /**
   * 隐藏全局加载
   */
  function hideLoading(): void {
    globalLoading.value = false
    loadingText.value = ''
  }

  /**
   * 初始化应用
   *
   * 加载菜单配置和页面配置
   */
  async function initializeApp(): Promise<void> {
    if (initialized.value) {
      return
    }

    showLoading('正在初始化应用...')

    try {
      const menuStore = useMenuStore()

      // 只加载菜单（路由依赖）；page_configs 延迟到首次访问动态页时加载
      await menuStore.fetchMenus()

      initialized.value = true
    } catch (error) {
      console.error('应用初始化失败:', error)
      throw error
    } finally {
      hideLoading()
    }
  }

  /**
   * 重新加载应用数据
   *
   * 强制重新加载所有配置
   */
  async function reloadAppData(): Promise<void> {
    initialized.value = false
    await initializeApp()
  }

  // 返回需要暴露的内容
  return {
    // State
    sidebarCollapsed,
    initialized,
    globalLoading,
    loadingText,
    // Getters
    sidebarWidth,
    // Actions
    toggleSidebar,
    setSidebarCollapsed,
    showLoading,
    hideLoading,
    initializeApp,
    reloadAppData
  }
})
