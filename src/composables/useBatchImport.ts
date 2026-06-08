import type { PageConfig } from '@/types'
import { buildReferenceOrder } from '@/utils/referenceTopoSort'
import { importPageRecords, type ImportPageResult } from '@/utils/importPageRecords'

export interface BatchImportPageInput {
  pageId: string
  collection: string
  records: Record<string, any>[]
}

export interface BatchImportPageResult extends ImportPageResult {
  collection: string
  reResolved: number
  pending: number
}

export interface RunBatchImportParams {
  store: any
  post: <T>(url: string, body: any) => Promise<T>
  pages: BatchImportPageInput[]
  allConfigs: PageConfig[]
  onPageProgress?: (collection: string, current: number, total: number) => void
}

/**
 * 按引用依赖顺序逐页导入，收尾对每个导入过的页重新解析引用。
 */
export async function runBatchImport(params: RunBatchImportParams): Promise<BatchImportPageResult[]> {
  const { store, post, pages, allConfigs, onPageProgress } = params
  const { order } = buildReferenceOrder(allConfigs)
  const orderIndex = new Map(order.map((c, i) => [c, i]))

  const sorted = [...pages].sort(
    (a, b) => (orderIndex.get(a.collection) ?? 9999) - (orderIndex.get(b.collection) ?? 9999),
  )

  const results: BatchImportPageResult[] = []
  for (const p of sorted) {
    const r = await importPageRecords({
      store, post, pageId: p.pageId, collection: p.collection, records: p.records,
      onProgress: (cur, total) => onPageProgress?.(p.collection, cur, total),
    })
    results.push({ ...r, collection: p.collection, reResolved: 0, pending: 0 })
  }

  // 收尾重解析（顺序同上，确保被引用页已落库）
  for (const res of results) {
    const page = sorted.find((p) => p.collection === res.collection)!
    try {
      const { updated, pending } = await store.reResolveReferences(page.pageId)
      res.reResolved = updated
      res.pending = pending
    } catch {
      // 重解析失败不影响已导入数据
    }
  }

  return results
}
