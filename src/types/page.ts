/**
 * 页面配置相关类型定义
 *
 * 定义页面配置的数据结构，包含页面基本信息和字段配置列表
 */

import type { FieldConfig } from './field'

/**
 * 继承字段映射接口
 *
 * 用于删除绑定时，将源记录的字段值映射到目标记录
 */
export interface InheritFieldMapping {
  /** 源字段名（被删除记录的字段） */
  sourceField: string
  /** 目标字段名（目标集合的字段） */
  targetField: string
}

/**
 * 删除绑定配置接口
 *
 * 定义删除数据时的绑定行为：
 * - 弹出表单让用户填写信息
 * - 保存到目标集合
 * - 再执行删除操作
 */
export interface DeleteBindingConfig {
  /** 是否启用删除绑定 */
  enabled: boolean
  /** 目标集合（不含 page- 前缀） */
  targetCollection: string
  /** 对话框标题 */
  dialogTitle?: string
  /** 对话框宽度 */
  dialogWidth?: string
  /** 继承字段映射（从被删除记录自动填充到目标记录） */
  inheritFields: InheritFieldMapping[]
  /** 表单字段定义（用户填写的字段） */
  fields: FieldConfig[]
  /** 自动填充操作者信息 */
  autoFillOperator: boolean
}

/**
 * 页面配置接口
 *
 * 定义单个数据页面的完整配置
 *
 * @property id - 页面唯一标识（与菜单绑定）
 * @property name - 页面名称
 * @property description - 页面描述
 * @property apiEndpoint - 后端API端点（用于数据CRUD操作）
 * @property fields - 字段配置列表
 * @property createdAt - 创建时间
 * @property updatedAt - 更新时间
 */
export interface KanbanConfig {
  groupField: string
  cardTitle: string
  cardFields: string[]
  columnOrder?: string[]
  cardColorField?: string
}

export interface CalendarConfig {
  /** 日期字段名（必填，必须是 date 或 datetime 类型） */
  dateField: string
  /** 结束日期字段名（可选，支持跨天事件） */
  endDateField?: string
  /** 卡片标题字段 */
  cardTitle: string
  /** 颜色标记字段（可选，按状态/优先级着色） */
  cardColorField?: string
  /** 默认视图模式 */
  defaultMode?: 'month' | 'week'
}

export interface ExcelViewConfig {
  /** 是否启用 Excel 视图 */
  enabled?: boolean
  /** 默认显示的字段 */
  defaultFields?: string[]
  /** 冻结列数（默认冻结序号列） */
  freezeColumns?: number
}

export interface GanttConfig {
  /** 开始日期字段（必填，必须是 date 或 datetime 类型） */
  startDateField: string
  /** 结束日期字段（必填，必须是 date 或 datetime 类型） */
  endDateField: string
  /** 任务标题字段（必填，用于 Y 轴显示） */
  titleField: string
  /** 进度字段（可选，0-100 数字，显示完成百分比） */
  progressField?: string
  /** 依赖字段（可选，存储依赖任务 ID 数组） */
  dependenciesField?: string
  /** 颜色标记字段（可选，按状态/优先级着色） */
  colorField?: string
}

export interface ViewConfig {
  defaultView?: 'table' | 'kanban' | 'excel' | 'calendar' | 'gantt'
  kanban?: KanbanConfig
  excel?: ExcelViewConfig
  calendar?: CalendarConfig
  gantt?: GanttConfig
}

export interface PageConfig {
  id: string
  name: string
  description?: string
  apiEndpoint: string
  fields: FieldConfig[]
  exportScripts?: string[]
  rowExportScripts?: string[]
  apiPublic?: boolean
  apiWritable?: boolean
  validationScript?: string
  viewConfig?: ViewConfig
  deleteBinding?: DeleteBindingConfig
  createdAt: string
  updatedAt: string
}

/**
 * 页面表单数据接口
 *
 * 用于页面配置编辑表单的数据结构
 */
export interface PageFormData {
  id?: string
  name: string
  description: string
  apiEndpoint: string
  exportScripts?: string[]
  rowExportScripts?: string[]
  apiPublic?: boolean
  apiWritable?: boolean
  validationScript?: string
  viewConfig?: ViewConfig
  deleteBinding?: DeleteBindingConfig
}

/**
 * 创建空页面表单数据
 *
 * @returns 初始化的页面表单数据
 */
export function createEmptyPageFormData(): PageFormData {
  return {
    name: '',
    description: '',
    apiEndpoint: '/api/data/',
    exportScripts: [],
    rowExportScripts: [],
    apiPublic: false,
    apiWritable: false,
    validationScript: '',
    viewConfig: {},
  }
}

/**
 * 创建空页面配置
 *
 * @param id - 页面ID
 * @param name - 页面名称
 * @returns 初始化的页面配置
 */
export function createEmptyPageConfig(id: string, name: string): PageConfig {
  const now = new Date().toISOString()
  return {
    id,
    name,
    description: '',
    apiEndpoint: `/api/data/${id}`,
    fields: [],
    createdAt: now,
    updatedAt: now
  }
}
