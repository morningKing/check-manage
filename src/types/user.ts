/**
 * 用户相关类型定义
 */
import type { ResolvedPermissions } from './role'

/**
 * 用户角色类型
 *
 * 自定义角色系统下，角色为任意 slug 字符串（内置：admin / developer / guest）
 */
export type UserRole = string

/**
 * 用户信息接口
 */
export interface UserInfo {
  id: string
  username: string
  displayName: string
  role: UserRole
  permissions?: ResolvedPermissions
  createdAt?: string
}

/**
 * 登录请求参数
 */
export interface LoginParams {
  username: string
  password: string
}

/**
 * 登录响应数据
 */
export interface LoginResult {
  token: string
  user: UserInfo
}

/**
 * 用户表单数据（管理端创建/编辑）
 */
export interface UserFormData {
  id?: string
  username: string
  password: string
  displayName: string
  role: UserRole
}

/**
 * 角色选项常量
 */
export const ROLE_OPTIONS: { label: string; value: UserRole }[] = [
  { label: '管理员', value: 'admin' },
  { label: '开发人员', value: 'developer' },
  { label: '访客', value: 'guest' },
]

/**
 * 角色显示名映射
 */
export const ROLE_LABELS: Record<UserRole, string> = {
  admin: '管理员',
  developer: '开发人员',
  guest: '访客',
}
