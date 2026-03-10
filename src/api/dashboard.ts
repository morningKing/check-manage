import { get, post, put, del } from '@/utils/request'

export interface DashboardWidget {
  id: string
  type: 'statCard' | 'barChart' | 'lineChart' | 'pieChart' | 'table'
  title: string
  x: number
  y: number
  w: number
  h: number
  config: {
    collection: string
    metric: 'count' | 'sum' | 'avg' | 'min' | 'max'
    field?: string
    groupField?: string
    filter?: Record<string, any>
  }
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

export interface AggregateResult {
  type: 'single' | 'grouped'
  value?: number
  data?: Array<{ key: string; value: number }>
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
  metric: string
  field?: string
  groupField?: string
  filter?: Record<string, any>
}) {
  return post<AggregateResult>('/dashboards/aggregate', params)
}
