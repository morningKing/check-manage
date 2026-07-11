/**
 * 系统配置 API 接口
 */
import { get, put, post, del } from '@/utils/request'
import type {
  SystemConfig,
  SystemConfigUpdate,
  WidgetConfig,
  WidgetLayout,
  WidgetLayoutUpdateItem,
  CreatableWidgetType
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

/** 创建自定义区块。传 layout 时按指定网格坐标创建（区块面板拖拽新增用），不传则追加到底部 */
export function createHomeWidget(data: {
  widgetType: CreatableWidgetType
  title?: string
  content: Record<string, any>
  visibleRoles?: string[]
  layout?: WidgetLayout
}) {
  return post<WidgetConfig>('/home-widgets', data)
}

/** 删除区块 */
export function deleteHomeWidget(id: string) {
  return del<{ success: boolean }>(`/home-widgets/${id}`)
}

/** 批量保存区块网格布局（x/y/w/h），后端会按新阅读顺序重算 order */
export function updateWidgetsLayout(layout: WidgetLayoutUpdateItem[]) {
  return put<WidgetConfig[]>('/home-widgets/layout', { layout })
}