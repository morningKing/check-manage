/**
 * 列视图 API 调用模块
 */

import { get, post, put, del } from '@/utils/request'
import type {
  GetViewsResponse,
  ColumnView,
  CreateViewRequest,
  UpdateViewRequest,
  CopyViewRequest
} from '@/types'

export function getColumnViews(pageId: string) {
  return get<GetViewsResponse>(`/column-views/${pageId}/views`)
}

export function createColumnView(pageId: string, data: CreateViewRequest) {
  return post<ColumnView>(`/column-views/${pageId}/views`, data)
}

export function updateColumnView(pageId: string, viewId: number, data: UpdateViewRequest) {
  return put<ColumnView>(`/column-views/${pageId}/views/${viewId}`, data)
}

export function deleteColumnView(pageId: string, viewId: number) {
  return del(`/column-views/${pageId}/views/${viewId}`)
}

export function setDefaultColumnView(pageId: string, viewId: number) {
  return put(`/column-views/${pageId}/views/${viewId}/default`)
}

export function copyColumnView(pageId: string, viewId: number, data?: CopyViewRequest) {
  return post<ColumnView>(`/column-views/${pageId}/views/${viewId}/copy`, data || {})
}
