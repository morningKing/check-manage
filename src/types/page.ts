/**
 * 页面配置相关类型定义
 *
 * 定义页面配置的数据结构，包含页面基本信息和字段配置列表
 */

import type { FieldConfig } from './field'

/**
 * 页面配置接口
 *
 * 定义单个数据页面的完整配置
 *
 * @property id - 页面唯一标识（与菜单绑定）
 * @property name - 页面名称
 * @property description - 页面描述
 * @property apiEndpoint - 后端API端点（用于数据CRUD操作）
 * @property fields - 字段配置列表
 * @property createdAt - 创建时间
 * @property updatedAt - 更新时间
 */
export interface PageConfig {
  id: string
  name: string
  description?: string
  apiEndpoint: string
  fields: FieldConfig[]
  createdAt: string
  updatedAt: string
}

/**
 * 页面表单数据接口
 *
 * 用于页面配置编辑表单的数据结构
 */
export interface PageFormData {
  id?: string
  name: string
  description: string
  apiEndpoint: string
}

/**
 * 创建空页面表单数据
 *
 * @returns 初始化的页面表单数据
 */
export function createEmptyPageFormData(): PageFormData {
  return {
    name: '',
    description: '',
    apiEndpoint: '/api/data/'
  }
}

/**
 * 创建空页面配置
 *
 * @param id - 页面ID
 * @param name - 页面名称
 * @returns 初始化的页面配置
 */
export function createEmptyPageConfig(id: string, name: string): PageConfig {
  const now = new Date().toISOString()
  return {
    id,
    name,
    description: '',
    apiEndpoint: `/api/data/${id}`,
    fields: [],
    createdAt: now,
    updatedAt: now
  }
}
