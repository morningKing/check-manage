/**
 * 操作日志相关类型定义
 */
/**
 * 操作类型
 */
export type OperationAction = 'create' | 'update' | 'delete'

/**
 * 目标类型
 */
export type OperationTargetType = 'menu' | 'page_config' | 'dynamic_data' | 'user' | 'relation'

/**
 * 操作日志记录
 */
export interface OperationLog {
  id: string
  action: OperationAction
  targetType: OperationTargetType
  targetId: string
  targetName: string
  description: string
  operatorId: string
  operatorName: string
  operatorRole: string
  createdAt: string
  batchId?: string
  batchDesc?: string
}

/**
 * 操作日志表格行（含树形子行）
 */
export interface OperationLogRow extends OperationLog {
  children?: OperationLogRow[]
  batchCount?: number
}

/**
 * 操作日志查询参数
 */
export interface OperationLogQuery {
  page?: number
  pageSize?: number
  action?: string
  targetType?: string
  operatorName?: string
  startTime?: string
  endTime?: string
}

/**
 * 操作日志分页响应
 */
export interface OperationLogPage {
  items: OperationLog[]
  total: number
}

/**
 * 操作类型显示标签
 */
export const ACTION_LABELS: Record<OperationAction, string> = {
  create: '新增',
  update: '修改',
  delete: '删除',
}

/**
 * 操作类型 Tag 颜色
 */
export const ACTION_TAG_TYPES: Record<OperationAction, string> = {
  create: 'success',
  update: '',
  delete: 'danger',
}

/**
 * 目标类型显示标签
 */
export const TARGET_TYPE_LABELS: Record<OperationTargetType, string> = {
  menu: '菜单',
  page_config: '页面配置',
  dynamic_data: '动态数据',
  user: '用户',
  relation: '关联关系',
}

/**
 * 操作类型选项（用于筛选下拉框）
 */
export const ACTION_OPTIONS: { label: string; value: OperationAction }[] = [
  { label: '新增', value: 'create' },
  { label: '修改', value: 'update' },
  { label: '删除', value: 'delete' },
]

/**
 * 目标类型选项（用于筛选下拉框）
 */
export const TARGET_TYPE_OPTIONS: { label: string; value: OperationTargetType }[] = [
  { label: '菜单', value: 'menu' },
  { label: '页面配置', value: 'page_config' },
  { label: '动态数据', value: 'dynamic_data' },
  { label: '用户', value: 'user' },
  { label: '关联关系', value: 'relation' },
]
