/**
 * 系统配置 API 接口
 */
import { get, put, post, del } from '@/utils/request'
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  OrderUpdateItem
} from '@/types'

/** 获取系统配置 */
export function getSystemConfig() {
  return get<SystemConfig>('/system-config')
}

/** 更新系统配置 */
export function updateSystemConfig(data: SystemConfigUpdate) {
  return put<SystemConfig>('/system-config', data)
}

/** 获取首页区块列表 */
export function getHomeWidgets() {
  return get<WidgetConfig[]>('/home-widgets')
}

/** 批量更新区块配置 */
export function batchUpdateHomeWidgets(widgets: Partial<WidgetConfig>[]) {
  return put<WidgetConfig[]>('/home-widgets', { widgets })
}

/** 创建自定义区块 */
export function createHomeWidget(data: {
  widgetType: 'custom-markdown' | 'data-card'
  title?: string
  content: Record<string, any>
  visibleRoles?: string[]
}) {
  return post<WidgetConfig>('/home-widgets', data)
}

/** 删除区块 */
export function deleteHomeWidget(id: string) {
  return del<{ success: boolean }>(`/home-widgets/${id}`)
}

/** 更新区块排序 */
export function updateWidgetsOrder(orders: OrderUpdateItem[]) {
  return put<{ success: boolean }>('/home-widgets/order', { orders })
}