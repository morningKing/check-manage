/**
 * 页面配置相关 API 接口
 *
 * 封装页面配置的 CRUD 操作
 */

import { get, post, put, del } from '@/utils/request'
import type { PageConfig } from '@/types'

/**
 * 获取页面配置列表
 */
export function getPageConfigList() {
  return get<PageConfig[]>('/pageConfigs')
}

/**
 * 获取页面配置详情
 *
 * @param id - 页面ID
 */
export function getPageConfigById(id: string) {
  return get<PageConfig>(`/pageConfigs/${id}`)
}

/**
 * 创建页面配置
 *
 * @param config - 页面配置数据
 */
export function createPageConfig(config: Omit<PageConfig, 'id' | 'createdAt' | 'updatedAt'>) {
  return post<PageConfig>('/pageConfigs', config)
}

/**
 * 更新页面配置
 *
 * @param id - 页面ID
 * @param config - 更新的配置数据
 */
export function updatePageConfig(id: string, config: Partial<PageConfig>) {
  return put<PageConfig>(`/pageConfigs/${id}`, { ...config, id })
}

/**
 * 删除页面配置
 *
 * @param id - 页面ID
 */
export function deletePageConfig(id: string) {
  return del(`/pageConfigs/${id}`)
}

/**
 * 关系图谱节点
 */
export interface RelationNode {
  id: string
  name: string
  fields: number
}

/**
 * 关系图谱边
 */
export interface RelationEdge {
  source: string
  target: string
  type: string
  field: string
  label: string
}

/**
 * 关系图谱数据
 */
export interface RelationGraph {
  nodes: RelationNode[]
  edges: RelationEdge[]
}

/**
 * 获取页面配置的关系图谱
 *
 * @param pageId - 页面ID
 * @param depth - 关系深度（可选）
 */
export function getPageConfigRelations(pageId: string, depth?: number) {
  const params: Record<string, any> = {}
  if (depth) params.depth = depth

  return get<RelationGraph>(`/pageConfigs/${pageId}/relations`, params)
}

/**
 * 字段索引构建状态（一个字段一行）
 */
export interface FieldIndexStatus {
  fieldName: string
  status: 'pending' | 'building' | 'ready' | 'failed' | 'dropping'
  error: string | null
  requestedAt: string
  readyAt: string | null
}

/**
 * 获取页面下所有字段的索引构建状态（供字段配置界面轮询展示）
 *
 * @param pageId - 页面ID
 */
export function getFieldIndexStatuses(pageId: string) {
  return get<{ data: FieldIndexStatus[] }>(`/pageConfigs/${pageId}/field-indexes`)
}
