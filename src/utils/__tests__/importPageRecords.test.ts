import { describe, it, expect, vi } from 'vitest'
import { importPageRecords } from '../importPageRecords'

function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    batchGenerateSequenceValues: vi.fn().mockReturnValue({}),
    stripRelationFields: vi.fn((_p: string, r: any) => ({ ...r })),
    getRelationFields: vi.fn().mockReturnValue([]),
    ...overrides,
  }
}

describe('importPageRecords', () => {
  it('resolves references then calls batch-create and returns counts', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({ success: true, created: 2, updated: 0, failed: 0 })
    const records = [{ name: 'a' }, { name: 'b' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(store.resolveReferenceImportValues).toHaveBeenCalledWith('page-orders', records, expect.any(Map))
    expect(store.resolveRelationImportValues).toHaveBeenCalled()
    expect(store.resolveQuoteImportValues).toHaveBeenCalled()
    expect(store.resolveCollectionSelectImportValues).toHaveBeenCalled()
    expect(post).toHaveBeenCalledWith('/orders/batch-create', expect.objectContaining({
      records: expect.any(Array),
    }))
    expect(result).toEqual({ success: 2, failed: 0, created: 2, updated: 0 })
  })

  it('reports progress for the final batch', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const onProgress = vi.fn()
    await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }], onProgress,
    })
    expect(onProgress).toHaveBeenCalledWith(1, 1)
  })

  it('counts batch failure without throwing', async () => {
    const store = makeStore()
    const post = vi.fn().mockRejectedValue(new Error('boom'))
    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }],
    })
    expect(result.failed).toBe(1)
    expect(result.success).toBe(0)
  })
})
