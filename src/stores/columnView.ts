/**
 * 列视图状态管理 Store
 *
 * 管理列视图配置数据，包括：
 * - 视图列表的获取和缓存
 * - 视图的增删改查
 * - 当前选中视图的持久化
 * - 表格列的动态过滤
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FieldConfig } from '@/types'
import type {
  ColumnView,
  ColumnConfigItem,
  CreateViewRequest,
  UpdateViewRequest
} from '@/types'
import {
  getColumnViews,
  createColumnView,
  updateColumnView,
  deleteColumnView,
  setDefaultColumnView,
  copyColumnView
} from '@/api/columnView'

export const useColumnViewStore = defineStore('columnView', () => {
  // ==================== State ====================

  const views = ref<ColumnView[]>([])
  const currentViewId = ref<number | null>(null)
  const defaultViewId = ref<number | null>(null)
  const loading = ref(false)

  // ==================== Getters ====================

  const currentView = computed(() =>
    views.value.find(v => v.id === currentViewId.value) || null
  )

  const publicViews = computed(() =>
    views.value.filter(v => v.isPublic)
  )

  const myViews = computed(() =>
    views.value.filter(v => !v.isPublic)
  )

  // ==================== Actions ====================

  async function loadViews(pageId: string) {
    loading.value = true
    try {
      const res = await getColumnViews(pageId)
      views.value = res.views
      defaultViewId.value = res.defaultViewId
      const lastViewId = localStorage.getItem(`view:${pageId}`)
      if (lastViewId) {
        const id = Number(lastViewId)
        if (views.value.some(v => v.id === id)) {
          currentViewId.value = id
          return
        }
      }
      currentViewId.value = res.defaultViewId
    } catch (error) {
      console.error('加载列视图失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function createView(pageId: string, data: CreateViewRequest) {
    const newView = await createColumnView(pageId, data)
    views.value.push(newView)
    return newView
  }

  async function updateView(pageId: string, viewId: number, data: UpdateViewRequest) {
    const updated = await updateColumnView(pageId, viewId, data)
    const index = views.value.findIndex(v => v.id === viewId)
    if (index !== -1) {
      views.value[index] = updated
    }
    return updated
  }

  async function removeView(pageId: string, viewId: number) {
    await deleteColumnView(pageId, viewId)
    views.value = views.value.filter(v => v.id !== viewId)
    if (currentViewId.value === viewId) {
      currentViewId.value = defaultViewId.value
    }
  }

  async function setDefault(pageId: string, viewId: number) {
    await setDefaultColumnView(pageId, viewId)
    views.value.forEach(v => { v.isDefault = v.id === viewId })
    defaultViewId.value = viewId
  }

  async function copyView(pageId: string, viewId: number) {
    const newView = await copyColumnView(pageId, viewId)
    views.value.push(newView)
    return newView
  }

  function selectView(pageId: string, viewId: number | null) {
    currentViewId.value = viewId
    if (viewId !== null) {
      localStorage.setItem(`view:${pageId}`, String(viewId))
    } else {
      localStorage.removeItem(`view:${pageId}`)
    }
  }

  function clearState() {
    views.value = []
    currentViewId.value = null
    defaultViewId.value = null
  }

  function getTableColumns(allFields: FieldConfig[]): FieldConfig[] {
    if (!currentView.value) {
      return allFields.filter(f => !f.hidden).sort((a, b) => a.order - b.order)
    }

    const configMap = new Map<string, ColumnConfigItem>(
      currentView.value.columns.map(c => [c.fieldId, c])
    )

    return allFields
      .filter(f => {
        const config = configMap.get(f.id)
        return config && config.visible
      })
      .map(f => {
        const config = configMap.get(f.id)!
        return {
          ...f,
          order: config.order,
          width: config.width !== 'auto' ? config.width : f.width
        }
      })
      .sort((a, b) => a.order - b.order)
  }

  return {
    // State
    views, currentViewId, defaultViewId, loading,
    // Getters
    currentView, publicViews, myViews,
    // Actions
    loadViews, createView, updateView, removeView, setDefault, copyView,
    selectView, clearState, getTableColumns,
  }
})
