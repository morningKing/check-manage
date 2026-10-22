import { Extension, type Editor } from "@tiptap/core"
import { Plugin, PluginKey, type EditorState } from "@tiptap/pm/state"
import { Decoration, DecorationSet } from "@tiptap/pm/view"
import * as Y from "yjs"
import { relativePositionToAbsolutePosition, ySyncPluginKey } from "@tiptap/y-tiptap"
import { runLocalRules, type Severity } from "./local-rules"
import { EMPTY_COMPILED_RULES, runCheckRules, type CompiledRules } from "./check-rules"

/** 刷新 decoration 的事务 meta key */
export const SUGGESTIONS_REFRESH_META = "suggest-editor:refresh"

export interface SuggestionItem {
  id: string
  source: "ai" | "rewrite" | "local"
  quote: string
  replacement: string
  reason: string
  severity: Severity
  /** 正文中的位置（用于跳转与排序） */
  from: number
  to: number
  /** AI / 改写建议的序号（与正文标记一致）；本地即时检查为 null */
  index: number | null
  /** 命中的检查规则名（仅动态规则命中） */
  ruleName?: string
  /** 命中的检查规则 id（仅动态规则命中，用于忽略记忆键） */
  ruleId?: string
  /** 命中机制（内置格式规则 / 规则四类 / AI），供 UI data 属性与测试定位 */
  mechanism?: 'builtin' | 'maps' | 'patterns' | 'whitelists' | 'presence' | 'ai'
  /** 完整性提醒：不可接受，只能忽略 */
  notice?: boolean
}

export interface SuggestionLayersApi {
  accept: (item: SuggestionItem) => void
  ignore: (item: SuggestionItem) => void
  focus: (item: SuggestionItem) => void
  setActive: (id: string | null) => void
}

interface StoredSuggestion {
  kind?: unknown
  quote?: unknown
  replacement?: unknown
  reason?: unknown
  severity?: unknown
  relFrom?: unknown
  relTo?: unknown
}

/**
 * 建议渲染层（Word 式批注）：
 * - 正文里只保留**标注线高亮**（inline decoration）与**行内编号标记**（小圆点/数字）；
 * - 建议卡片交给右侧边栏（Vue）渲染，通过 onChange 同步，点击标记与卡片互相联动；
 * - 三层来源：AI 分析、用户改写（都带序号）、本地即时检查（点状标记，无序号）。
 * 建议永不写正文：接受才产生一次普通编辑事务。
 */
export function createSuggestionLayers(options: {
  ydoc: Y.Doc
  getEditor: () => Editor | null
  onChange: (items: { ai: SuggestionItem[]; local: SuggestionItem[] }) => void
  onActiveChange?: (id: string | null) => void
  getCheckRules?: () => CompiledRules | null
}): { extension: Extension; api: SuggestionLayersApi } {
  const { ydoc, getEditor, onChange, onActiveChange, getCheckRules } = options
  const suggestions = ydoc.getMap("suggestions")
  const aiRanges = new Map<string, { from: number; to: number }>()
  const localRanges = new Map<string, { from: number; to: number }>()
  const dismissedLocal = new Set<string>()
  const pluginKey = new PluginKey<DecorationSet>("suggestionLayers")

  let activeId: string | null = null
  let lastSignature = ""

  const refresh = () => {
    const view = getEditor()?.view
    if (view) view.dispatch(view.state.tr.setMeta(SUGGESTIONS_REFRESH_META, Date.now()))
  }

  const replaceRange = (range: { from: number; to: number }, replacement: string): void => {
    const editor = getEditor()
    if (!editor) return
    let chain = editor.chain().focus().deleteRange(range)
    if (replacement) chain = chain.insertContentAt(range.from, replacement)
    chain.run()
  }

  const api: SuggestionLayersApi = {
    accept: (item) => {
      if (item.notice) return // 完整性提醒不可接受，只能忽略
      if (item.source === "local") {
        const range = localRanges.get(item.id)
        if (!range) return
        replaceRange(range, item.replacement) // 文本一变，规则自然不再命中
        return
      }
      const range = aiRanges.get(item.id)
      if (!range) return
      replaceRange(range, item.replacement)
      suggestions.delete(item.id)
    },
    ignore: (item) => {
      if (item.source === "local") {
        dismissedLocal.add(`${item.ruleId ?? item.id}\u0000${item.quote}`)
        localRanges.delete(item.id)
        refresh()
        return
      }
      suggestions.delete(item.id)
    },
    focus: (item) => {
      const editor = getEditor()
      if (!editor) return
      editor.chain().focus().setTextSelection({ from: item.from, to: item.to }).scrollIntoView().run()
    },
    setActive: (id) => {
      if (activeId === id) return
      activeId = id
      refresh()
    },
  }

  /** 行内标记：AI/改写是编号圆标，本地检查是点状标 */
  const markerFor = (item: SuggestionItem): HTMLElement => {
    const marker = document.createElement("span")
    marker.className = [
      "suggest-marker",
      item.index !== null ? "suggest-marker-numbered" : "suggest-marker-local",
      activeId === item.id ? "is-active" : "",
    ]
      .filter(Boolean)
      .join(" ")
    marker.contentEditable = "false"
    marker.textContent = item.index !== null ? String(item.index) : ""
    marker.title = item.reason || "建议"
    marker.addEventListener("mousedown", (event) => {
      event.preventDefault()
      event.stopPropagation()
    })
    marker.addEventListener("click", (event) => {
      event.preventDefault()
      activeId = item.id
      onActiveChange?.(item.id)
      api.focus(item)
      refresh()
    })
    return marker
  }

  const build = (state: EditorState): DecorationSet => {
    aiRanges.clear()
    localRanges.clear()
    const decorations: Decoration[] = []

    const aiItems: SuggestionItem[] = []
    const localItems: SuggestionItem[] = []

    // 1) 服务端写入的建议（AI 分析 / 用户改写，带相对位置锚）
    const sync = ySyncPluginKey.getState(state)
    if (sync?.binding) {
      suggestions.forEach((raw, id) => {
        const stored = raw as StoredSuggestion
        if (!(stored.relFrom instanceof Uint8Array) || !(stored.relTo instanceof Uint8Array)) return

        let from: number | null
        let to: number | null
        try {
          from = relativePositionToAbsolutePosition(
            ydoc,
            sync.type,
            Y.decodeRelativePosition(stored.relFrom),
            sync.binding.mapping,
          )
          to = relativePositionToAbsolutePosition(
            ydoc,
            sync.type,
            Y.decodeRelativePosition(stored.relTo),
            sync.binding.mapping,
          )
        } catch {
          return
        }
        if (from === null || to === null || to <= from) return // 锚点已死

        const severity: Severity =
          stored.severity === "error" || stored.severity === "warn" ? stored.severity : "info"
        aiItems.push({
          id,
          source: stored.kind === "rewrite" ? "rewrite" : "ai",
          quote: String(stored.quote ?? ""),
          replacement: String(stored.replacement ?? ""),
          reason: String(stored.reason ?? ""),
          severity,
          from,
          to,
          index: null,
          mechanism: "ai",
        })
      })
    }

    // 2) 本地即时检查（毫秒级规则）：与已存在的建议范围重叠时让位
    for (const match of runLocalRules(state.doc)) {
      if (dismissedLocal.has(`local:${match.id}\u0000${match.text}`)) continue
      if (aiItems.some((item) => match.from < item.to && match.to > item.from)) continue
      if (localItems.some((item) => match.from < item.to && match.to > item.from)) continue
      localItems.push({
        id: `local:${match.id}`,
        source: "local",
        quote: match.text,
        replacement: match.replacement,
        reason: match.reason,
        severity: match.severity,
        from: match.from,
        to: match.to,
        index: null,
        ruleId: match.ruleId,
        mechanism: match.mechanism,
      })
    }

    // 2b) 服务端下发的检查规则（全局 + 文档级，毫秒级本地命中）：同样让位 AI、与本地去重
    const matches = runCheckRules(state.doc, getCheckRules?.() ?? EMPTY_COMPILED_RULES)
    for (const match of matches) {
      if (dismissedLocal.has(`${match.ruleId ?? match.id}\u0000${match.text}`)) continue
      if (aiItems.some((item) => match.from < item.to && match.to > item.from)) continue
      if (localItems.some((item) => match.from < item.to && match.to > item.from)) continue
      localItems.push({
        id: match.id,
        source: "local",
        quote: match.text,
        replacement: match.replacement ?? "",
        reason: match.reason,
        severity: match.severity,
        from: match.from,
        to: match.to,
        index: null,
        ruleName: match.ruleName,
        ruleId: match.ruleId,
        mechanism: match.mechanism,
        notice: match.notice,
      })
    }

    // 3) 按位置排序并编号（序号与正文标记一致）
    aiItems.sort((a, b) => a.from - b.from || a.id.localeCompare(b.id))
    aiItems.forEach((item, position) => {
      item.index = position + 1
    })
    localItems.sort((a, b) => a.from - b.from)

    for (const item of aiItems) {
      aiRanges.set(item.id, { from: item.from, to: item.to })
      decorations.push(
        Decoration.inline(item.from, item.to, {
          class: `suggest suggest-${item.source} suggest-${item.severity}${activeId === item.id ? " is-active" : ""}`,
        }),
      )
      decorations.push(
        Decoration.widget(item.to, () => markerFor(item), {
          side: 1,
          key: `marker:${item.id}:${item.index}:${activeId === item.id ? "a" : ""}`,
        }),
      )
    }

    for (const item of localItems) {
      localRanges.set(item.id, { from: item.from, to: item.to })
      decorations.push(
        Decoration.inline(item.from, item.to, {
          class: `suggest suggest-local suggest-${item.severity}${activeId === item.id ? " is-active" : ""}`,
        }),
      )
      decorations.push(
        Decoration.widget(item.to, () => markerFor(item), {
          side: 1,
          key: `marker:${item.id}:${activeId === item.id ? "a" : ""}`,
        }),
      )
    }

    // 4) 通知侧栏（内容有变化才回调，避免每个事务都 setState）
    // quote/reason/replacement/notice 也纳入签名：规则重载后即使位置未变，
    // 卡片也能拿到新的提示文案与替换值，避免展示/接受旧值。
    const signature = JSON.stringify([
      aiItems.map((item) => [
        item.id, item.index, item.from, item.to,
        item.quote, item.reason, item.replacement, item.notice,
      ]),
      localItems.map((item) => [
        item.id, item.from, item.to,
        item.quote, item.reason, item.replacement, item.notice,
      ]),
    ])
    if (signature !== lastSignature) {
      lastSignature = signature
      const snapshot = { ai: aiItems, local: localItems }
      queueMicrotask(() => onChange(snapshot))
    }

    return DecorationSet.create(state.doc, decorations)
  }

  const extension = Extension.create({
    name: "suggestionLayers",
    addProseMirrorPlugins() {
      return [
        new Plugin<DecorationSet>({
          key: pluginKey,
          state: {
            init: (_config, state) => build(state),
            apply: (tr, value, _oldState, newState) =>
              tr.docChanged || tr.getMeta(SUGGESTIONS_REFRESH_META) ? build(newState) : value,
          },
          props: {
            decorations: (state) => pluginKey.getState(state) ?? DecorationSet.empty,
          },
        }),
      ]
    },
  })

  return { extension, api }
}
