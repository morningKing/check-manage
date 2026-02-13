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
