/**
 * Build an order-preserving row id for data-page imports.
 *
 * Rows imported in one batch all share `created_at` (the INSERT uses the
 * `now()` default = transaction time), so the list's `ORDER BY created_at, id`
 * falls back to `id`. Embedding a zero-padded, fixed-width sequence number makes
 * the lexicographic id order match the file's row order. A short random suffix
 * keeps the id globally unique across re-imports (id+branch is the PK).
 *
 * @param collection collection name (id prefix)
 * @param index      zero-based row index in file order
 * @param total      total number of rows in this import (sets the pad width)
 */
export function makeImportRowId(collection: string, index: number, total: number): string {
  const width = String(Math.max(total, 1)).length
  const seq = String(index).padStart(width, '0')
  const rand = Math.random().toString(36).slice(2, 8)
  return `${collection}-${seq}-${rand}`
}
