/**
 * 用户管理 API 接口（管理员）
 */
import { get, post, put, del } from '@/utils/request'
import type { UserInfo } from '@/types'

/** 获取用户列表 */
export function getUserList() {
  return get<UserInfo[]>('/users')
}

/** 创建用户 */
export function createUser(data: { username: string; password: string; displayName: string; role: string }) {
  return post<UserInfo>('/users', data)
}

/** 更新用户 */
export function updateUser(id: string, data: Partial<{ displayName: string; role: string; password: string }>) {
  return put<UserInfo>(`/users/${id}`, data)
}

/** 删除用户 */
export function deleteUser(id: string) {
  return del(`/users/${id}`)
}
