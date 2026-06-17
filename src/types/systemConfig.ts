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
  | 'quick-form'
  | 'chart'
  | 'todo'
  | 'activity'
  | 'announcement'

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

/** 快速录入表单内容配置 */
export interface QuickFormContent {
  targetCollection: string
  buttonLabel: string
  description?: string
  icon?: string
  /** 指定要录入的字段（fieldName 列表，按此顺序展示）；留空/未设置 = 全部字段（自动字段除外） */
  fields?: string[]
  /** 区块内「最近 5 条」列表所展示的字段（fieldName）；留空/未设置 = 自动取第一个文本类字段 */
  displayField?: string
}

/** 图表区块内容配置 */
export interface ChartContent {
  /** 数据页集合名 */
  collection: string
  /** 图表类型 */
  chartType: 'bar' | 'pie' | 'line'
  /** 分组字段（按其取值分组计数） */
  groupField: string
  /** 取前 N 个分组，默认 20 */
  limit?: number
}

/** 我的待办区块内容配置 */
export interface TodoContent {
  /** 最多显示条数，默认 5 */
  limit?: number
}

/** 最近动态区块内容配置 */
export interface ActivityContent {
  /** 最多显示条数，默认 8 */
  limit?: number
}

/** 公告区块内容配置 */
export interface AnnouncementContent {
  /** 标题 */
  title?: string
  /** 正文（Markdown） */
  body?: string
  /** 提示级别（决定配色） */
  level?: 'info' | 'success' | 'warning' | 'danger'
  /** 是否允许用户关闭（关闭状态记在本地） */
  closable?: boolean
}

/** Widget 内容类型映射 */
export interface WidgetContentMap {
  welcome: { heading: string; description: string }
  stats: { items: StatsItem[] }
  'quick-links': { links: QuickLinkItem[] }
  'system-info': { markdown: string }
  'custom-markdown': { markdown: string }
  'data-card': DataCardContent
  'quick-form': QuickFormContent
  chart: ChartContent
  todo: TodoContent
  activity: ActivityContent
  announcement: AnnouncementContent
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
  /** 登录页标题（留空则用 systemName） */
  loginTitle?: string | null
  /** 登录页副标题 / 欢迎语 */
  loginSubtitle?: string | null
  /** 登录页页脚文案 */
  loginFooter?: string | null
}

/** 系统配置更新参数 */
export interface SystemConfigUpdate {
  systemName: string
  systemShortName: string
  logoUrl?: string | null
  loginTitle?: string | null
  loginSubtitle?: string | null
  loginFooter?: string | null
}

/** 排序更新参数 */
export interface OrderUpdateItem {
  id: string
  order: number
}