/**
 * 列视图相关类型定义
 */

export interface ColumnView {
  id: number
  pageId: string
  name: string
  isPublic: boolean
  isDefault: boolean
  creatorId: number | null
  columns: ColumnConfigItem[]
  sortConfig: SortConfigItem[]
  filterConfig: FilterConfigItem[]
  groupConfig: GroupConfig | null
  createdAt: string
  updatedAt: string
}

export interface ColumnConfigItem {
  fieldId: string
  visible: boolean
  order: number
  width: string
}

export interface SortConfigItem {
  field: string
  direction: 'asc' | 'desc'
}

export interface FilterConfigItem {
  field: string
  operator: '=' | '!=' | 'contains' | '>' | '<' | '>=' | '<='
  value: any
}

export interface GroupConfig {
  field: string
  order?: string[]
}

export interface GetViewsResponse {
  views: ColumnView[]
  defaultViewId: number | null
}

export interface CreateViewRequest {
  name: string
  isPublic: boolean
  columns: ColumnConfigItem[]
  sortConfig?: SortConfigItem[]
  filterConfig?: FilterConfigItem[]
  groupConfig?: GroupConfig | null
}

export interface UpdateViewRequest {
  name?: string
  isPublic?: boolean
  columns?: ColumnConfigItem[]
  sortConfig?: SortConfigItem[]
  filterConfig?: FilterConfigItem[]
  groupConfig?: GroupConfig | null
}

export interface CopyViewRequest {
  name?: string
  isPublic?: boolean
}
