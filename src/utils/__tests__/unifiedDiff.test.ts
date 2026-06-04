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
})
