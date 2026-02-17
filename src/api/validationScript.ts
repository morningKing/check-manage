import { get, post, put, del } from '@/utils/request'
import type { ValidationScript } from '@/types'

export function getValidationScripts() {
  return get<ValidationScript[]>('/validationScripts')
}

export function createValidationScript(data: Partial<ValidationScript>) {
  return post<ValidationScript>('/validationScripts', data)
}

export function updateValidationScript(id: string, data: Partial<ValidationScript>) {
  return put<ValidationScript>(`/validationScripts/${id}`, data)
}

export function deleteValidationScript(id: string) {
  return del(`/validationScripts/${id}`)
}

export function testValidationScript(
  id: string,
  testData: { record: Record<string, any>; action: string; oldData?: Record<string, any>; fields: any[]; collection: string }
) {
  return post<{ success: boolean; errors: string[]; warnings: string[]; pendingRelations: any[] }>(
    `/validationScripts/${id}/test`,
    testData
  )
}
