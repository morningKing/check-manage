/**
 * 认证相关 API 接口
 */
import { post, get, put } from '@/utils/request'
import type { LoginParams, LoginResult, UserInfo } from '@/types'

/** 用户登录 */
export function login(params: LoginParams) {
  return post<LoginResult>('/auth/login', params)
}

/** 获取当前用户信息 */
export function getCurrentUser() {
  return get<UserInfo>('/auth/me')
}

/** 修改密码 */
export function changePassword(data: { oldPassword: string; newPassword: string }) {
  return put('/auth/password', data)
}
