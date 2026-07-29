import { get, post } from '@/utils/request'

export interface ImportRunSummary {
  id: string
  fileName: string
  branchId: string
  successCount: number
  createdCount: number
  updatedCount: number
  failedCount: number
  status: 'success' | 'partial'
  operator: string
  createdAt: string
}

export interface ImportRunFailureRow {
  recordId: string
  originalRecord: Record<string, any>
  payload: { id: string; data: any; relations: Record<string, string[]> }
  reason: string
  createdAt: string
}

export function createImportRun(body: {
  pageId: string
  collection: string
  branchId: string
  fileName: string
  successCount: number
  createdCount: number
  updatedCount: number
  failedCount: number
  failures: Array<{ recordId: string; originalRecord: any; payload: any; reason: string }>
}): Promise<{ id: string }> {
  return post('/importRuns', body)
}

export function listImportRuns(
  pageId: string,
  collection: string,
  limit = 20,
  offset = 0,
): Promise<{ runs: ImportRunSummary[]; total: number }> {
  return get('/importRuns', { pageId, collection, limit, offset })
}

export function getImportRunDetail(
  id: string,
): Promise<{ run: ImportRunSummary; failures: ImportRunFailureRow[] }> {
  return get(`/importRuns/${id}`)
}

export function syncImportRunRetryResult(
  id: string,
  body: { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number },
): Promise<{ successCount: number; createdCount: number; updatedCount: number; failedCount: number; status: string }> {
  return post(`/importRuns/${id}/retry-result`, body)
}
