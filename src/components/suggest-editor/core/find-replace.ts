import { Extension, type Editor } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import type { Node as PMNode } from '@tiptap/pm/model'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

export interface FindMatch {
  from: number
  to: number
}

/**
 * 查找全部字面命中（逐文本节点匹配，不跨块——跨块的「半句话」不该命中）。
 * 默认大小写不敏感：haystack/needle 都转小写后 indexOf。
 * 注：极端字符（如 İ）的小写变换会改变长度导致偏移误差，中文场景不受影响。
 */
export function findMatches(doc: PMNode, query: string, caseSensitive = false): FindMatch[] {
  const matches: FindMatch[] = []
  const needle = caseSensitive ? query : query.toLowerCase()
  if (!needle) return matches
  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return
    const haystack = caseSensitive ? node.text : node.text.toLowerCase()
    let idx = haystack.indexOf(needle)
    while (idx !== -1) {
      matches.push({ from: pos + idx, to: pos + idx + needle.length })
      idx = haystack.indexOf(needle, idx + needle.length)
    }
  })
  return matches
}

/** 命中范围必须落在同一文本块内（装饰过期时位置可能漂移，直接跳过避免破坏结构） */
function rangeInSameTextblock(doc: PMNode, from: number, to: number): boolean {
  try {
    return doc.resolve(from).sameParent(doc.resolve(to))
  } catch {
    return false
  }
}

/** 替换单处命中（单事务，一次撤销可还原）；范围失效返回 false */
export function replaceOne(editor: Editor, match: FindMatch, replacement: string): boolean {
  const { state, view } = editor
  if (!rangeInSameTextblock(state.doc, match.from, match.to)) return false
  view.dispatch(state.tr.insertText(replacement, match.from, match.to))
  return true
}

/** 全部替换（单事务 = 一次撤销整体还原；从后往前替换避免位置漂移）；返回实际替换处数 */
export function replaceAll(editor: Editor, matches: FindMatch[], replacement: string): number {
  const tr = editor.state.tr
  let applied = 0
  for (const match of [...matches].sort((a, b) => b.from - a.from)) {
    if (!rangeInSameTextblock(tr.doc, match.from, match.to)) continue
    tr.insertText(replacement, match.from, match.to)
    applied += 1
  }
  if (applied > 0) editor.view.dispatch(tr)
  return applied
}

/** 查找高亮的 meta 类型；面板组件推送命中集合，插件只负责画 decoration */
export const FIND_REPLACE_META = 'se-find-replace'

export interface FindHighlightPayload {
  matches: FindMatch[]
  /** 当前命中的下标（无命中时 -1） */
  index: number
}

export function FindReplaceHighlight() {
  return Extension.create({
    name: 'seFindReplaceHighlight',
    addProseMirrorPlugins() {
      const key = new PluginKey<DecorationSet>('seFindReplaceHighlight')
      return [
        new Plugin<DecorationSet>({
          key,
          state: {
            init: () => DecorationSet.empty,
            apply(tr, value) {
              const meta = tr.getMeta(FIND_REPLACE_META) as FindHighlightPayload | undefined
              if (meta) {
                if (!meta.matches.length) return DecorationSet.empty
                return DecorationSet.create(
                  tr.doc,
                  meta.matches.map((match, i) =>
                    Decoration.inline(match.from, match.to, {
                      // 全部命中都挂 se-find-hit（计数口径 = matches.length），当前命中叠加深色
                      class: i === meta.index ? 'se-find-hit se-find-current' : 'se-find-hit',
                    }),
                  ),
                )
              }
              return tr.docChanged ? value.map(tr.mapping, tr.doc) : value
            },
          },
          props: {
            decorations(state) {
              return key.getState(state)
            },
          },
        }),
      ]
    },
  })
}

/** 把文档某位置滚进可视区（.se-editor-host 是滚动容器；sticky 头部由 scroll-margin 让位） */
export function scrollDocPosIntoView(editor: Editor, pos: number): void {
  let target: Element | null = null
  try {
    const domAt = editor.view.domAtPos(pos)
    if (domAt.node.nodeType === Node.ELEMENT_NODE) {
      target = ((domAt.node as Element).childNodes[domAt.offset] as Element | undefined) ?? null
    }
    target = target ?? domAt.node.parentElement
  } catch {
    return
  }
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
