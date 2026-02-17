import { get, post, put, del } from '@/utils/request'
import type { ApiKey } from '@/types'

export function getApiKeyList() {
  return get<ApiKey[]>('/apiKeys')
}

export function createApiKey(data: { name: string }) {
  return post<ApiKey>('/apiKeys', data)
}

export function toggleApiKey(id: string, isActive: boolean) {
  return put<ApiKey>(`/apiKeys/${id}`, { isActive })
}

export function deleteApiKey(id: string) {
  return del(`/apiKeys/${id}`)
}
