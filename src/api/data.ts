/**
 * 动态数据 API 接口
 *
 * 封装动态页面数据的 CRUD 操作
 * 数据端点由页面配置的 apiEndpoint 决定
 */

import { get, post, put, del } from '@/utils/request'
import type { DynamicRecord } from '@/types'

/**
 * 获取页面数据列表
 *
 * @param endpoint - API 端点（不含 /api 前缀）
 * @param params - 查询参数
 */
export function getDataList(endpoint: string, params?: Record<string, any>) {
  return get<DynamicRecord[]>(endpoint, params)
}

/**
 * 获取数据详情
 *
 * @param endpoint - API 端点
 * @param id - 记录ID
 */
export function getDataById(endpoint: string, id: string) {
  return get<DynamicRecord>(`${endpoint}/${id}`)
}

/**
 * 创建数据记录
 *
 * @param endpoint - API 端点
 * @param data - 数据记录
 */
export function createData(endpoint: string, data: Omit<DynamicRecord, 'id'>) {
  return post<DynamicRecord>(endpoint, data)
}

/**
 * 更新数据记录
 *
 * @param endpoint - API 端点
 * @param id - 记录ID
 * @param data - 更新的数据
 */
export function updateData(endpoint: string, id: string, data: Partial<DynamicRecord>) {
  return put<DynamicRecord>(`${endpoint}/${id}`, { ...data, id })
}

/**
 * 删除数据记录
 *
 * @param endpoint - API 端点
 * @param id - 记录ID
 */
export function deleteData(endpoint: string, id: string) {
  return del(`${endpoint}/${id}`)
}

/**
 * 批量删除数据记录
 *
 * @param endpoint - API 端点
 * @param ids - 记录ID列表
 */
export async function batchDeleteData(endpoint: string, ids: string[]) {
  const promises = ids.map((id) => del(`${endpoint}/${id}`))
  return Promise.all(promises)
}
