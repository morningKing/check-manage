import { get, post, put, del } from '@/utils/request'

export type WidgetType = 'metric' | 'bar' | 'line' | 'pie' | 'area' | 'dataTable' | 'ring' | 'gauge' | 'radar' | 'funnel'
export type MetricType = 'count' | 'sum' | 'avg' | 'min' | 'max' | 'uniqueCount' | 'arrayLengthSum' | 'arrayLengthAvg' | 'arrayLengthMax' | 'arrayLengthMin' | 'relationCountSum' | 'relationCountAvg' | 'relationCountMax' | 'relationCountMin'
export type GroupByType = 'terms' | 'dateHistogram' | 'histogram' | 'range' | 'exists'
export type SortType = 'value_desc' | 'value_asc' | 'key_desc' | 'key_asc'
export type DateHistogramInterval = 'day' | 'week' | 'month' | 'year'

export interface MetricDef {
  type: MetricType
  field?: string
  name?: string
}

export interface RangeDef {
  key?: string
  from?: number
  to?: number
}

export interface GroupByDef {
  field: string
  type: GroupByType
  interval?: DateHistogramInterval | number
  ranges?: RangeDef[]
  offset?: number
}

export interface WidgetConfig {
  collection: string
  metrics: MetricDef[]
  groupBy?: GroupByDef
  breakdownBy?: GroupByDef
  filter?: Record<string, any>
  sort?: SortType
  limit?: number
  gaugeTarget?: number
  funnelShowRate?: boolean
}

export interface DashboardWidget {
  id: string
  type: WidgetType
  title: string
  x: number
  y: number
  w: number
  h: number
  config: WidgetConfig
}

export interface Dashboard {
  id: string
  name: string
  description?: string
  layout: DashboardWidget[]
  ownerId?: string
  isGlobal: boolean
  createdAt?: string
  updatedAt?: string
}

export type AggregateMetricMap = Record<string, number>

export interface AggregateGroupedRow {
  key: string | number
  value: number
  metrics?: AggregateMetricMap
}

export interface AggregateMatrixCell {
  rowKey: string | number
  columnKey: string | number
  value: number
  metrics?: AggregateMetricMap
}

export type AggregateResult =
  | {
      type: 'single'
      value: number
      metrics?: AggregateMetricMap
    }
  | {
      type: 'grouped'
      data: AggregateGroupedRow[]
    }
  | {
      type: 'matrix'
      rows: Array<string | number>
      columns: Array<string | number>
      data: AggregateMatrixCell[]
    }

export const WIDGET_TYPE_OPTIONS: { label: string; value: WidgetType; icon: string }[] = [
  { label: '指标卡', value: 'metric', icon: 'Odometer' },
  { label: '柱状图', value: 'bar', icon: 'Histogram' },
  { label: '折线图', value: 'line', icon: 'TrendCharts' },
  { label: '饼图', value: 'pie', icon: 'PieChart' },
  { label: '环形图', value: 'ring', icon: 'PieChart' },
  { label: '面积图', value: 'area', icon: 'DataLine' },
  { label: '仪表盘', value: 'gauge', icon: 'Odometer' },
  { label: '雷达图', value: 'radar', icon: 'Aim' },
  { label: '漏斗图', value: 'funnel', icon: 'Filter' },
  { label: '数据表', value: 'dataTable', icon: 'Grid' },
]

export const METRIC_TYPE_OPTIONS: { label: string; value: MetricType; needField: boolean }[] = [
  { label: '计数', value: 'count', needField: false },
  { label: '求和', value: 'sum', needField: true },
  { label: '平均值', value: 'avg', needField: true },
  { label: '最小值', value: 'min', needField: true },
  { label: '最大值', value: 'max', needField: true },
  { label: '去重计数', value: 'uniqueCount', needField: true },
  { label: '数组长度求和', value: 'arrayLengthSum', needField: true },
  { label: '数组长度平均', value: 'arrayLengthAvg', needField: true },
  { label: '数组长度最大', value: 'arrayLengthMax', needField: true },
  { label: '数组长度最小', value: 'arrayLengthMin', needField: true },
  { label: '关联计数求和', value: 'relationCountSum', needField: true },
  { label: '关联计数平均', value: 'relationCountAvg', needField: true },
  { label: '关联计数最大', value: 'relationCountMax', needField: true },
  { label: '关联计数最小', value: 'relationCountMin', needField: true },
]

export function getMetricName(metric?: MetricDef | null): string | undefined {
  if (!metric) return undefined
  if (metric.name) return metric.name
  if (metric.type === 'count') return 'count'
  return metric.field ? `${metric.type}_${metric.field}` : metric.type
}

export function getDashboards() {
  return get<Dashboard[]>('/dashboards')
}

export function getDashboard(id: string) {
  return get<Dashboard>(`/dashboards/${id}`)
}

export function saveDashboard(dashboard: Partial<Dashboard> & { name: string }) {
  if (dashboard.id) {
    return put<Dashboard>(`/dashboards/${dashboard.id}`, dashboard)
  }
  return post<Dashboard>('/dashboards', dashboard)
}

export function deleteDashboard(id: string) {
  return del(`/dashboards/${id}`)
}

export function aggregate(params: {
  collection: string
  metrics?: MetricDef[]
  metric?: string
  field?: string
  groupBy?: GroupByDef
  breakdownBy?: GroupByDef
  groupField?: string
  filter?: Record<string, any>
  sort?: SortType
  limit?: number
}) {
  return post<AggregateResult>('/dashboards/aggregate', params)
}
