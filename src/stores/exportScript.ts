/**
 * 导出脚本状态管理 Store
 *
 * 管理导出脚本配置数据，包括：
 * - 脚本列表的获取
 * - 脚本选项的构建
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { get } from '@/utils/request'

/**
 * 导出脚本接口
 */
export interface ExportScript {
  id: string
  name: string
  description?: string
  language: string
  script: string
  outputFormat: string
  scope: string
  createdAt?: string
  updatedAt?: string
}

/**
 * 导出脚本 Store
 */
export const useExportScriptStore = defineStore('exportScript', () => {
  // ==================== State ====================

  /**
   * 脚本列表
   */
  const scripts = ref<ExportScript[]>([])

  /**
   * 数据加载状态
   */
  const loading = ref(false)

  // ==================== Getters ====================

  /**
   * 脚本选项（用于下拉选择）
   */
  const scriptOptions = computed(() => {
    return scripts.value.map(script => ({
      value: script.id,
      label: script.name
    }))
  })

  // ==================== Actions ====================

  /**
   * 从API获取脚本列表
   */
  async function fetchScripts(): Promise<void> {
    loading.value = true
    try {
      const data = await get<ExportScript[]>('/exportScripts')
      scripts.value = data
    } catch (error) {
      console.error('获取导出脚本列表失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  /**
   * 根据ID获取脚本
   */
  function getScriptById(id: string): ExportScript | undefined {
    return scripts.value.find(script => script.id === id)
  }

  // 返回需要暴露的内容
  return {
    // State
    scripts,
    loading,
    // Getters
    scriptOptions,
    // Actions
    fetchScripts,
    getScriptById
  }
})