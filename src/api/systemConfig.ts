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

/** 获取首页区块列表。all=true 时返回全部区块（含未启用，需 admin.home_widgets 权限），供配置页使用 */
export function getHomeWidgets(all = false) {
  return get<WidgetConfig[]>('/home-widgets', all ? { all: true } : undefined)
}

/** 批量更新区块配置 */
export function batchUpdateHomeWidgets(widgets: Partial<WidgetConfig>[]) {
  return put<WidgetConfig[]>('/home-widgets', { widgets })
}

/** 创建自定义区块 */
export function createHomeWidget(data: {
  widgetType: 'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'
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