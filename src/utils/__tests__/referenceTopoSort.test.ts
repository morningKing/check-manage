import { describe, it, expect } from 'vitest'
import { buildReferenceOrder } from '../referenceTopoSort'
import type { PageConfig } from '@/types'

function page(id: string, refs: Array<{ type: 'reference' | 'quoteSelect'; target: string }> = []): PageConfig {
  return {
    id,
    name: id,
    fields: refs.map((r, i) => ({
      id: `${id}-f${i}`,
      label: `f${i}`,
      fieldName: `f${i}`,
      controlType: r.type,
      required: false,
      order: i,
      ...(r.type === 'reference'
        ? { referenceConfig: { targetCollection: r.target, displayField: 'name', inheritFields: [] } }
        : { quoteConfig: { targetCollection: r.target, displayField: 'name' } }),
    })),
  } as unknown as PageConfig
}

describe('buildReferenceOrder', () => {
  it('orders referenced collection before the referencing one', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-b', [{ type: 'reference', target: 'a' }]),
      page('page-a'),
    ])
    expect(cycles).toEqual([])
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('b'))
  })

  it('handles diamond dependencies', () => {
    const { order } = buildReferenceOrder([
      page('page-d', [{ type: 'reference', target: 'b' }, { type: 'quoteSelect', target: 'c' }]),
      page('page-b', [{ type: 'reference', target: 'a' }]),
      page('page-c', [{ type: 'reference', target: 'a' }]),
      page('page-a'),
    ])
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('b'))
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('c'))
    expect(order.indexOf('b')).toBeLessThan(order.indexOf('d'))
    expect(order.indexOf('c')).toBeLessThan(order.indexOf('d'))
  })

  it('ignores self-reference', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'a' }]),
    ])
    expect(cycles).toEqual([])
    expect(order).toEqual(['a'])
  })

  it('ignores references to collections outside the batch', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'external' }]),
    ])
    expect(cycles).toEqual([])
    expect(order).toEqual(['a'])
  })

  it('detects a cycle and still returns all nodes', () => {
    const { order, cycles } = buildReferenceOrder([
      page('page-a', [{ type: 'reference', target: 'b' }]),
      page('page-b', [{ type: 'reference', target: 'a' }]),
    ])
    expect(cycles.length).toBeGreaterThan(0)
    expect([...order].sort()).toEqual(['a', 'b'])
  })

  it('returns empty for empty input', () => {
    expect(buildReferenceOrder([])).toEqual({ order: [], cycles: [] })
  })
})
