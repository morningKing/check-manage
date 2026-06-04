export type DiffRowType = 'context' | 'add' | 'del' | 'change'

export interface DiffRow {
  type: DiffRowType
  left: string | null
  right: string | null
  leftNo: number | null
  rightNo: number | null
}

export interface DiffHunk {
  header: string
  rows: DiffRow[]
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/**
 * Parse git unified diff text into side-by-side hunks. Removal (`-`) and
 * addition (`+`) runs are paired row-by-row; the shorter side gets blank
 * fillers. Context lines appear on both sides.
 */
export function parseUnifiedDiff(diff: string): DiffHunk[] {
  if (!diff.trim()) return []
  const lines = diff.split('\n')
  const hunks: DiffHunk[] = []
  let cur: DiffHunk | null = null
  let leftNo = 0
  let rightNo = 0
  let dels: string[] = []
  let adds: string[] = []

  const flush = () => {
    if (!cur) return
    const n = Math.max(dels.length, adds.length)
    for (let i = 0; i < n; i++) {
      const l = i < dels.length ? dels[i] : null
      const r = i < adds.length ? adds[i] : null
      if (l !== null && r !== null) {
        cur.rows.push({ type: 'change', left: l, right: r, leftNo: ++leftNo, rightNo: ++rightNo })
      } else if (l !== null) {
        cur.rows.push({ type: 'del', left: l, right: null, leftNo: ++leftNo, rightNo: null })
      } else if (r !== null) {
        cur.rows.push({ type: 'add', left: null, right: r, leftNo: null, rightNo: ++rightNo })
      }
    }
    dels = []
    adds = []
  }

  for (const line of lines) {
    const m = HUNK_RE.exec(line)
    if (m) {
      flush()
      cur = { header: line, rows: [] }
      hunks.push(cur)
      leftNo = parseInt(m[1], 10) - 1
      rightNo = parseInt(m[2], 10) - 1
      continue
    }
    if (!cur) continue // skip the diff/index/--- /+++ preamble
    if (line === '') continue // trailing split artifact (diff ends with \n); a real blank context line is ' '
    if (line.startsWith('\\')) continue // "\ No newline at end of file"
    if (line.startsWith('-')) { dels.push(line.slice(1)); continue }
    if (line.startsWith('+')) { adds.push(line.slice(1)); continue }
    // context (leading space) or blank line inside a hunk
    flush()
    const text = line.startsWith(' ') ? line.slice(1) : line
    cur.rows.push({ type: 'context', left: text, right: text, leftNo: ++leftNo, rightNo: ++rightNo })
  }
  flush()
  return hunks
}
