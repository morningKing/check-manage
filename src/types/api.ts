/**
 * API 相关类型定义
 *
 * 定义 API 请求和响应的通用数据结构
 */

/**
 * API 响应基础接口
 *
 * @property code - 响应状态码（0表示成功）
 * @property message - 响应消息
 * @property data - 响应数据
 */
export interface ApiResponse<T = any> {
  code: number
  message: string
  data: T
}

/**
 * 分页请求参数接口
 *
 * @property page - 当前页码（从1开始）
 * @property pageSize - 每页数量
 * @property sortField - 排序字段
 * @property sortOrder - 排序方向（asc/desc）
 */
export interface PaginationParams {
  page: number
  pageSize: number
  sortField?: string
  sortOrder?: 'asc' | 'desc'
}

/**
 * 分页响应数据接口
 *
 * @property list - 数据列表
 * @property total - 总记录数
 * @property page - 当前页码
 * @property pageSize - 每页数量
 */
export interface PaginatedData<T> {
  list: T[]
  total: number
  page: number
  pageSize: number
}

/**
 * 数据记录基础接口
 *
 * 所有业务数据记录的基础字段
 *
 * @property id - 记录唯一标识
 * @property createdAt - 创建时间
 * @property updatedAt - 更新时间
 */
export interface BaseRecord {
  id: string
  createdAt?: string
  updatedAt?: string
}

/**
 * 动态数据记录类型
 *
 * 用于动态表单数据，字段名和值都是动态的
 */
export type DynamicRecord = BaseRecord & Record<string, any>

/**
 * 上传文件信息接口
 *
 * @property uid - 文件唯一标识
 * @property name - 文件名称
 * @property url - 文件访问地址
 * @property size - 文件大小（字节）
 * @property type - 文件MIME类型
 */
export interface UploadFile {
  uid: string
  name: string
  url: string
  size?: number
  type?: string
}
