import { describe, it, expect } from 'vitest'
import { makeImportRowId, makeStableImportId } from '../importId'

describe('makeImportRowId', () => {
  it('prefixes with the collection name', () => {
    expect(makeImportRowId('orders', 0, 10)).toMatch(/^orders-/)
  })

  it('zero-pads the sequence to the width of total', () => {
    // total=1000 -> width 4
    expect(makeImportRowId('c', 5, 1000)).toMatch(/^c-0005-/)
    // total=10 -> width 2
    expect(makeImportRowId('c', 3, 10)).toMatch(/^c-03-/)
  })

  it('generates ids that sort lexicographically in file order (incl. powers of ten)', () => {
    const total = 23 // crosses the 9->10 boundary
    const ids = Array.from({ length: total }, (_, i) => makeImportRowId('c', i, total))
    const sorted = [...ids].sort()
    expect(sorted).toEqual(ids)
  })

  it('includes a random suffix so two calls for the same index differ', () => {
    const a = makeImportRowId('c', 1, 10)
    const b = makeImportRowId('c', 1, 10)
    expect(a).not.toBe(b)
    expect(a.slice(0, 'c-01-'.length)).toBe(b.slice(0, 'c-01-'.length)) // same prefix+seg
  })

  it('handles total=0 without throwing or zero-width padding', () => {
    expect(makeImportRowId('c', 0, 0)).toMatch(/^c-0-/)
  })
})

describe('makeStableImportId', () => {
  it('prefixes with the collection name and a -pk- marker', () => {
    expect(makeStableImportId('products', ['SKU-1'])).toMatch(/^products-pk-/)
  })

  it('is deterministic: same primary-key value always yields the same id', () => {
    const a = makeStableImportId('products', ['SKU-1'])
    const b = makeStableImportId('products', ['SKU-1'])
    expect(a).toBe(b)
  })

  it('different primary-key values yield different ids', () => {
    expect(makeStableImportId('products', ['SKU-1'])).not.toBe(
      makeStableImportId('products', ['SKU-2'])
    )
  })

  it('supports composite keys without boundary collisions', () => {
    // ["a","bc"] and ["ab","c"] must not collapse to the same id
    expect(makeStableImportId('c', ['a', 'bc'])).not.toBe(makeStableImportId('c', ['ab', 'c']))
    // composite order matters
    expect(makeStableImportId('c', ['a', 'b'])).not.toBe(makeStableImportId('c', ['b', 'a']))
  })

  it('coerces numeric keys to strings deterministically', () => {
    expect(makeStableImportId('c', [42])).toBe(makeStableImportId('c', ['42']))
  })

  it('produces a url/id-safe string (base36 hash, no random suffix)', () => {
    const id = makeStableImportId('c', ['关键-值 with space'])
    expect(id).toMatch(/^c-pk-[0-9a-z]+$/)
  })
})
