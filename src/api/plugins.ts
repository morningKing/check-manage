/**
 * 第一方插件 API（plugin-architecture spec §5）
 *
 * 数据源唯一 GET /plugins（Task 2 后端）；PUT /plugins/:id 切换启用状态。
 */

import { get, put } from '@/utils/request'

export interface PluginInfo {
  id: string
  name: string
  description: string
  version: string
  enabled: boolean
}

export function getPlugins() {
  return get<PluginInfo[]>('/plugins')
}

export function setPluginEnabled(id: string, enabled: boolean) {
  return put<{ id: string; enabled: boolean }>(`/plugins/${encodeURIComponent(id)}`, { enabled })
}
