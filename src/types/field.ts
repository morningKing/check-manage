/**
 * 字段配置相关类型定义
 *
 * 定义表单字段的配置结构，支持多种控件类型
 */

/**
 * 控件类型枚举
 *
 * 定义系统支持的所有表单控件类型
 */
export type ControlType =
  | 'text'        // 单行文本输入框
  | 'textarea'    // 多行文本输入框
  | 'number'      // 数字输入框
  | 'select'      // 单选下拉框
  | 'multiSelect' // 多选下拉框
  | 'radio'       // 单选按钮组
  | 'checkbox'    // 复选框组
  | 'date'        // 日期选择器
  | 'datetime'    // 日期时间选择器
  | 'file'        // 文件上传
  | 'image'       // 图片上传
  | 'relation'    // 多对多关联
  | 'reference'   // 数据引用（一对多依赖）
  | 'autoTimestamp' // 自动时间戳（新增/修改时自动填充）
  | 'autoSequence'  // 自增序列（新增时自动生成递增编号）
  | 'quoteSelect'   // 引用选择（单向多选引用另一集合记录）
  | 'richText'      // 富文本编辑器
  | 'markdown'      // Markdown 编辑器（前端渲染）
  | 'compositeText' // 组合文本（自动拼接其他字段值）
  | 'statusBadge'   // 状态徽标（只读，图标+颜色展示离散阶段，第三方系统异步回写）

/**
 * 控件类型配置
 *
 * 用于控件类型选择下拉框的选项
 */
export const CONTROL_TYPE_OPTIONS: { label: string; value: ControlType }[] = [
  { label: '单行文本', value: 'text' },
  { label: '多行文本', value: 'textarea' },
  { label: '富文本', value: 'richText' },
  { label: 'Markdown', value: 'markdown' },
  { label: '数字输入', value: 'number' },
  { label: '单选下拉', value: 'select' },
  { label: '多选下拉', value: 'multiSelect' },
  { label: '单选按钮', value: 'radio' },
  { label: '复选框', value: 'checkbox' },
  { label: '日期选择', value: 'date' },
  { label: '日期时间', value: 'datetime' },
  { label: '文件上传', value: 'file' },
  { label: '图片上传', value: 'image' },
  { label: '关联关系', value: 'relation' },
  { label: '数据引用', value: 'reference' },
  { label: '自动时间戳', value: 'autoTimestamp' },
  { label: '自增序列', value: 'autoSequence' },
  { label: '引用选择', value: 'quoteSelect' },
  { label: '组合文本', value: 'compositeText' },
  { label: '状态徽标', value: 'statusBadge' }
]

/**
 * 字段选项接口
 *
 * 用于下拉框、单选、多选等控件的选项
 *
 * @property label - 选项显示文本
 * @property value - 选项值
 */
export interface FieldOption {
  label: string
  value: string | number | boolean
}

/**
 * 选项来源配置接口
 *
 * 定义选项数据的获取方式
 *
 * @property type - 来源类型：static（静态配置）、api（接口获取）或 collection（数据页数据）
 * @property url - API地址（type为api时需要）
 * @property labelField - 标签字段名（api/collection 类型使用）
 * @property valueField - 值字段名（api/collection 类型使用）
 * @property collection - 目标集合名称（type为collection时需要）
 */
export interface OptionsSource {
  type: 'static' | 'api' | 'collection'
  url?: string
  labelField?: string
  valueField?: string
  collection?: string
}

/**
 * 关联关系配置接口
 *
 * @property targetCollection - 目标集合名称，如 "inspection-template"
 * @property displayField - 目标集合中用于显示的字段名，如 "templateName"
 * @property targetField - 目标集合中反向关联的字段名，如 "cases"
 */
export interface RelationConfig {
  targetCollection: string
  displayField: string
  targetField: string
}

/**
 * 数据引用配置接口
 *
 * @property targetCollection - 目标集合名称
 * @property displayField - 下拉框中显示的字段名
 * @property inheritFields - 继承显示的父字段名列表
 */
export interface ReferenceConfig {
  targetCollection: string
  displayField: string
  inheritFields: string[]
}

/**
 * 引用选择配置接口
 *
 * @property targetCollection - 目标集合名称
 * @property displayField - 下拉框中显示的字段名
 */
export interface QuoteConfig {
  targetCollection: string
  displayField: string
}

/**
 * 自增序列配置接口
 *
 * @property prefix - 序列前缀，如 "IC-"
 * @property max - 数字上限，如 999 表示最多编到 999
 */
export interface SequenceConfig {
  prefix: string
  max: number
}

export interface CompositeTextConfig {
  sourceFields: string[]
  separator: string
}

/**
 * 状态徽标选项接口
 *
 * @property value - 选项值（与字段实际存储值一致）
 * @property label - 显示文本
 * @property icon - Element Plus 图标名
 * @property color - 图标/文字颜色（十六进制），不填用默认灰
 * @property animated - 是否持续旋转动画（表示"进行中"）
 * @property terminal - 是否终态：到达后前端停止轮询、后端超时任务跳过
 */
export interface StatusBadgeOption {
  value: string
  label: string
  icon: string
  color?: string
  animated?: boolean
  terminal?: boolean
}

/**
 * 状态徽标配置接口
 *
 * @property options - 各阶段选项列表
 * @property pollIntervalSec - 轮询间隔（秒），默认 5
 * @property timeoutSec - 超时阈值（秒），不填则不启用超时兜底
 * @property timeoutValue - 超时后写入的选项值，必须是 options 里某个 value
 */
export interface StatusBadgeConfig {
  options: StatusBadgeOption[]
  pollIntervalSec?: number
  timeoutSec?: number
  timeoutValue?: string
}

/**
 * 验证规则接口
 *
 * 定义字段的验证规则
 */
export interface ValidationRule {
  type: 'required' | 'min' | 'max' | 'pattern' | 'custom'
  value?: any
  message: string
}

/**
 * 字段配置接口
 *
 * 定义单个表单字段的完整配置
 *
 * @property id - 字段唯一标识
 * @property label - 字段显示名称
 * @property fieldName - 后端字段名（与API对应）
 * @property controlType - 控件类型
 * @property required - 是否必填
 * @property order - 显示顺序
 * @property placeholder - 占位提示文本
 * @property defaultValue - 默认值
 * @property options - 静态选项列表（用于下拉框等）
 * @property optionsSource - 选项来源配置
 * @property validation - 验证规则列表
 * @property disabled - 是否禁用
 * @property hidden - 是否隐藏
 * @property width - 字段宽度（如 '100%', '50%'）
 */
export interface WorkflowCondition {
  field: string
  rule: 'notEmpty' | 'equals' | 'notEquals'
  value?: any
  message: string
}

export interface WorkflowAction {
  type: 'setField' | 'runScript'
  field?: string
  value?: string
  scriptId?: string
}

export interface WorkflowTransition {
  from: string
  to: string
  label: string
  roles?: string[]
  conditions?: WorkflowCondition[]
  actions?: WorkflowAction[]
}

export interface WorkflowConfig {
  enabled: boolean
  transitions: WorkflowTransition[]
}

export interface FieldConfig {
  id: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder?: string
  defaultValue?: any
  options?: FieldOption[]
  optionsSource?: OptionsSource
  validation?: ValidationRule[]
  disabled?: boolean
  hidden?: boolean
  width?: string
  relationConfig?: RelationConfig
  isPrimaryKey?: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
  statusBadgeConfig?: StatusBadgeConfig
}

/**
 * 字段表单数据接口
 *
 * 用于字段编辑表单的数据结构
 */
export interface FieldFormData {
  id?: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder: string
  defaultValue: any
  options: FieldOption[]
  optionsSource: OptionsSource
  relationConfig?: RelationConfig
  isPrimaryKey: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
  statusBadgeConfig: StatusBadgeConfig
}

/**
 * 创建空字段表单数据
 *
 * @param order - 字段顺序
 * @returns 初始化的字段表单数据
 */
export function createEmptyFieldFormData(order: number = 1): FieldFormData {
  return {
    label: '',
    fieldName: '',
    controlType: 'text',
    required: false,
    order,
    placeholder: '',
    defaultValue: null,
    options: [],
    optionsSource: { type: 'static' },
    relationConfig: { targetCollection: '', displayField: '', targetField: '' },
    isPrimaryKey: false,
    referenceConfig: { targetCollection: '', displayField: '', inheritFields: [] },
    sequenceConfig: { prefix: '', max: 999 },
    quoteConfig: { targetCollection: '', displayField: '' },
    compositeTextConfig: { sourceFields: [], separator: ' - ' },
    statusBadgeConfig: { options: [], pollIntervalSec: 5 }
  }
}
