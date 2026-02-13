/**
 * 关联关系 API 接口
 */

import { get, put, del } from '@/utils/request'

export type RelationData = Record<string, string[]>

export function getRecordRelations(collection: string, recordId: string) {
  return get<RelationData>(`/relations/${collection}/${recordId}`)
}

export function updateFieldRelations(
  collection: string,
  recordId: string,
  fieldName: string,
  targetCollection: string,
  targetField: string,
  ids: string[]
) {
  return put(`/relations/${collection}/${recordId}/${fieldName}`, {
    targetCollection,
    targetField,
    ids
  })
}

export function deleteRecordRelations(collection: string, recordId: string) {
  return del(`/relations/${collection}/${recordId}`)
}
