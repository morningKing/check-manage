import { Extension, type Editor, type Range } from "@tiptap/core"
import { Suggestion } from "@tiptap/suggestion"
import type { TextSnippet } from "@/api/suggestEditor"

interface SlashItem {
  title: string
  hint: string
  keywords: string
  badge: string
  /** 可配置文本模板（与内置块类型区分，便于样式与测试定位） */
  isSnippet?: boolean
  command: (props: { editor: Editor; range: Range }) => void
}

/** 空查询时一次展示的上限（块类型 + 文本模板合计） */
const MAX_ITEMS = 14

/** Notion 式斜杠菜单内置块类型 */
const BLOCK_ITEMS: SlashItem[] = [
  {
    title: "正文",
    hint: "普通段落",
    keywords: "text paragraph zhengwen",
    badge: "¶",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).setParagraph().run(),
  },
  {
    title: "一级标题",
    hint: "大标题",
    keywords: "h1 heading title yiji biaoti",
    badge: "H1",
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 1 }).run(),
  },
  {
    title: "二级标题",
    hint: "中标题",
    keywords: "h2 heading erji biaoti",
    badge: "H2",
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 2 }).run(),
  },
  {
    title: "三级标题",
    hint: "小标题",
    keywords: "h3 heading sanji biaoti",
    badge: "H3",
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 3 }).run(),
  },
  {
    title: "无序列表",
    hint: "项目符号列表",
    keywords: "bullet ul list wuxu liebiao",
    badge: "•",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    title: "有序列表",
    hint: "编号列表",
    keywords: "ordered ol number youxu liebiao",
    badge: "1.",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    title: "任务列表",
    hint: "待办勾选",
    keywords: "task todo checkbox renwu daiban",
    badge: "☑",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).toggleTaskList().run(),
  },
  {
    title: "引用",
    hint: "引用块",
    keywords: "quote blockquote yinyong",
    badge: "❝",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  {
    title: "代码块",
    hint: "等宽文本",
    keywords: "code codeblock daima",
    badge: "</>",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
  {
    title: "表格",
    hint: "3×3，带表头",
    keywords: "table biaoge",
    badge: "▦",
    command: ({ editor, range }) =>
      editor.chain().focus().deleteRange(range).insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
  },
  {
    title: "分割线",
    hint: "水平分隔",
    keywords: "hr divider line fengexian",
    badge: "—",
    command: ({ editor, range }) => editor.chain().focus().deleteRange(range).setHorizontalRule().run(),
  },
]

/** 插入后选中第一个 ___ 占位符，让用户直接输入覆盖，不用手动找位置 */
function selectFirstPlaceholder(editor: Editor, from: number): void {
  const { doc, selection } = editor.state
  let found: { from: number; to: number } | null = null
  doc.nodesBetween(from, selection.to, (node, pos) => {
    if (found || !node.isText) return true
    const index = (node.text ?? "").indexOf("___")
    if (index >= 0) found = { from: pos + index, to: pos + index + 3 }
    return true
  })
  if (found) editor.chain().focus().setTextSelection(found).run()
}

function snippetItems(snippets: TextSnippet[]): SlashItem[] {
  return snippets.map((snippet) => ({
    title: snippet.name,
    hint: snippet.description || "文本模板",
    keywords: snippet.keywords,
    badge: "T",
    isSnippet: true,
    command: ({ editor, range }) => {
      editor.chain().focus().deleteRange(range).insertContent(snippet.contentHtml).run()
      selectFirstPlaceholder(editor, range.from)
    },
  }))
}

function buildItems(query: string, snippets: TextSnippet[]): SlashItem[] {
  // 文本模板排在前面：写文档时更常用的是「填一段检查逻辑」而不是换块类型
  const items = [...snippetItems(snippets), ...BLOCK_ITEMS]
  const normalized = query.trim().toLowerCase()
  if (!normalized) return items.slice(0, MAX_ITEMS)
  return items
    .filter((item) => `${item.title}${item.hint}${item.keywords}`.toLowerCase().includes(normalized))
    .slice(0, MAX_ITEMS)
}

/**
 * 斜杠命令菜单（Notion 式）：输入 `/` 后在光标处弹出列表，
 * 内置块类型 + 后台可配置的文本模板；↑↓ 选择、Enter/Tab 执行、Esc 关闭。
 * 模板内容在每次过滤时从 getSnippets() 现取，因此管理面保存后
 * （经 BroadcastChannel 通知）无需重建编辑器即可生效。
 * 弹层挂在 document.body，根类名带 se- 前缀防全局样式冲突。
 */
export function createSlashMenu(getSnippets: () => TextSnippet[] = () => []): Extension {
  return Extension.create({
    name: "slashMenu",

    addProseMirrorPlugins() {
      return [
        Suggestion<SlashItem>({
          editor: this.editor,
          char: "/",
          allowSpaces: false,
          startOfLine: false,
          items: ({ query }) => buildItems(query, getSnippets()),
          command: ({ editor, range, props }) => props.command({ editor, range }),
          render: () => {
            let popup: HTMLElement | null = null
            let current: SlashItem[] = []
            let selected = 0

            const render = (rect?: DOMRect | null) => {
              const element = popup
              if (!element) return
              element.innerHTML = ""

              if (current.length === 0) {
                const empty = document.createElement("div")
                empty.className = "slash-empty"
                empty.textContent = "没有匹配的块类型或模板"
                element.append(empty)
              }

              current.forEach((item, index) => {
                const row = document.createElement("div")
                row.className = `slash-item${index === selected ? " is-selected" : ""}`
                row.setAttribute("role", "option")
                row.dataset.kind = item.isSnippet ? "snippet" : "block"
                if (item.isSnippet) row.classList.add("is-snippet")

                const badge = document.createElement("span")
                badge.className = "slash-badge"
                badge.textContent = item.badge
                row.append(badge)

                const title = document.createElement("span")
                title.className = "slash-title"
                title.textContent = item.title
                row.append(title)

                const hint = document.createElement("span")
                hint.className = "slash-hint"
                hint.textContent = item.hint
                row.append(hint)

                row.addEventListener("mousedown", (event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  selected = index
                  execute()
                })
                row.addEventListener("mouseenter", () => {
                  if (selected === index) return
                  selected = index
                  render(rect)
                })

                element.append(row)
              })

              if (rect) {
                element.style.left = `${Math.round(rect.left)}px`
                element.style.top = `${Math.round(rect.bottom + 6)}px`
              }
            }

            let executeRef: (() => void) | null = null
            const execute = () => {
              const item = current[selected]
              if (!item || !executeRef) return
              executeRef()
            }

            return {
              onStart: (props) => {
                current = props.items
                selected = 0
                popup = document.createElement("div")
                popup.className = "se-slash-menu"
                popup.setAttribute("role", "listbox")
                document.body.append(popup)
                executeRef = () => props.command(props.items[selected])
                render(props.clientRect?.() ?? null)
              },
              onUpdate: (props) => {
                current = props.items
                selected = Math.min(selected, Math.max(current.length - 1, 0))
                executeRef = () => props.command(props.items[selected])
                render(props.clientRect?.() ?? null)
              },
              onKeyDown: (props) => {
                const { event } = props
                if (event.key === "ArrowDown") {
                  selected = current.length === 0 ? 0 : (selected + 1) % current.length
                  render()
                  return true
                }
                if (event.key === "ArrowUp") {
                  selected = current.length === 0 ? 0 : (selected - 1 + current.length) % current.length
                  render()
                  return true
                }
                if (event.key === "Enter" || event.key === "Tab") {
                  execute()
                  return true
                }
                if (event.key === "Escape") {
                  popup?.remove()
                  popup = null
                  return true
                }
                return false
              },
              onExit: () => {
                popup?.remove()
                popup = null
              },
            }
          },
        }),
      ]
    },
  })
}
