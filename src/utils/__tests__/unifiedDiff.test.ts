import { describe, it, expect } from 'vitest'
import { parseUnifiedDiff } from '../unifiedDiff'

const DIFF = `diff --git a/a.txt b/a.txt
index 111..222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line1
-line2
+CHANGED
 line3
`

describe('parseUnifiedDiff', () => {
  it('aligns context, removal and addition into side-by-side rows', () => {
    const hunks = parseUnifiedDiff(DIFF)
    expect(hunks).toHaveLength(1)
    const rows = hunks[0].rows
    // context line1
    expect(rows[0]).toEqual({ type: 'context', left: 'line1', right: 'line1', leftNo: 1, rightNo: 1 })
    // removal paired with addition on the same row
    expect(rows[1]).toEqual({ type: 'change', left: 'line2', right: 'CHANGED', leftNo: 2, rightNo: 2 })
    // context line3
    expect(rows[2]).toEqual({ type: 'context', left: 'line3', right: 'line3', leftNo: 3, rightNo: 3 })
  })

  it('pads unbalanced add/remove runs with blank fillers', () => {
    const diff = `--- a/x
+++ b/x
@@ -1,1 +1,2 @@
-old
+new1
+new2
`
    const rows = parseUnifiedDiff(diff)[0].rows
    expect(rows[0]).toEqual({ type: 'change', left: 'old', right: 'new1', leftNo: 1, rightNo: 1 })
    expect(rows[1]).toEqual({ type: 'add', left: null, right: 'new2', leftNo: null, rightNo: 2 })
  })

  it('returns empty array for empty diff', () => {
    expect(parseUnifiedDiff('')).toEqual([])
  })

  it('does not emit a phantom row for the trailing newline', () => {
    const rows = parseUnifiedDiff('@@ -1,1 +1,1 @@\n line1\n')[0].rows
    expect(rows).toHaveLength(1)
    expect(rows[0]).toEqual({ type: 'context', left: 'line1', right: 'line1', leftNo: 1, rightNo: 1 })
  })

  it('keeps a genuine blank context line (single-space prefix)', () => {
    const rows = parseUnifiedDiff('@@ -1,2 +1,2 @@\n \n line2\n')[0].rows
    expect(rows).toHaveLength(2)
    expect(rows[0]).toEqual({ type: 'context', left: '', right: '', leftNo: 1, rightNo: 1 })
    expect(rows[1]).toEqual({ type: 'context', left: 'line2', right: 'line2', leftNo: 2, rightNo: 2 })
  })

  it('skips the "no newline at end of file" marker and keeps line numbers aligned', () => {
    const diff = [
      '@@ -1,2 +1,2 @@',
      ' line1',
      '-line2',
      '\\ No newline at end of file',
      '+line2b',
      '\\ No newline at end of file',
    ].join('\n')
    const rows = parseUnifiedDiff(diff)[0].rows
    expect(rows).toHaveLength(2)
    expect(rows[1]).toEqual({ type: 'change', left: 'line2', right: 'line2b', leftNo: 2, rightNo: 2 })
  })

  it('handles an additions-only hunk', () => {
    const rows = parseUnifiedDiff('@@ -1,1 +1,3 @@\n line1\n+new2\n+new3\n')[0].rows
    expect(rows).toEqual([
      { type: 'context', left: 'line1', right: 'line1', leftNo: 1, rightNo: 1 },
      { type: 'add', left: null, right: 'new2', leftNo: null, rightNo: 2 },
      { type: 'add', left: null, right: 'new3', leftNo: null, rightNo: 3 },
    ])
  })

  it('resets line counters across multiple hunks', () => {
    const diff = '@@ -1,1 +1,1 @@\n a\n@@ -50,1 +60,1 @@\n b\n'
    const hunks = parseUnifiedDiff(diff)
    expect(hunks).toHaveLength(2)
    expect(hunks[0].rows[0]).toEqual({ type: 'context', left: 'a', right: 'a', leftNo: 1, rightNo: 1 })
    expect(hunks[1].rows[0]).toEqual({ type: 'context', left: 'b', right: 'b', leftNo: 50, rightNo: 60 })
  })
})
