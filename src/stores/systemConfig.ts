/**
 * 系统配置状态管理 Store
 *
 * 管理系统配置和首页区块状态
 */
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useAuthStore } from './auth'
import {
  getSystemConfig as fetchConfigApi,
  updateSystemConfig as updateConfigApi,
  getHomeWidgets as fetchWidgetsApi,
  batchUpdateHomeWidgets,
  createHomeWidget,
  deleteHomeWidget,
  updateWidgetsLayout
} from '@/api/systemConfig'
import type { SystemConfig, SystemConfigUpdate, WidgetConfig, WidgetLayout, WidgetLayoutUpdateItem, CreatableWidgetType } from '@/types'

/** 默认系统配置 */
const DEFAULT_SYSTEM_CONFIG: SystemConfig = {
  systemName: 'Check Manage',
  systemShortName: 'CM'
}

export const useSystemConfigStore = defineStore('systemConfig', () => {
  // ==================== State ====================

  /** 系统配置 */
  const systemConfig = ref<SystemConfig>({ ...DEFAULT_SYSTEM_CONFIG })

  /** 首页区块列表 */
  const widgets = ref<WidgetConfig[]>([])

  /** 加载状态 */
  const loading = ref(false)

  /** 是否已初始化 */
  const initialized = ref(false)

  // ==================== Getters ====================

  /**
   * 可见区块列表
   * 根据当前用户角色过滤 enabled 的区块，按 order 排序
   */
  const visibleWidgets = computed(() => {
    const authStore = useAuthStore()
    const userRole = authStore.userRole

    return widgets.value
      .filter(widget => {
        // 只显示启用的区块
        if (!widget.enabled) return false

        // 如果未配置可见角色，则所有人可见
        if (!widget.visibleRoles || widget.visibleRoles.length === 0) return true

        // 检查用户角色是否在可见角色列表中
        return widget.visibleRoles.includes(userRole ?? '')
      })
      .sort((a, b) => a.order - b.order)
  })

  /** 系统名称 */
  const systemName = computed(() => systemConfig.value.systemName)

  /** 系统简称 */
  const systemShortName = computed(() => systemConfig.value.systemShortName)

  // ==================== Actions ====================

  /**
   * 加载系统配置
   */
  async function fetchSystemConfig(): Promise<void> {
    try {
      const config = await fetchConfigApi()
      systemConfig.value = config
    } catch (error) {
      console.error('Failed to fetch system config:', error)
    }
  }

  /**
   * 加载首页区块
   * @param all true 时拉取全部区块（含未启用，供配置页使用）；默认仅启用且当前角色可见
   */
  async function fetchWidgets(all = false): Promise<void> {
    try {
      const result = await fetchWidgetsApi(all)
      widgets.value = result
    } catch (error) {
      console.error('Failed to fetch widgets:', error)
    }
  }

  /**
   * 初始化（并行加载两个配置）
   */
  async function initialize(): Promise<void> {
    if (initialized.value) return

    loading.value = true
    try {
      await Promise.all([fetchSystemConfig(), fetchWidgets()])
      initialized.value = true
    } finally {
      loading.value = false
    }
  }

  /**
   * 更新系统配置
   */
  async function updateConfig(data: SystemConfigUpdate): Promise<void> {
    try {
      const config = await updateConfigApi(data)
      systemConfig.value = config
    } catch (error) {
      console.error('Failed to update system config:', error)
      throw error
    }
  }

  /**
   * 批量更新区块
   */
  async function updateWidgets(changes: Partial<WidgetConfig>[]): Promise<void> {
    try {
      const result = await batchUpdateHomeWidgets(changes)
      widgets.value = result
    } catch (error) {
      console.error('Failed to update widgets:', error)
      throw error
    }
  }

  /**
   * 创建自定义区块。传 layout 时按指定网格坐标创建（区块面板拖拽新增用），不传则追加到底部
   */
  async function createWidget(data: {
    widgetType: CreatableWidgetType
    title?: string
    content: Record<string, any>
    visibleRoles?: string[]
    layout?: WidgetLayout
  }): Promise<void> {
    try {
      const widget = await createHomeWidget(data)
      widgets.value.push(widget)
    } catch (error) {
      console.error('Failed to create widget:', error)
      throw error
    }
  }

  /**
   * 删除区块
   */
  async function removeWidget(id: string): Promise<void> {
    try {
      await deleteHomeWidget(id)
      widgets.value = widgets.value.filter(w => w.id !== id)
    } catch (error) {
      console.error('Failed to remove widget:', error)
      throw error
    }
  }

  /**
   * 批量保存网格布局（管理端拖拽/缩放后调用）
   */
  async function updateLayout(layout: WidgetLayoutUpdateItem[]): Promise<void> {
    try {
      const result = await updateWidgetsLayout(layout)
      widgets.value = result
    } catch (error) {
      console.error('Failed to update layout:', error)
      throw error
    }
  }

  // ==================== Watchers ====================

  // 监听系统名称变化，自动更新文档标题
  watch(
    systemName,
    (name) => {
      if (name) {
        document.title = name
      }
    },
    { immediate: true }
  )

  return {
    // State
    systemConfig,
    widgets,
    loading,
    initialized,
    // Getters
    visibleWidgets,
    systemName,
    systemShortName,
    // Actions
    fetchSystemConfig,
    fetchWidgets,
    initialize,
    updateConfig,
    updateWidgets,
    createWidget,
    removeWidget,
    updateLayout
  }
})