import { describe, it, expect } from 'vitest'
import { makeImportRowId } from '../importId'

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
