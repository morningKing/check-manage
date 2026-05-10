/**
 * 系统配置相关类型定义
 */

/** Widget 类型枚举 */
export type WidgetType =
  | 'welcome'
  | 'stats'
  | 'quick-links'
  | 'system-info'
  | 'custom-markdown'
  | 'data-card'

/** 统计项类型 */
export interface StatsItem {
  type: 'menuCount' | 'pageCount' | 'fieldCount' | 'recordCount'
  label: string
  icon: string
  collection?: string
  filter?: Record<string, any>
}

/** 快捷链接项 */
export interface QuickLinkItem {
  name: string
  path: string
  icon: string
  action?: string
}

/** 数据卡片数据源配置 */
export interface DataSourceConfig {
  collection: string
  branchId?: string
  filter?: Record<string, any>
  limit?: number
}

/** 数据卡片内容配置 */
export interface DataCardContent {
  dataSource: DataSourceConfig
  displayType: 'count' | 'list' | 'table'
  columns?: string[]
  titleField?: string
  linkToDetail?: boolean
}

/** Widget 内容类型映射 */
export interface WidgetContentMap {
  welcome: { heading: string; description: string }
  stats: { items: StatsItem[] }
  'quick-links': { links: QuickLinkItem[] }
  'system-info': { markdown: string }
  'custom-markdown': { markdown: string }
  'data-card': DataCardContent
}

/** Widget 配置 */
export interface WidgetConfig {
  id: string
  widgetType: WidgetType
  title?: string
  content: WidgetContentMap[WidgetType]
  enabled: boolean
  order: number
  visibleRoles: string[]
  createdAt?: string
  updatedAt?: string
}

/** 系统配置 */
export interface SystemConfig {
  systemName: string
  systemShortName: string
  logoUrl?: string | null
}

/** 系统配置更新参数 */
export interface SystemConfigUpdate {
  systemName: string
  systemShortName: string
  logoUrl?: string | null
}

/** 排序更新参数 */
export interface OrderUpdateItem {
  id: string
  order: number
}