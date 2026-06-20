import { describe, it, expect, vi } from 'vitest'
import { importPageRecords } from '../importPageRecords'
import { makeStableImportId } from '../importId'

function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    batchGenerateSequenceValues: vi.fn().mockReturnValue({}),
    stripRelationFields: vi.fn((_p: string, r: any) => ({ ...r })),
    getRelationFields: vi.fn().mockReturnValue([]),
    getPrimaryKeyFields: vi.fn().mockReturnValue([]),
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

  it('derives a stable, primary-key-based id when the page has a primary key', async () => {
    const store = makeStore({
      getPrimaryKeyFields: vi.fn().mockReturnValue([{ fieldName: 'code' }]),
    })
    const post = vi.fn().mockResolvedValue({ created: 2, updated: 0, failed: 0 })
    const records = [{ code: 'SKU-1', name: 'a' }, { code: 'SKU-2', name: 'b' }]

    await importPageRecords({ store, post, pageId: 'page-products', collection: 'products', records })

    const sent = post.mock.calls[0][1].records
    // id is the deterministic hash of the primary key — identical to a fresh derivation,
    // so a delete-and-re-import of the same code reuses this exact id.
    expect(sent[0].id).toBe(makeStableImportId('products', ['SKU-1']))
    expect(sent[1].id).toBe(makeStableImportId('products', ['SKU-2']))
    // re-importing the same record yields the same id (no random suffix)
    expect(sent[0].id).toBe(makeStableImportId('products', ['SKU-1']))
  })

  it('falls back to a random row id when a primary-key value is missing', async () => {
    const store = makeStore({
      getPrimaryKeyFields: vi.fn().mockReturnValue([{ fieldName: 'code' }]),
    })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const records = [{ name: 'no-code-here' }]

    await importPageRecords({ store, post, pageId: 'page-products', collection: 'products', records })

    const sent = post.mock.calls[0][1].records
    expect(sent[0].id).not.toContain('-pk-')
    expect(sent[0].id).toMatch(/^products-/)
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
