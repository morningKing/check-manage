import { describe, it, expect, vi } from 'vitest'

vi.mock('@/utils/importPageRecords', () => ({
  importPageRecords: vi.fn(async ({ collection }: any) => ({
    success: 1, failed: 0, created: 1, updated: 0, _collection: collection,
  })),
}))

import { importPageRecords } from '@/utils/importPageRecords'
import { runBatchImport } from '../useBatchImport'
import type { PageConfig } from '@/types'

function page(id: string, target?: string): PageConfig {
  return {
    id, name: id,
    fields: target
      ? [{ id: 'f', label: 'f', fieldName: 'f', controlType: 'reference', required: false, order: 0,
          referenceConfig: { targetCollection: target, displayField: 'name', inheritFields: [] } }]
      : [],
  } as unknown as PageConfig
}

describe('runBatchImport', () => {
  it('imports pages in dependency order then re-resolves each', async () => {
    const store = {
      reResolveReferences: vi.fn().mockResolvedValue({ updated: 0, pending: 0 }),
    }
    const post = vi.fn()
    // b 引用 a → 应先导 a 再导 b
    const configs = [page('page-b', 'a'), page('page-a')]
    const pages = [
      { pageId: 'page-b', collection: 'b', records: [{ n: 1 }] },
      { pageId: 'page-a', collection: 'a', records: [{ n: 1 }] },
    ]

    const results = await runBatchImport({ store, post, pages, allConfigs: configs })

    const importedOrder = (importPageRecords as any).mock.calls.map((c: any[]) => c[0].collection)
    expect(importedOrder).toEqual(['a', 'b'])
    expect(store.reResolveReferences).toHaveBeenCalledWith('page-a')
    expect(store.reResolveReferences).toHaveBeenCalledWith('page-b')
    expect(results.map((r) => r.collection)).toEqual(['a', 'b'])
  })
})
