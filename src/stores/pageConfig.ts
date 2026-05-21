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
import { getRecordRelations, getCollectionRelations, updateFieldRelations } from '@/api/relation'
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

  /**
   * 集合选项全局缓存（跨控件共享，带 TTL）
   * 供 RelationSelect / ReferenceSelect / QuoteSelect 共用
   */
  const collectionOptionCache = new Map<string, { data: any[]; timestamp: number }>()
  const COLLECTION_CACHE_TTL = 5 * 60 * 1000 // 5 分钟
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
   * 复制页面配置（duplicate）
   *
   * 复制字段、视图配置、校验脚本、删除绑定。出于安全考虑，副本 apiPublic / apiWritable
   * 强制重置为 false，避免误开放外部 API。
   */
  async function duplicatePageConfig(sourceId: string): Promise<PageConfig> {
    const source = pageConfigs.value.find((c) => c.id === sourceId)
    if (!source) {
      throw new Error(`源页面配置不存在: ${sourceId}`)
    }

    const newId = `page-${uuidv4().slice(0, 8)}`
    const now = new Date().toISOString()
    // 深拷贝避免共享引用
    const cloned = JSON.parse(JSON.stringify(source)) as PageConfig
    const newConfig: PageConfig = {
      ...cloned,
      id: newId,
      name: `${source.name}（副本）`,
      apiEndpoint: `/api/data/${newId.replace('page-', '')}`,
      apiPublic: false,
      apiWritable: false,
      createdAt: now,
      updatedAt: now,
    }

    try {
      const created = await post<PageConfig>('/pageConfigs', newConfig)
      pageConfigs.value.push(created)
      return created
    } catch (error) {
      console.error('复制页面配置失败:', error)
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
   * 辅助函数：获取目标集合数据，使用共享缓存避免重复请求
   * 注：用于解析关联/引用字段，获取全量数据（不分页）
   */
  async function fetchCollectionData(
    collection: string,
    cache: Map<string, any[]>
  ): Promise<any[]> {
    if (cache.has(collection)) {
      return cache.get(collection)!
    }
    try {
      // 使用大页量获取全量数据（用于解析关联/引用字段）
      const response = await get<{ data: any[]; total: number }>(`/${collection}`, { pageSize: 10000 })
      const records = response.data || []
      cache.set(collection, records)
      return records
    } catch {
      cache.set(collection, [])
      return []
    }
  }

  /**
   * 获取集合选项数据（全局缓存，供表单控件共享）
   *
   * 与 fetchCollectionData 的区别：此函数使用持久缓存（带 TTL），
   * 同一集合在 TTL 内只请求一次，跨对话框/控件复用。
   */
  async function fetchCollectionOptions(collection: string): Promise<any[]> {
    const cached = collectionOptionCache.get(collection)
    if (cached && Date.now() - cached.timestamp < COLLECTION_CACHE_TTL) {
      return cached.data
    }
    try {
      const response = await get<{ data: any[]; total: number }>(`/${collection}`, { pageSize: 10000 })
      const records = response.data || []
      collectionOptionCache.set(collection, { data: records, timestamp: Date.now() })
      return records
    } catch {
      return []
    }
  }

  /**
   * 获取页面数据列表（支持后端分页）
   *
   * @param pageId - 页面ID
   * @param options - 可选参数
   * @param options.query - MongoDB 查询条件
   * @param options.page - 页码（从1开始）
   * @param options.pageSize - 每页数量
   * @returns 包含数据和分页信息的对象
   */
  async function fetchPageData(
    pageId: string,
    options?: {
      query?: Record<string, any>
      page?: number
      pageSize?: number
      keyword?: string
      loadAll?: boolean
      locateId?: string
    }
  ): Promise<{
    data: DynamicRecord[]
    total: number
    locatedPage?: number | null
    locatedIndex?: number | null
    locateFilterMiss?: boolean
  }> {
    const { query, page = 1, pageSize = 50, keyword, loadAll, locateId } = options || {}
    try {
      // 根据页面配置获取对应的数据端点
      const endpoint = pageId.replace('page-', '')

      // 构建查询参数
      const params: Record<string, any> = { page, pageSize }
      if (query) {
        params.q = JSON.stringify(query)
      }
      if (keyword && keyword.trim()) {
        params.keyword = keyword.trim()
      }
      if (loadAll) {
        params.all = 'true'
      }
      if (locateId) {
        params.locateId = locateId
      }

      // 请求后端分页数据
      const response = await get<{
        data: DynamicRecord[]
        total: number
        page: number
        pageSize: number
        locatedPage?: number | null
        locatedIndex?: number | null
        locateFilterMiss?: boolean
      }>(`/${endpoint}`, params)

      const data = response.data || []
      const total = response.total || 0

      // 立即捕获定位信息，防止后续关联解析失败时丢失
      const locateInfo = {
        locatedPage: response.locatedPage,
        locatedIndex: response.locatedIndex,
        locateFilterMiss: response.locateFilterMiss,
      }

      // 共享集合缓存，避免多个 resolve 函数重复请求同一集合
      const collectionCache = new Map<string, any[]>()

      // 批量加载关联字段数据（一次请求替代 N 次）
      const relationFields = getRelationFields(pageId)
      if (relationFields.length > 0) {
        try {
          const batchRelations = await getCollectionRelations(endpoint)
          for (const record of data) {
            const recordRelations = batchRelations[record.id] || {}
            for (const field of relationFields) {
              record[field.fieldName] = recordRelations[field.fieldName] || []
            }
          }
        } catch {
          for (const record of data) {
            for (const field of relationFields) {
              record[field.fieldName] = []
            }
          }
        }
      }

      // 解析关联字段的 ID 为显示名称
      if (relationFields.length > 0) {
        await resolveRelationLabels(data, relationFields, collectionCache)
      }

      // 加载引用字段的继承数据
      const referenceFields = getReferenceFields(pageId)
      if (referenceFields.length > 0) {
        await resolveReferences(data, referenceFields, collectionCache)
      }

      // 解析引用选择字段的 ID 为显示名称
      const quoteFields = getQuoteFields(pageId)
      if (quoteFields.length > 0) {
        await resolveQuoteLabels(data, quoteFields, collectionCache)
      }

      // 更新缓存（仅缓存当前页数据）
      pageDataCache.value[pageId] = data
      return { data, total, ...locateInfo }
    } catch (error) {
      console.error(`获取页面数据失败 [${pageId}]:`, error)
      pageDataCache.value[pageId] = []
      return { data: [], total: 0 }
    }
  }

  /**
   * 添加页面数据记录
   *
   * @param pageId - 页面ID
   * @param record - 数据记录
   * @param importId - 导入时预生成的记录ID（可选，用于自引用集合选项场景）
   * @returns 创建的记录
   */
  async function addPageData(
    pageId: string,
    record: Omit<DynamicRecord, 'id'>,
    importId?: string,
    relationData?: Record<string, any>
  ): Promise<DynamicRecord> {
    const endpoint = pageId.replace('page-', '')
    const now = new Date().toISOString()
    const newRecord: DynamicRecord = {
      ...record,
      id: importId || `${endpoint}-${uuidv4().slice(0, 8)}`,
      createdAt: now
    }

    // 自动填充 autoTimestamp 字段
    for (const field of getAutoTimestampFields(pageId)) {
      newRecord[field.fieldName] = now
    }

    // 自动填充 autoSequence 字段
    for (const field of getAutoSequenceFields(pageId)) {
      newRecord[field.fieldName] = generateNextSequenceValue(pageId, field)
    }

    // 自动计算 compositeText 字段
    for (const field of getCompositeTextFields(pageId)) {
      newRecord[field.fieldName] = computeCompositeValue(newRecord, field)
    }

    // 将关联数据合并到同一请求，实现原子性事务
    if (relationData) {
      const relationFields = getRelationFields(pageId)
      const relations: Array<{
        fieldName: string
        targetCollection: string
        targetField: string
        ids: string[]
      }> = []
      for (const field of relationFields) {
        const config = field.relationConfig
        if (!config) continue
        const ids = relationData[field.fieldName] || []
        relations.push({
          fieldName: field.fieldName,
          targetCollection: config.targetCollection,
          targetField: config.targetField,
          ids,
        })
      }
      if (relations.length > 0) {
        ;(newRecord as any)._relations = relations
      }
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
    record: Partial<DynamicRecord>,
    relationData?: Record<string, any>
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

      // 自动计算 compositeText 字段
      for (const field of getCompositeTextFields(pageId)) {
        updateData[field.fieldName] = computeCompositeValue(updateData, field)
      }

      // 携带版本号用于乐观锁检测
      const cached = pageDataCache.value[pageId]?.find((r) => r.id === recordId)
      if (cached?._version !== undefined) {
        updateData._version = cached._version
      }

      // 将关联数据合并到同一请求，实现原子性事务
      if (relationData) {
        const relationFields = getRelationFields(pageId)
        const relations: Array<{
          fieldName: string
          targetCollection: string
          targetField: string
          ids: string[]
        }> = []
        for (const field of relationFields) {
          const config = field.relationConfig
          if (!config) continue
          const ids = relationData[field.fieldName] || []
          relations.push({
            fieldName: field.fieldName,
            targetCollection: config.targetCollection,
            targetField: config.targetField,
            ids,
          })
        }
        if (relations.length > 0) {
          ;(updateData as any)._relations = relations
        }
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
   * 批量删除页面数据记录
   *
   * 单次请求批量删除，比逐条删除性能好得多。
   * 返回 { deleted, blocked? } 信息。
   */
  async function batchDeletePageData(
    pageId: string,
    recordIds: string[]
  ): Promise<{ deleted: number; blocked?: Record<string, string> }> {
    const endpoint = pageId.replace('page-', '')

    try {
      const result = await post<{ deleted: number; blocked?: Record<string, string> }>(
        `/${endpoint}/batch-delete`,
        { ids: recordIds }
      )
      // 从缓存中移除已删除的记录
      if (pageDataCache.value[pageId]) {
        const blockedIds = new Set(result.blocked ? Object.keys(result.blocked) : [])
        const deletedIds = new Set(recordIds.filter((id) => !blockedIds.has(id)))
        pageDataCache.value[pageId] = pageDataCache.value[pageId].filter(
          (r) => !deletedIds.has(r.id)
        )
      }
      return result
    } catch (error) {
      console.error(`批量删除数据失败 [${pageId}]:`, error)
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
   * 获取页面配置中所有 autoSequence 类型的字段
   */
  function getAutoSequenceFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'autoSequence')
  }

  function getCompositeTextFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'compositeText')
  }

  function computeCompositeValue(record: Record<string, any>, field: FieldConfig): string {
    const config = field.compositeTextConfig
    if (!config || !config.sourceFields?.length) return ''
    const values = config.sourceFields
      .map(fn => record[fn])
      .filter(v => v !== null && v !== undefined && v !== '')
      .map(v => String(v))
    return values.join(config.separator ?? ' - ')
  }

  /**
   * 生成下一个自增序列值
   *
   * 从缓存的页面数据中提取目标字段的已有值，
   * 去掉前缀后解析数字，取最大值 +1，补零后返回。
   */
  function generateNextSequenceValue(pageId: string, field: FieldConfig): string {
    const config = field.sequenceConfig || { prefix: '', max: 999 }
    const prefix = config.prefix
    const maxNum = config.max
    const padLen = String(maxNum).length

    const records = pageDataCache.value[pageId] || []
    let currentMax = 0

    for (const record of records) {
      const val = record[field.fieldName]
      if (typeof val === 'string' && val.startsWith(prefix)) {
        const numStr = val.slice(prefix.length)
        const num = parseInt(numStr, 10)
        if (!isNaN(num) && num > currentMax) {
          currentMax = num
        }
      }
    }

    const next = currentMax + 1
    return `${prefix}${String(next).padStart(padLen, '0')}`
  }

  /**
   * 批量生成自增序列值（性能优化版本）
   *
   * 一次遍历找到最大值，批量生成 N 个序列值。
   * 时间复杂度 O(N) 而非 O(N²)。
   *
   * @param pageId - 页面ID
   * @param count - 需要生成的序列值数量
   * @returns 每个序列字段的批量值映射
   */
  function batchGenerateSequenceValues(
    pageId: string,
    count: number
  ): Record<string, string[]> {
    const results: Record<string, string[]> = {}
    const sequenceFields = getAutoSequenceFields(pageId)

    for (const field of sequenceFields) {
      const config = field.sequenceConfig || { prefix: '', max: 999 }
      const prefix = config.prefix
      const maxNum = config.max
      const padLen = String(maxNum).length

      // 一次遍历找到最大值
      let currentMax = 0
      const records = pageDataCache.value[pageId] || []
      for (const record of records) {
        const val = record[field.fieldName]
        if (typeof val === 'string' && val.startsWith(prefix)) {
          const numStr = val.slice(prefix.length)
          const num = parseInt(numStr, 10)
          if (!isNaN(num) && num > currentMax) {
            currentMax = num
          }
        }
      }

      // 批量生成序列值
      results[field.fieldName] = []
      for (let i = 1; i <= count; i++) {
        results[field.fieldName].push(
          `${prefix}${String(currentMax + i).padStart(padLen, '0')}`
        )
      }
    }

    return results
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
   * 获取页面配置中所有 quoteSelect 类型的字段
   */
  function getQuoteFields(pageId: string): FieldConfig[] {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return []
    return config.fields.filter((f) => f.controlType === 'quoteSelect')
  }

  /**
   * 批量解析引用字段，加载父记录数据并合并到子记录
   */
  async function resolveReferences(
    data: DynamicRecord[],
    referenceFields: FieldConfig[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()

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
      const allRecords = await fetchCollectionData(collection, cache)
      for (const rec of allRecords) {
        parentRecordMap.set(rec.id, rec)
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
    relationFields: FieldConfig[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()

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
      const records = await fetchCollectionData(collection, cache)
      collectionRecords.set(collection, records)
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
   * 批量解析引用选择字段的 ID 为显示名称
   *
   * 按 targetCollection 分组批量请求，构建 id → displayField 映射，
   * 将结果写入 record[`_quote_${fieldName}_labels`] 为 { id, label }[]
   */
  async function resolveQuoteLabels(
    data: DynamicRecord[],
    quoteFields: FieldConfig[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()

    const collectionSet = new Set<string>()
    for (const field of quoteFields) {
      const config = field.quoteConfig
      if (config?.targetCollection) {
        collectionSet.add(config.targetCollection)
      }
    }

    const collectionRecords = new Map<string, any[]>()
    for (const collection of collectionSet) {
      const records = await fetchCollectionData(collection, cache)
      collectionRecords.set(collection, records)
    }

    for (const field of quoteFields) {
      const config = field.quoteConfig
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
          record[`_quote_${field.fieldName}_labels`] = ids.map((id: string) => ({
            id,
            label: idToLabel.get(id) || id
          }))
        } else {
          record[`_quote_${field.fieldName}_labels`] = []
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
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
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
   * 解析导入数据中的关联字段：将主键值 / 显示名称转为内部记录 ID
   *
   * Excel 中关联列填写的是目标记录的主键值（如用例ID "IC-001"）或
   * 显示名称（如 "用例A"），此方法查找匹配的记录并替换为内部 ID。
   *
   * 查找优先级：已经是内部 ID → 按主键值匹配 → 按 displayField 匹配
   */
  async function resolveRelationImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const relationFields = getRelationFields(pageId)
    if (relationFields.length === 0) return

    for (const field of relationFields) {
      const config = field.relationConfig
      if (!config) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      // 构建查找表
      const pkToId = new Map<string, string>()
      const displayToId = new Map<string, string>()
      const idSet = new Set<string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) pkToId.set(String(pkVal), r.id)
        }
        if (config.displayField) {
          const displayVal = r[config.displayField]
          if (displayVal) displayToId.set(String(displayVal), r.id)
        }
      }

      // 解析每条导入记录的关联值
      for (const record of records) {
        const vals = record[field.fieldName]
        if (!Array.isArray(vals) || vals.length === 0) continue
        record[field.fieldName] = vals
          .map((v: string) => {
            if (idSet.has(v)) return v            // 已经是内部 ID
            return pkToId.get(v) || displayToId.get(v) || null
          })
          .filter((v: string | null): v is string => v !== null)
      }
    }
  }

  /**
   * 解析导入数据中的引用字段（reference）：将显示值 / 主键值转为内部记录 ID
   *
   * reference 字段存储的是目标记录的内部 ID（如 "template-abc12345"），
   * 但 Excel 导出时写出的是 displayField 的值（如 "模板A"），导入时需要反向查找。
   *
   * 查找优先级：已经是内部 ID → 按主键值匹配 → 按 displayField 匹配
   */
  async function resolveReferenceImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const referenceFields = getReferenceFields(pageId)
    if (referenceFields.length === 0) return

    for (const field of referenceFields) {
      const config = field.referenceConfig
      if (!config?.targetCollection) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      // 构建查找表
      const idSet = new Set<string>()
      const pkToId = new Map<string, string>()
      const displayToId = new Map<string, string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) pkToId.set(String(pkVal), r.id)
        }
        const displayVal = r[config.displayField]
        if (displayVal) displayToId.set(String(displayVal), r.id)
      }

      // 解析每条导入记录
      for (const record of records) {
        const val = record[field.fieldName]
        if (!val || val === '') continue
        const strVal = String(val)
        if (idSet.has(strVal)) continue                             // 已经是内部 ID
        const resolved = pkToId.get(strVal) || displayToId.get(strVal)
        if (resolved) {
          record[field.fieldName] = resolved
        }
      }
    }
  }

  /**
   * 获取引用选择字段的主键映射（用于导出 Excel 时将内部 ID 转为主键值）
   */
  async function fetchQuoteDisplayMaps(
    pageId: string
  ): Promise<Record<string, Map<string, string>>> {
    const quoteFields = getQuoteFields(pageId)
    const result: Record<string, Map<string, string>> = {}

    for (const field of quoteFields) {
      const config = field.quoteConfig
      if (!config) continue
      const pkField = getTargetPrimaryKeyField(config.targetCollection)
      if (!pkField) continue
      try {
        const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
        const records = response.data || []
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
   * 解析导入数据中的引用选择字段：将主键值 / 显示名称转为内部记录 ID
   *
   * 查找优先级：已经是内部 ID → 按主键值匹配 → 按 displayField 匹配
   */
  async function resolveQuoteImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const cache = collectionCache || new Map<string, any[]>()
    const quoteFields = getQuoteFields(pageId)
    if (quoteFields.length === 0) return

    for (const field of quoteFields) {
      const config = field.quoteConfig
      if (!config) continue

      const pkField = getTargetPrimaryKeyField(config.targetCollection)

      let targetRecords: any[]
      try {
        targetRecords = await fetchCollectionData(config.targetCollection, cache)
      } catch {
        continue
      }

      const pkToIds = new Map<string, string[]>()
      const displayToIds = new Map<string, string[]>()
      const idSet = new Set<string>()
      for (const r of targetRecords) {
        idSet.add(r.id)
        if (pkField) {
          const pkVal = r[pkField]
          if (pkVal) {
            const key = String(pkVal)
            const arr = pkToIds.get(key)
            if (arr) arr.push(r.id)
            else pkToIds.set(key, [r.id])
          }
        }
        if (config.displayField) {
          const displayVal = r[config.displayField]
          if (displayVal) {
            const key = String(displayVal)
            const arr = displayToIds.get(key)
            if (arr) arr.push(r.id)
            else displayToIds.set(key, [r.id])
          }
        }
      }

      for (const record of records) {
        const vals = record[field.fieldName]
        if (!Array.isArray(vals) || vals.length === 0) continue
        const seen = new Set<string>()
        const resolved: string[] = []
        for (const v of vals) {
          if (idSet.has(v)) {
            if (!seen.has(v)) { seen.add(v); resolved.push(v) }
          } else {
            const ids = pkToIds.get(v) || displayToIds.get(v)
            if (ids) {
              for (const id of ids) {
                if (!seen.has(id)) { seen.add(id); resolved.push(id) }
              }
            }
          }
        }
        record[field.fieldName] = resolved
      }
    }
  }

  /**
   * 解析导入数据中 collection 类型选项字段：将显示标签转为实际选项值
   *
   * 当 select/multiSelect/radio/checkbox 字段的 optionsSource.type 为 'collection' 时，
   * Excel 中填写的是 labelField 的值（显示标签），需要转换为 valueField 的值（实际存储值）。
   *
   * 对于自引用情况（source.collection 等于当前页面集合），也会从正在导入的记录
   * 中构建映射，并为这些记录预生成 ID（当 valueField 为 'id' 时），存储在
   * record._importId 中，以确保后续 addPageData 能使用一致的 ID。
   */
  async function resolveCollectionSelectImportValues(
    pageId: string,
    records: Record<string, any>[],
    collectionCache?: Map<string, any[]>
  ): Promise<void> {
    const config = pageConfigs.value.find((c) => c.id === pageId)
    if (!config) return

    const cache = collectionCache || new Map<string, any[]>()
    const collectionSelectFields = config.fields.filter(
      (f) =>
        ['select', 'multiSelect', 'radio', 'checkbox'].includes(f.controlType) &&
        f.optionsSource?.type === 'collection' &&
        f.optionsSource?.collection
    )
    if (collectionSelectFields.length === 0) return

    const endpoint = pageId.replace('page-', '')

    for (const field of collectionSelectFields) {
      const source = field.optionsSource!
      const collection = source.collection!
      const labelField = source.labelField || 'id'
      const valueField = source.valueField || 'id'

      // 获取目标集合的已有记录
      const targetRecords = await fetchCollectionData(collection, cache)

      // 构建映射: labelField 值 → valueField 值
      const labelToVal = new Map<string, any>()
      for (const r of targetRecords) {
        const label = String(r[labelField] ?? '')
        const value = r[valueField] ?? r.id
        if (label) labelToVal.set(label, value)
      }

      // 自引用：将正在导入的记录也加入映射
      const isSelfReferencing = collection === endpoint
      if (isSelfReferencing) {
        for (const record of records) {
          const label = String(record[labelField] ?? '')
          if (!label) continue

          if (valueField === 'id') {
            // valueField 为 id 时需要预生成 ID
            if (!record._importId) {
              record._importId = `${endpoint}-${uuidv4().slice(0, 8)}`
            }
            labelToVal.set(label, record._importId)
          } else {
            const value = record[valueField]
            if (value !== undefined && value !== null && value !== '') {
              labelToVal.set(label, value)
            }
          }
        }
      }

      // 解析每条记录的选项值
      for (const record of records) {
        const val = record[field.fieldName]
        if (val === null || val === undefined || val === '') continue

        if (['select', 'radio'].includes(field.controlType)) {
          // 单选：直接查找映射
          const resolved = labelToVal.get(String(val))
          if (resolved !== undefined) {
            record[field.fieldName] = resolved
          }
        } else if (['multiSelect', 'checkbox'].includes(field.controlType)) {
          // 多选：逐个映射
          if (Array.isArray(val)) {
            record[field.fieldName] = val.map((v: any) => {
              const resolved = labelToVal.get(String(v))
              return resolved !== undefined ? resolved : v
            })
          }
        }
      }
    }
  }

  /**
   * 智能刷新单条记录（编辑/新增后调用，避免全量重新加载）
   *
   * @param pageId - 页面ID
   * @param recordId - 记录ID
   * @returns 更新后的记录，如果找不到则返回 null
   */
  async function refreshSingleRecord(pageId: string, recordId: string): Promise<DynamicRecord | null> {
    const endpoint = pageId.replace('page-', '')
    const collectionCache = new Map<string, any[]>()

    try {
      // 获取单条记录
      const record = await get<DynamicRecord>(`/${endpoint}/${recordId}`)

      // 获取该记录的关联数据
      const relationFields = getRelationFields(pageId)
      if (relationFields.length > 0) {
        try {
          const relations = await getRecordRelations(endpoint, recordId)
          for (const field of relationFields) {
            record[field.fieldName] = relations[field.fieldName] || []
          }
        } catch {
          for (const field of relationFields) {
            record[field.fieldName] = []
          }
        }
      }

      // 解析关联标签
      if (relationFields.length > 0) {
        await resolveRelationLabels([record], relationFields, collectionCache)
      }

      // 解析引用字段
      const referenceFields = getReferenceFields(pageId)
      if (referenceFields.length > 0) {
        await resolveReferences([record], referenceFields, collectionCache)
      }

      // 解析引用选择标签
      const quoteFields = getQuoteFields(pageId)
      if (quoteFields.length > 0) {
        await resolveQuoteLabels([record], quoteFields, collectionCache)
      }

      // 更新缓存中的对应记录
      if (pageDataCache.value[pageId]) {
        const index = pageDataCache.value[pageId].findIndex((r) => r.id === recordId)
        if (index !== -1) {
          pageDataCache.value[pageId][index] = record
        } else {
          // 新增记录，追加到缓存末尾
          pageDataCache.value[pageId].push(record)
        }
      }

      return record
    } catch (error) {
      console.error(`刷新单条记录失败 [${pageId}/${recordId}]:`, error)
      return null
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
    duplicatePageConfig,
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
    batchDeletePageData,
    getCachedPageData,
    refreshSingleRecord,
    // 关联关系
    getRelationFields,
    stripRelationFields,
    saveRelations,
    fetchRelationDisplayMaps,
    resolveRelationImportValues,
    // 数据引用
    resolveReferenceImportValues,
    // 引用选择
    fetchQuoteDisplayMaps,
    resolveQuoteImportValues,
    // collection 类型选项解析
    resolveCollectionSelectImportValues,
    // 自动字段
    generateNextSequenceValue,
    batchGenerateSequenceValues,
    // 集合选项缓存
    fetchCollectionOptions
  }
})
