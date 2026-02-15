import { v4 as uuidv4 } from 'uuid'

let _batchId: string | null = null
let _batchDesc: string | null = null

export function getBatchHeaders(): Record<string, string> {
  const h: Record<string, string> = {}
  if (_batchId) h['X-Batch-Id'] = _batchId
  if (_batchDesc) h['X-Batch-Desc'] = encodeURIComponent(_batchDesc)
  return h
}

export async function withBatch<T>(desc: string, fn: () => Promise<T>): Promise<T> {
  _batchId = `batch-${uuidv4().slice(0, 12)}`
  _batchDesc = desc
  try {
    return await fn()
  } finally {
    _batchId = null
    _batchDesc = null
  }
}
