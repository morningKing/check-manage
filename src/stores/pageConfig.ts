/**
 * 页面配置状态管理 Store
 *
 * 管理应用的页面配置数据，包括：
 * - 页面配置列表的获取和更新
 * - 页面字段配置的管理
 * - 动态数据的CRUD操作
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { PageConfig, FieldConfig, DynamicRecord } from '@/types'
import { get, post, put, del } from '@/utils/request'
import { getRecordRelations, updateFieldRelations } from '@/api/relation'
import { v4 as uuidv4 } from 'uuid'

/**
 * 页面配置 Store
 *
 * 使用 Composition API 风格定义
 */
export const usePageConfigStore = defineStore('pageConfig', () => {
  // ==================== State ====================

  /**
   * 页面配置列表
   */
  const pageConfigs = ref<PageConfig[]>([])

  /**
   * 当前编辑的页面配置
   */
  const currentPageConfig = ref<PageConfig | null>(null)

  /**
   * 数据加载状态
   */
  const loading = ref(false)

  /**
   * 页面数据缓存
   * key: pageId, value: 数据列表
   */
  const pageDataCache = ref<Record<string, DynamicRecord[]>>({})

  // ==================== Getters ====================

  /**
   * 根据ID获取页面配置
   *
   * @param id - 页面ID
   * @returns 页面配置或undefined
   */
  const getPageConfigById = computed(() => {
    return (id: string): PageConfig | undefined => {
      return pageConfigs.value.find((config) => config.id === id)
    }
  })

  /**
   * 获取页面配置选项列表
   *
   * 用于下拉选择框
   */
  const pageConfigOptions = computed(() => {
    return pageConfigs.value.map((config) => ({
      label: config.name,
      value: config.id
    }))
  })

  /**
   * 获取指定页面的字段列表（按顺序排列）
   *
   * @param pageId - 页面ID
   * @returns 排序后的字段列表
   */
  const getPageFields = computed(() => {
    return (pageId: string): FieldConfig[] => {
      const config = pageConfigs.value.find((c) => c.id === pageId)
      if (!config) return []
      return [...config.fields].sort((a, b) => a.order - b.order)
    }
  })

  // ==================== Actions ====================

  /**
   * 从API获取页面配置列表
   */
  async function fetchPageConfigs(): Promise<void> {
    loading.value = true
    try {
      const data = await get<PageConfig[]>('/pageConfigs')
      pageConfigs.value = data
    } catch (error) {
      console.error('获取页面配置失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  /**
   * 添加页面配置
   *
   * @param config - 页面配置数据（不含ID和时间戳）
   * @returns 创建的页面配置
   */
  async function addPageConfig(
    config: Omit<PageConfig, 'id' | 'createdAt' | 'updatedAt'>
  ): Promise<PageConfig> {
    const now = new Date().toISOString()
    const newConfig: PageConfig = {
      ...config,
      id: `page-${uuidv4().slice(0, 8)}`,
      createdAt: now,
      updatedAt: now
    }

    try {
      const created = await post<PageConfig>('/pageConfigs', newConfig)
      pageConfigs.value.push(created)
      return created
    } catch (error) {
      console.error('添加页面配置失败:', error)
      throw error
    }
  }

  /**
   * 更新页面配置
   *
   * @param id - 页面ID
   * @param config - 更新的配置数据
   * @returns 更新后的页面配置
   */
  async function updatePageConfig(
    id: string,
    config: Partial<PageConfig>
  ): Promise<PageConfig> {
    const now = new Date().toISOString()
    const updateData = {
      ...config,
      id,
      updatedAt: now
    }

    try {
      const updated = await put<PageConfig>(`/pageConfigs/${id}`, updateData)
      const index = pageConfigs.value.findIndex((c) => c.id === id)
      if (index !== -1) {
        pageConfigs.value[index] = updated
      }
      return updated
    } catch (error) {
      console.error('更新页面配置失败:', error)
      throw error
    }
  }

  /**
   * 删除页面配置
   *
   * @param id - 页面ID
   */
  async function deletePageConfig(id: string): Promise<void> {
    try {
      await del(`/pageConfigs/${id}`)
      pageConfigs.value = pageConfigs.value.filter((c) => c.id !== id)
      // 清除缓存的页面数据
      delete pageDataCache.value[id]
    } catch (error) {
      console.error('删除页面配置失败:', error)
      throw error
    }
  }

  /**
   * 设置当前编辑的页面配置
   *
   * @param config - 页面配置
   */
  function setCurrentPageConfig(config: PageConfig | null): void {
    currentPageConfig.value = config
  }

  /**
   * 更新页面字段配置
   *
   * @param pageId - 页面ID
   * @param fields - 新的字段列表
   */
  async function updatePageFields(pageId: string, fields: FieldConfig[]): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) {
      throw new Error(`页面配置不存在: ${pageId}`)
    }

    await updatePageConfig(pageId, {
      ...config,
      fields
    })
  }

  /**
   * 添加字段到页面
   *
   * @param pageId - 页面ID
   * @param field - 字段配置
   */
  async function addField(pageId: string, field: Omit<FieldConfig, 'id'>): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) {
      throw new Error(`页面配置不存在: ${pageId}`)
    }

    const newField: FieldConfig = {
      ...field,
      id: `field-${uuidv4().slice(0, 8)}`
    }

    const updatedFields = [...config.fields, newField]
    await updatePageFields(pageId, updatedFields)
  }

  /**
   * 更新字段配置
   *
   * @param pageId - 页面ID
   * @param fieldId - 字段ID
   * @param field - 更新的字段数据
   */
  async function updateField(
    pageId: string,
    fieldId: string,
    field: Partial<FieldConfig>
  ): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) {
      throw new Error(`页面配置不存在: ${pageId}`)
    }

    const updatedFields = config.fields.map((f) =>
      f.id === fieldId ? { ...f, ...field } : f
    )
    await updatePageFields(pageId, updatedFields)
  }

  /**
   * 删除字段
   *
   * @param pageId - 页面ID
   * @param fieldId - 字段ID
   */
  async function deleteField(pageId: string, fieldId: string): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) {
      throw new Error(`页面配置不存在: ${pageId}`)
    }

    const updatedFields = config.fields.filter((f) => f.id !== fieldId)
    await updatePageFields(pageId, updatedFields)
  }

  // ==================== 页面数据 CRUD ====================

  /**
   * 获取页面数据列表
   *
   * @param pageId - 页面ID
   * @returns 数据列表
   */
  async function fetchPageData(pageId: string): Promise<DynamicRecord[]> {
    try {
      // 根据页面配置获取对应的数据端点
      const config = getPageConfigById.value(pageId)
      // 使用简化的端点名称（从pageId提取）
      const endpoint = pageId.replace('page-', '')
      const data = await get<DynamicRecord[]>(`/${endpoint}`)

      // 加载关联字段数据
      const relationFields = getRelationFields(pageId)
      if (relationFields.length > 0) {
        for (const record of data) {
          try {
            const relations = await getRecordRelations(endpoint, record.id)
            for (const field of relationFields) {
              record[field.fieldName] = relations[field.fieldName] || []
            }
          } catch {
            for (const field of relationFields) {
              record[field.fieldName] = []
            }
          }
        }
      }

      // 解析关联字段的 ID 为显示名称
      if (relationFields.length > 0) {
        await resolveRelationLabels(data, relationFields)
      }

      // 加载引用字段的继承数据
      const referenceFields = getReferenceFields(pageId)
      if (referenceFields.length > 0) {
        await resolveReferences(data, referenceFields)
      }

      pageDataCache.value[pageId] = data
      return data
    } catch (error) {
      console.error(`获取页面数据失败 [${pageId}]:`, error)
      // 如果API不存在，返回空数组
      pageDataCache.value[pageId] = []
      return []
    }
  }

  /**
   * 添加页面数据记录
   *
   * @param pageId - 页面ID
   * @param record - 数据记录
   * @returns 创建的记录
   */
  async function addPageData(
    pageId: string,
    record: Omit<DynamicRecord, 'id'>
  ): Promise<DynamicRecord> {
    const endpoint = pageId.replace('page-', '')
    const now = new Date().toISOString()
    const newRecord: DynamicRecord = {
      ...record,
      id: `${endpoint}-${uuidv4().slice(0, 8)}`,
      createdAt: now
    }

    // 自动填充 autoTimestamp 字段
    for (const field of getAutoTimestampFields(pageId)) {
      newRecord[field.fieldName] = now
    }

    try {
      const created = await post<DynamicRecord>(`/${endpoint}`, newRecord)
      if (pageDataCache.value[pageId]) {
        pageDataCache.value[pageId].push(created)
      }
      return created
    } catch (error) {
      console.error(`添加数据失败 [${pageId}]:`, error)
      throw error
    }
  }

  /**
   * 更新页面数据记录
   *
   * @param pageId - 页面ID
   * @param recordId - 记录ID
   * @param record - 更新的数据
   * @returns 更新后的记录
   */
  async function updatePageData(
    pageId: string,
    recordId: string,
    record: Partial<DynamicRecord>
  ): Promise<DynamicRecord> {
    const endpoint = pageId.replace('page-', '')

    try {
      const now = new Date().toISOString()
      const updateData: Partial<DynamicRecord> = {
        ...record,
        id: recordId,
        updatedAt: now
      }

      // 自动更新 autoTimestamp 字段
      for (const field of getAutoTimestampFields(pageId)) {
        updateData[field.fieldName] = now
      }

      const updated = await put<DynamicRecord>(`/${endpoint}/${recordId}`, updateData)

      if (pageDataCache.value[pageId]) {
        const index = pageDataCache.value[pageId].findIndex((r) => r.id === recordId)
        if (index !== -1) {
          pageDataCache.value[pageId][index] = updated
        }
      }
      return updated
    } catch (error) {
      console.error(`更新数据失败 [${pageId}]:`, error)
      throw error
    }
  }

  /**
   * 删除页面数据记录
   *
   * @param pageId - 页面ID
   * @param recordId - 记录ID
   */
  async function deletePageData(pageId: string, recordId: string): Promise<void> {
    const endpoint = pageId.replace('page-', '')

    try {
      await del(`/${endpoint}/${recordId}`)
      if (pageDataCache.value[pageId]) {
        pageDataCache.value[pageId] = pageDataCache.value[pageId].filter(
          (r) => r.id !== recordId
        )
      }
    } catch (error) {
      console.error(`删除数据失败 [${pageId}]:`, error)
      throw error
    }
  }

  /**
   * 获取缓存的页面数据
   *
   * @param pageId - 页面ID
   * @returns 缓存的数据列表
   */
  function getCachedPageData(pageId: string): DynamicRecord[] {
    return pageDataCache.value[pageId] || []
  }

  /**
   * 获取页面配置中所有 autoTimestamp 类型的字段
   */
  function getAutoTimestampFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'autoTimestamp')
  }

  /**
   * 获取页面配置中所有 relation 类型的字段
   */
  function getRelationFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'relation')
  }

  /**
   * 从表单数据中移除 relation 类型字段（relation 数据存储在关联表中）
   */
  function stripRelationFields(pageId: string, formData: Record<string, any>): Record<string, any> {
    const relationFieldNames = new Set(getRelationFields(pageId).map((f) => f.fieldName))
    const result: Record<string, any> = {}
    for (const [key, value] of Object.entries(formData)) {
      if (!relationFieldNames.has(key)) {
        result[key] = value
      }
    }
    return result
  }

  /**
   * 保存记录的关联关系数据
   */
  async function saveRelations(
    pageId: string,
    recordId: string,
    formData: Record<string, any>
  ): Promise<void> {
    const collection = pageId.replace('page-', '')
    const relationFields = getRelationFields(pageId)

    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config) continue
      const ids = formData[field.fieldName] || []
      await updateFieldRelations(
        collection,
        recordId,
        field.fieldName,
        config.targetCollection,
        config.targetField,
        ids
      )
    }
  }

  /**
   * 获取页面配置中所有 reference 类型的字段
   */
  function getReferenceFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'reference')
  }

  /**
   * 批量解析引用字段，加载父记录数据并合并到子记录
   */
  async function resolveReferences(
    data: DynamicRecord[],
    referenceFields: FieldConfig[]
  ): Promise<void> {
    // 按目标集合分组，收集所有需要加载的父记录 ID
    const collectionIds = new Map<string, Set<string>>()

    for (const field of referenceFields) {
      const config = field.referenceConfig
      if (!config?.targetCollection) continue
      if (!collectionIds.has(config.targetCollection)) {
        collectionIds.set(config.targetCollection, new Set())
      }
      const idSet = collectionIds.get(config.targetCollection)!
      for (const record of data) {
        const refId = record[field.fieldName]
        if (refId) idSet.add(refId)
      }
    }

    // 批量加载每个目标集合的记录
    const parentRecordMap = new Map<string, Record<string, any>>()
    for (const [collection, ids] of collectionIds) {
      if (ids.size === 0) continue
      try {
        const allRecords = await get<any[]>(`/${collection}`)
        for (const rec of allRecords) {
          parentRecordMap.set(rec.id, rec)
        }
      } catch {
        // 加载失败，跳过
      }
    }

    // 将继承字段值合并到子记录
    for (const field of referenceFields) {
      const config = field.referenceConfig
      if (!config) continue
      for (const record of data) {
        const refId = record[field.fieldName]
        const parent = refId ? parentRecordMap.get(refId) : null
        // 写入 displayField 值
        record[`_ref_${field.fieldName}_display`] = parent
          ? (parent[config.displayField] || parent.id)
          : ''
        // 写入继承字段值
        for (const inheritField of (config.inheritFields || [])) {
          record[`_ref_${field.fieldName}_${inheritField}`] = parent
            ? parent[inheritField]
            : ''
        }
      }
    }
  }

  /**
   * 批量解析关联字段的 ID 为显示名称
   *
   * 按 targetCollection 分组批量请求，构建 id → displayField 映射，
   * 将结果写入 record[`_rel_${fieldName}_labels`] 为 { id, label }[]
   */
  async function resolveRelationLabels(
    data: DynamicRecord[],
    relationFields: FieldConfig[]
  ): Promise<void> {
    // 按 targetCollection 分组，避免重复请求同一集合
    const collectionSet = new Set<string>()
    for (const field of relationFields) {
      const config = field.relationConfig
      if (config?.targetCollection) {
        collectionSet.add(config.targetCollection)
      }
    }

    // 批量加载每个目标集合的全部记录
    const collectionRecords = new Map<string, any[]>()
    for (const collection of collectionSet) {
      try {
        const records = await get<any[]>(`/${collection}`)
        collectionRecords.set(collection, records)
      } catch {
        // 加载失败时跳过
      }
    }

    // 为每个关联字段构建 id → label 映射，并写入 _rel_ 前缀字段
    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config?.targetCollection) continue

      const records = collectionRecords.get(config.targetCollection)
      if (!records) continue

      const idToLabel = new Map<string, string>()
      for (const rec of records) {
        idToLabel.set(rec.id, rec[config.displayField] || rec.id)
      }

      for (const record of data) {
        const ids = record[field.fieldName]
        if (Array.isArray(ids) && ids.length > 0) {
          record[`_rel_${field.fieldName}_labels`] = ids.map((id: string) => ({
            id,
            label: idToLabel.get(id) || id
          }))
        } else {
          record[`_rel_${field.fieldName}_labels`] = []
        }
      }
    }
  }

  /**
   * 获取目标集合的主键字段名
   *
   * 查找目标集合的页面配置，返回 isPrimaryKey 为 true 的字段名。
   * 如果没有主键字段，返回 null。
   */
  function getTargetPrimaryKeyField(targetCollection: string): string | null {
    const targetPageId = `page-${targetCollection}`
    const config = pageConfigs.value.find((c) => c.id === targetPageId)
    if (!config) return null
    const pkField = config.fields.find((f) => f.isPrimaryKey)
    return pkField?.fieldName || null
  }

  /**
   * 获取关联字段的主键映射（用于导出 Excel 时将内部 ID 转为主键值）
   */
  async function fetchRelationDisplayMaps(
    pageId: string
  ): Promise<Record<string, Map<string, string>>> {
    const relationFields = getRelationFields(pageId)
    const result: Record<string, Map<string, string>> = {}

    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config) continue
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      if (!pkField) continue
      try {
        const records = await get<any[]>(`/${config.targetCollection}`)
        const idToPk = new Map<string, string>()
        for (const r of records) {
          const pkVal = r[pkField]
          if (pkVal) idToPk.set(r.id, String(pkVal))
        }
        result[field.fieldName] = idToPk
      } catch {
        // 目标集合加载失败时跳过
      }
    }

    return result
  }

  /**
   * 解析导入数据中的关联字段：将主键值转为内部记录 ID
   *
   * Excel 中关联列填写的是目标记录的主键值（如用例ID "IC-001"），
   * 此方法查找匹配的记录并替换为内部 ID，以便 saveRelations 使用。
   */
  async function resolveRelationImportValues(
    pageId: string,
    records: Record<string, any>[]
  ): Promise<void> {
    const relationFields = getRelationFields(pageId)
    if (relationFields.length === 0) return

    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await get<any[]>(`/${config.targetCollection}`)
      } catch {
        continue
      }

      // 构建查找表：主键值 → 内部 ID
      const pkToId = new Map<string, string>()
      const idSet = new Set<string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) pkToId.set(String(pkVal), r.id)
        }
      }

      // 解析每条导入记录的关联值
      for (const record of records) {
        const vals = record[field.fieldName]
        if (!Array.isArray(vals) || vals.length === 0) continue
        record[field.fieldName] = vals
          .map((v: string) => {
            if (idSet.has(v)) return v            // 已经是内部 ID
            return pkToId.get(v) || null          // 按主键值查找
          })
          .filter((v: string | null): v is string => v !== null)
      }
    }
  }

  // 返回需要暴露的内容
  return {
    // State
    pageConfigs,
    currentPageConfig,
    loading,
    pageDataCache,
    // Getters
    getPageConfigById,
    pageConfigOptions,
    getPageFields,
    // Actions
    fetchPageConfigs,
    addPageConfig,
    updatePageConfig,
    deletePageConfig,
    setCurrentPageConfig,
    updatePageFields,
    addField,
    updateField,
    deleteField,
    // 页面数据 CRUD
    fetchPageData,
    addPageData,
    updatePageData,
    deletePageData,
    getCachedPageData,
    // 关联关系
    stripRelationFields,
    saveRelations,
    fetchRelationDisplayMaps,
    resolveRelationImportValues
  }
})
