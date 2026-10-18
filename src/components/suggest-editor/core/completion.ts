import { Extension, type Editor } from "@tiptap/core"
import { Plugin, PluginKey, TextSelection, type EditorState } from "@tiptap/pm/state"
import { Decoration, DecorationSet } from "@tiptap/pm/view"
import * as Y from "yjs"
import { requestCompletion } from "./commands"
import {
  DocumentVocabulary,
  gatherCandidates,
  minPrefixLength,
  readVocabulary,
  shouldTrigger,
  tokenPrefix,
  type CompletionCandidate,
} from "./vocabulary"

/** 触发 decoration 重算（列表/幽灵文本变化时用） */
export const GHOST_REFRESH_META = "suggest-editor:ghost"

/** LLM 兜底层：只在本地层完全没命中时才请求 */
const LLM_DEBOUNCE_MS = 700
/** 文档自学习索引的重建防抖（避免每个按键都重建索引） */
const LEARN_DEBOUNCE_MS = 300
const MIN_CONTEXT_CHARS = 4
const MAX_TAIL_CHARS = 40
const MAX_LIST_ITEMS = 20

interface ListState {
  pos: number
  prefix: string
  candidates: CompletionCandidate[]
  selected: number
}

interface GhostState {
  id: string
  pos: number
  insert: string
  replaceLength: number
  source: CompletionCandidate["source"]
  /** 片段占位符（相对插入文本的偏移）：接受后可用 Tab 逐个跳转 */
  stops?: Array<{ start: number; end: number }>
}

interface TabstopState {
  ranges: Array<{ from: number; to: number }>
  index: number
}

/** VS Code 风格的行首图标（内联 SVG，静态字符串） */
const SOURCE_ICON: Record<"词汇" | "文档", string> = {
  词汇:
    '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3">' +
    '<path d="M3.5 3.2c0-.7.6-1.2 1.2-1.2H12c.6 0 1 .4 1 1v9.8H5c-.9 0-1.5.5-1.5 1.2z"/>' +
    '<path d="M5 2v10.5"/></svg>',
  文档:
    '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3">' +
    '<path d="M4 2.2h5.2L13 5.9V14H4z"/><path d="M9.2 2.2v3.7H13"/></svg>',
}

/** 联想补全总开关：VITE_LLM_COMPLETION=off 时只用词表/文档层 */
const LLM_COMPLETION_ENABLED = (import.meta.env.VITE_LLM_COMPLETION ?? "on") !== "off"

/**
 * VS Code 风格的词表补全 + 续写联想：
 * - **候选列表**：按光标前一个「词」的前缀实时（同步、无防抖）过滤，支持前缀与模糊（子序列）匹配，
 *   命中字符高亮；列表左侧是来源 chip，右侧是详情，底部是快捷键提示，右侧浮动详情面板；
 *   ↑↓ 选择、Tab/Enter 接受、Esc 关闭、点击某行接受、**Ctrl/Cmd+Space 显示全部**；
 * - **幽灵文本**：把当前选中候选的「剩余部分」灰色预览在光标后（Tab 一步完成）；
 * - **降级顺序**：词表 → 文档词 → 文档续写（n-gram）→ LLM 兜底；本地命中时不调用 LLM。
 */
export function CompletionGhost(options: { ydoc: Y.Doc; getEditor: () => Editor | null }) {
  const { ydoc, getEditor } = options
  const commands = ydoc.getMap("commands")
  const pluginKey = new PluginKey("completionGhost")
  const docVocabulary = new DocumentVocabulary()

  let list: ListState | null = null
  let ghost: GhostState | null = null
  let pendingId: string | null = null
  let docVersion = 0
  let versionAtRequest = 0
  let learnedVersion = -1
  let learnScheduled = false
  let dismissedAtVersion = -1
  let llmTimer: ReturnType<typeof setTimeout> | null = null
  /** 片段占位符跳转状态（接受带 ${1:xx} 的词条后可用 Tab 逐个跳转） */
  let tabstops: TabstopState | null = null

  const refresh = () => {
    const view = getEditor()?.view
    if (view) view.dispatch(view.state.tr.setMeta(GHOST_REFRESH_META, Date.now()))
  }

  /** 光标前文（取词前缀 / 续写上下文共用） */
  const tailAt = (state: EditorState): { tail: string; pos: number } | null => {
    const selection = state.selection
    if (!selection.empty) return null
    const $from = selection.$from
    if (!$from.parent.isTextblock) return null
    const start = Math.max(0, $from.parentOffset - MAX_TAIL_CHARS)
    return { tail: $from.parent.textBetween(start, $from.parentOffset, "\n"), pos: selection.from }
  }

  const atParagraphEnd = (state: EditorState): boolean => {
    const selection = state.selection
    if (!selection.empty) return false
    const $from = selection.$from
    return (
      $from.parent.isTextblock &&
      $from.parentOffset === $from.parent.content.size &&
      $from.parentOffset >= MIN_CONTEXT_CHARS
    )
  }

  const ghostFromCandidate = (candidate: CompletionCandidate, pos: number): GhostState => ({
    id: `candidate:${candidate.matchKind}:${candidate.label}`,
    pos,
    insert: candidate.insert,
    replaceLength: candidate.replaceLength,
    source: candidate.source,
    stops: candidate.stops,
  })

  /** 同步重建候选列表与幽灵文本（实时过滤；不做防抖） */
  const rebuild = (state: EditorState, resetSelection = false, forced = false): void => {
    if (tabstops) {
      // 片段占位符编辑中：不弹候选列表（对齐 VS Code 的片段行为）
      list = null
      ghost = null
      return
    }
    if (!forced && dismissedAtVersion === docVersion) {
      list = null
      ghost = null
      return
    }

    const context = tailAt(state)
    if (context) {
      const run = tokenPrefix(context.tail)
      const chars = [...run]
      const minLength = minPrefixLength(run)
      const entries = readVocabulary(ydoc)
      let candidates: CompletionCandidate[] = []

      if (forced && chars.length < minLength) {
        // Ctrl+Space：不依赖前缀，直接显示全部候选
        candidates = gatherCandidates("", entries, docVocabulary, MAX_LIST_ITEMS)
      } else if (shouldTrigger(run)) {
        // 中文没有空格：光标前的整段汉字都算一个「词」，所以从最长后缀往下试。
        // 先只找「连续前缀命中」（更具体），全都没有才放开模糊/拼音首字母匹配。
        for (const mode of ["prefix", "extended"] as const) {
          for (let length = chars.length; length >= minLength; length -= 1) {
            const prefix = chars.slice(-length).join("")
            const found = gatherCandidates(prefix, entries, docVocabulary, MAX_LIST_ITEMS, { mode })
            if (found.length > 0) {
              candidates = found
              break
            }
          }
          if (candidates.length > 0) break
        }
      }

      if (candidates.length > 0) {
        const previous = !resetSelection && list ? list.candidates[list.selected]?.label : undefined
        const keep = previous ? candidates.findIndex((candidate) => candidate.label === previous) : -1
        const selected = keep >= 0 ? keep : 0
        list = { pos: context.pos, prefix: run, candidates, selected }
        ghost = ghostFromCandidate(candidates[selected], context.pos)
        return
      }
    }

    list = null

    // 本地第 3 层：文档续写（仅在段尾、且没有候选词时）
    if (context && atParagraphEnd(state)) {
      const continuation = docVocabulary.complete(context.tail)
      if (continuation) {
        ghost = {
          id: `doc:${continuation.insert}`,
          pos: context.pos,
          insert: continuation.insert,
          replaceLength: 0,
          source: "文档",
        }
        return
      }
    }

    ghost = null
  }

  /** LLM 兜底：列表为空且光标在段尾时才请求 */
  const requestLlm = (): void => {
    if (!LLM_COMPLETION_ENABLED || list || ghost) return
    const editor = getEditor()
    if (!editor) return
    const state = editor.state
    if (!atParagraphEnd(state)) return
    const context = tailAt(state)
    if (!context || context.tail.trim().length < MIN_CONTEXT_CHARS) return
    pendingId = requestCompletion(ydoc, ydoc.clientID, context.tail.slice(-300))
    versionAtRequest = docVersion
  }

  /** 文档自学习索引：防抖重建，重建后刷新一次（让文档词进入候选） */
  const scheduleLearn = (state: EditorState): void => {
    if (learnScheduled || learnedVersion === docVersion) return
    learnScheduled = true
    setTimeout(() => {
      learnScheduled = false
      const editor = getEditor()
      const text =
        editor?.state.doc.textBetween(0, editor.state.doc.content.size, "\n") ??
        state.doc.textBetween(0, state.doc.content.size, "\n")
      docVocabulary.learn(text)
      learnedVersion = docVersion
      const current = getEditor()?.state
      if (current) {
        rebuild(current, true)
        refresh()
      }
    }, LEARN_DEBOUNCE_MS)
  }

  const closeList = (): void => {
    list = null
    ghost = null
    refresh()
  }

  const accept = (target: GhostState): void => {
    const editor = getEditor()
    if (!editor) return
    const insertAt = target.replaceLength > 0 ? target.pos - target.replaceLength : target.pos
    const tr =
      target.replaceLength > 0
        ? editor.state.tr.delete(insertAt, target.pos).insertText(target.insert, insertAt)
        : editor.state.tr.insertText(target.insert, target.pos)

    const stops = target.stops
    if (stops && stops.length > 0) {
      const first = stops[0]
      tr.setSelection(TextSelection.create(tr.doc, insertAt + first.start, insertAt + first.end))
    }
    editor.view.dispatch(tr.scrollIntoView())

    if (stops && stops.length > 0) {
      // 事务之后再记录占位符：避免被 apply 里的位移映射二次处理（坐标已是插入后的）
      tabstops = {
        ranges: stops.map((stop) => ({ from: insertAt + stop.start, to: insertAt + stop.end })),
        index: 0,
      }
    }
    closeList()
  }

  // 服务端同步/热更新词表后，让已打开的列表立即刷新（不必等下一次编辑）
  ydoc.getMap("vocabulary").observe(() => {
    const editor = getEditor()
    if (!editor) return
    rebuild(editor.state, false)
    refresh()
  })

  // 服务端把 LLM 续写结果写回命令；只认自己发起的、且期间没有新编辑的结果
  commands.observe((event) => {
    for (const key of event.keysChanged) {
      const value = commands.get(key) as
        | { kind?: unknown; clientId?: unknown; status?: unknown; replacement?: unknown }
        | undefined
      if (!value || value.kind !== "complete") continue
      if (value.clientId !== ydoc.clientID || key !== pendingId) continue

      if (value.status === "superseded" || value.status === "error") {
        pendingId = null
        continue
      }
      if (value.status !== "done" || typeof value.replacement !== "string" || !value.replacement) continue

      pendingId = null
      if (docVersion !== versionAtRequest) continue // 期间又编辑过 → 结果已过期
      if (list || ghost) continue // 本地层已经有更好的候选
      const editor = getEditor()
      if (!editor) continue
      const selection = editor.state.selection
      if (!selection.empty) continue

      ghost = { id: key, pos: selection.from, insert: value.replacement, replaceLength: 0, source: "AI" }
      refresh()
    }
  })

  /** 高亮：把命中的字符包进 <b>（下标与 fuzzyScore 的 UTF-16 一致） */
  const appendHighlightedLabel = (container: HTMLElement, label: string, positions: number[]): void => {
    if (positions.length === 0) {
      container.textContent = label
      return
    }
    const matched = new Set(positions)
    let buffer = ""
    let bufferMatched = false

    const flush = () => {
      if (!buffer) return
      if (bufferMatched) {
        const bold = document.createElement("b")
        bold.className = "completion-match"
        bold.textContent = buffer
        container.append(bold)
      } else {
        container.append(document.createTextNode(buffer))
      }
      buffer = ""
    }

    for (let index = 0; index < label.length; index += 1) {
      const isMatched = matched.has(index)
      if (buffer && isMatched !== bufferMatched) flush()
      bufferMatched = isMatched
      buffer += label[index]
    }
    flush()
  }

  const renderPopup = (editor: Editor, pane: ListState, current: GhostState): HTMLElement => {
    // 类名带 se- 前缀防全局冲突（弹层是 fixed 定位，视觉上脱离文档流）
    const popup = document.createElement("div")
    popup.className = "se-completion-popup"

    const panel = document.createElement("div")
    panel.className = "completion-list"
    panel.setAttribute("role", "listbox")

    const selectedCandidate = pane.candidates[pane.selected]

    pane.candidates.forEach((candidate, index) => {
      const row = document.createElement("div")
      row.className = `completion-item${index === pane.selected ? " is-selected" : ""}`
      row.setAttribute("role", "option")

      // VS Code 风格行首图标（词表 / 文档），鼠标悬停显示来源名
      const icon = document.createElement("span")
      icon.className = `completion-icon completion-icon-${candidate.source === "词表" ? "vocab" : "doc"}`
      icon.title = candidate.source
      icon.innerHTML = candidate.source === "词表" ? SOURCE_ICON.词汇 : SOURCE_ICON.文档
      row.append(icon)

      const itemLabel = document.createElement("span")
      itemLabel.className = "completion-label"
      appendHighlightedLabel(itemLabel, candidate.label, candidate.positions)
      row.append(itemLabel)

      if (candidate.stops && candidate.stops.length > 0) {
        const badge = document.createElement("span")
        badge.className = "completion-badge"
        badge.textContent = "片段"
        badge.title = `含 ${candidate.stops.length} 个占位符，接受后 Tab 逐个跳转`
        row.append(badge)
      }

      if (candidate.detail) {
        const detail = document.createElement("span")
        detail.className = "completion-detail"
        detail.textContent = candidate.detail
        row.append(detail)
      }

      row.addEventListener("mousedown", (event) => {
        event.preventDefault()
        event.stopPropagation()
        pane.selected = index
        accept(ghostFromCandidate(candidate, pane.pos))
      })
      row.addEventListener("mouseenter", () => {
        if (pane.selected === index) return
        pane.selected = index
        ghost = ghostFromCandidate(candidate, pane.pos)
        refresh()
      })

      panel.append(row)
    })

    const footer = document.createElement("div")
    footer.className = "completion-footer"
    footer.textContent = "↑↓ 选择 · Tab / Enter 接受 · Esc 关闭 · Ctrl+Space 显示全部"
    panel.append(footer)

    popup.append(panel)

    // 右侧详情面板（VS Code 的 docs 面板）
    if (selectedCandidate) {
      const doc = document.createElement("div")
      doc.className = "completion-doc"

      const title = document.createElement("div")
      title.className = "completion-doc-title"
      appendHighlightedLabel(title, selectedCandidate.label, selectedCandidate.positions)
      doc.append(title)

      const rows: Array<[string, string]> = [
        ["来源", selectedCandidate.source === "词表" ? "词表（词表.txt / 内置）" : "文档自学习"],
        [
          "匹配",
          selectedCandidate.matchedText
            ? `${selectedCandidate.matchKind}「${selectedCandidate.matchedText}」`
            : selectedCandidate.matchKind,
        ],
        ["长度", `${[...selectedCandidate.label].length} 字`],
      ]
      if (selectedCandidate.frequency !== undefined) rows.push(["出现", `${selectedCandidate.frequency} 次`])
      if (selectedCandidate.stops && selectedCandidate.stops.length > 0) {
        rows.push(["占位符", `${selectedCandidate.stops.length} 处（Tab 跳转）`])
      }
      if (selectedCandidate.aliases && selectedCandidate.aliases.length > 0) {
        rows.push(["别名", selectedCandidate.aliases.join(" / ")])
      }
      for (const [key, value] of rows) {
        const line = document.createElement("div")
        line.className = "completion-doc-row"
        const keySpan = document.createElement("span")
        keySpan.className = "completion-doc-key"
        keySpan.textContent = key
        const valueSpan = document.createElement("span")
        valueSpan.textContent = value
        line.append(keySpan, valueSpan)
        doc.append(line)
      }
      popup.append(doc)
    }

    // 定位到光标下方（fixed 定位，不参与排版流）
    try {
      const coords = editor.view.coordsAtPos(pane.pos)
      popup.style.left = `${Math.round(coords.left)}px`
      popup.style.top = `${Math.round(coords.bottom + 4)}px`
    } catch {
      /* ignore */
    }

    void current
    return popup
  }

  return Extension.create({
    name: "completionGhost",
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: pluginKey,
          view: () => {
            // 滚动时重新定位弹层（capture 阶段能捕获内层容器滚动）
            const onScroll = () => {
              if (list || ghost) refresh()
            }
            window.addEventListener("scroll", onScroll, true)
            return {
              destroy: () => window.removeEventListener("scroll", onScroll, true),
            }
          },
          state: {
            init: () => null,
            apply: (tr, value, _oldState, newState) => {
              if (tr.docChanged) docVersion += 1

              if (tabstops && tr.docChanged) {
                tabstops.ranges = tabstops.ranges.map((range) => ({
                  from: tr.mapping.map(range.from),
                  to: tr.mapping.map(range.to),
                }))
              }
              if (tabstops && (tr.docChanged || tr.selectionSet)) {
                // 光标离开所有占位符 → 结束片段会话
                const selection = newState.selection
                const inside = tabstops.ranges.some(
                  (range) => selection.from >= range.from - 1 && selection.to <= range.to + 1,
                )
                if (!inside) tabstops = null
              }

              if (tr.docChanged || tr.selectionSet) {
                rebuild(newState, tr.docChanged)
                scheduleLearn(newState)
              }
              if (tr.docChanged) {
                // LLM 兜底：本地层没命中时，停笔一小会才请求
                if (llmTimer) clearTimeout(llmTimer)
                llmTimer = setTimeout(() => {
                  llmTimer = null
                  requestLlm()
                }, LLM_DEBOUNCE_MS)
              }
              return value
            },
          },
          props: {
            decorations(state) {
              const current = ghost
              if (!current) return null
              // 只在「光标仍停在原位且没有选区」时显示
              if (!state.selection.empty || state.selection.from !== current.pos) return null

              const editor = getEditor()
              const pane = list
              const widget = Decoration.widget(
                current.pos,
                () => {
                  const wrapper = document.createElement("span")
                  wrapper.className = "completion-widget"
                  wrapper.contentEditable = "false"

                  const text = document.createElement("span")
                  text.className = "ghost-text"
                  text.textContent = current.insert
                  wrapper.append(text)

                  const label = document.createElement("span")
                  label.className = "ghost-source"
                  label.textContent = current.source
                  wrapper.append(label)

                  if (pane && pane.candidates.length > 0 && editor) {
                    wrapper.append(renderPopup(editor, pane, current))
                  }
                  return wrapper
                },
                { side: 1, key: `completion:${current.id}:${pane?.candidates.length ?? 0}` },
              )
              return DecorationSet.create(state.doc, [widget])
            },
            handleKeyDown(_view, event) {
              // Ctrl/Cmd + Space：显示全部候选（即使被 Esc 关过或前缀很短）
              if ((event.ctrlKey || event.metaKey) && (event.code === "Space" || event.key === " ")) {
                const editor = getEditor()
                if (!editor) return false
                dismissedAtVersion = -1
                rebuild(editor.state, true, true)
                refresh()
                return true
              }

              if (list && list.candidates.length > 0) {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  const delta = event.key === "ArrowDown" ? 1 : -1
                  list.selected = (list.selected + delta + list.candidates.length) % list.candidates.length
                  ghost = ghostFromCandidate(list.candidates[list.selected], list.pos)
                  refresh()
                  return true
                }
                if (event.key === "Tab" || event.key === "Enter") {
                  if (!ghost) return false
                  accept(ghost)
                  return true
                }
                if (event.key === "Escape") {
                  dismissedAtVersion = docVersion
                  closeList()
                  return true
                }
                return false
              }

              // 片段占位符跳转（列表已关闭时）：Tab / Shift+Tab 在 ${1:xx} 之间移动
              if (tabstops && !list) {
                if (event.key === "Tab") {
                  const editor = getEditor()
                  if (!editor) return false
                  const direction = event.shiftKey ? -1 : 1
                  const next = tabstops.index + direction
                  if (next < 0) return true
                  if (next >= tabstops.ranges.length) {
                    const last = tabstops.ranges[tabstops.ranges.length - 1]
                    tabstops = null
                    editor.view.dispatch(
                      editor.state.tr.setSelection(TextSelection.create(editor.state.doc, last.to)).scrollIntoView(),
                    )
                    return true
                  }
                  tabstops.index = next
                  const range = tabstops.ranges[next]
                  editor.view.dispatch(
                    editor.state.tr
                      .setSelection(TextSelection.create(editor.state.doc, range.from, range.to))
                      .scrollIntoView(),
                  )
                  return true
                }
                if (event.key === "Escape") {
                  tabstops = null
                  return true
                }
              }

              if (ghost) {
                if (event.key === "Tab") {
                  accept(ghost)
                  return true
                }
                if (event.key === "Escape") {
                  ghost = null
                  refresh()
                  return true
                }
              }
              return false
            },
          },
        }),
      ]
    },
  })
}
