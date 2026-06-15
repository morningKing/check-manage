/**
 * CodeMirror 6 断点行号槽：点击行号槽切换断点；并能读出/高亮命中行。
 * 用于导出脚本的「追踪式断点调试」。
 */
import { StateField, StateEffect, RangeSet, type Extension } from '@codemirror/state'
import { gutter, GutterMarker, EditorView, Decoration, type DecorationSet } from '@codemirror/view'

// ---- 断点（行起始位置）状态 ----
const breakpointEffect = StateEffect.define<{ pos: number; on: boolean }>({
  map: (val, change) => ({ pos: change.mapPos(val.pos), on: val.on }),
})

class BreakpointMarker extends GutterMarker {
  toDOM() {
    const el = document.createElement('span')
    el.textContent = '●'
    el.className = 'cm-breakpoint-dot'
    return el
  }
}
const breakpointMarker = new BreakpointMarker()

const breakpointState = StateField.define<RangeSet<GutterMarker>>({
  create() {
    return RangeSet.empty
  },
  update(set, tr) {
    set = set.map(tr.changes)
    for (const e of tr.effects) {
      if (e.is(breakpointEffect)) {
        if (e.value.on) set = set.update({ add: [breakpointMarker.range(e.value.pos)] })
        else set = set.update({ filter: (from) => from !== e.value.pos })
      }
    }
    return set
  },
})

function hasBreakpoint(view: EditorView, pos: number): boolean {
  let found = false
  const set = view.state.field(breakpointState, false)
  if (!set) return false
  set.between(pos, pos, () => {
    found = true
  })
  return found
}

function toggleBreakpoint(view: EditorView, pos: number) {
  const line = view.state.doc.lineAt(pos)
  view.dispatch({ effects: breakpointEffect.of({ pos: line.from, on: !hasBreakpoint(view, line.from) }) })
}

// ---- 命中行高亮（执行轨迹）----
const hitLineDeco = Decoration.line({ class: 'cm-hit-line' })
const hitEffect = StateEffect.define<number[]>()
const hitLinesState = StateField.define<DecorationSet>({
  create() {
    return Decoration.none
  },
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(hitEffect)) {
        const ranges = e.value
          .filter((ln) => ln >= 1 && ln <= tr.state.doc.lines)
          .sort((a, b) => a - b)
          .map((ln) => hitLineDeco.range(tr.state.doc.line(ln).from))
        return Decoration.set(ranges, true)
      }
    }
    return value.map(tr.changes)
  },
  provide: (f) => EditorView.decorations.from(f),
})

export const breakpointGutter: Extension = [
  breakpointState,
  hitLinesState,
  gutter({
    class: 'cm-breakpoint-gutter',
    markers: (v) => v.state.field(breakpointState),
    initialSpacer: () => breakpointMarker,
    domEventHandlers: {
      mousedown(view, line) {
        toggleBreakpoint(view, line.from)
        return true
      },
    },
  }),
  EditorView.baseTheme({
    '.cm-breakpoint-gutter': { width: '1.4em', cursor: 'pointer' },
    '.cm-breakpoint-gutter .cm-gutterElement': { display: 'flex', justifyContent: 'center', alignItems: 'center' },
    '.cm-breakpoint-dot': { color: '#f56c6c', fontSize: '12px' },
    '.cm-line.cm-hit-line': { background: 'rgba(103,194,58,0.15)' },
  }),
]

/** 读出当前所有断点的行号（1-based） */
export function getBreakpointLines(view: EditorView): number[] {
  const lines: number[] = []
  const set = view.state.field(breakpointState, false)
  if (!set) return lines
  const it = set.iter()
  while (it.value) {
    lines.push(view.state.doc.lineAt(it.from).number)
    it.next()
  }
  return lines.sort((a, b) => a - b)
}

/** 设置命中行高亮（执行轨迹中走过的行） */
export function setHitLines(view: EditorView, lines: number[]) {
  view.dispatch({ effects: hitEffect.of(lines) })
}
