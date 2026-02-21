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

/**
 * 控件类型配置
 *
 * 用于控件类型选择下拉框的选项
 */
export const CONTROL_TYPE_OPTIONS: { label: string; value: ControlType }[] = [
  { label: '单行文本', value: 'text' },
  { label: '多行文本', value: 'textarea' },
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
  { label: '自动时间戳', value: 'autoTimestamp' }
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
 * @property type - 来源类型：static（静态配置）或 api（接口获取）
 * @property url - API地址（type为api时需要）
 * @property labelField - API响应中的标签字段名
 * @property valueField - API响应中的值字段名
 */
export interface OptionsSource {
  type: 'static' | 'api'
  url?: string
  labelField?: string
  valueField?: string
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
    referenceConfig: { targetCollection: '', displayField: '', inheritFields: [] }
  }
}
