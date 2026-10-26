import type { Node as PMNode } from '@tiptap/pm/model'

export interface DocStats {
  /**
   * 字数 = CJK 字符逐字计 1 + 连续拉丁字母/数字串计 1（与 Word/WPS 的「字数」口径一致，
   * 标点与空白不计）。
   */
  words: number
  /** 字符数（不含空白） */
  chars: number
  /** 段落数（所有文本块，含空段/列表项/代码块） */
  paragraphs: number
}

export interface DocHeading {
  level: number
  text: string
  /** 文档位置（heading 节点之前），滚动定位用 */
  pos: number
}

const CJK_CHAR = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/
const LATIN_WORD = /[A-Za-z0-9][A-Za-z0-9'’_-]*/g

/** 统计文档字数（右下角 HUD 展示；高频调用方自带防抖） */
export function countDocument(doc: PMNode): DocStats {
  let words = 0
  let chars = 0
  let paragraphs = 0
  doc.descendants((node) => {
    if (node.isTextblock) paragraphs += 1
    if (!node.isText || !node.text) return
    for (const ch of node.text) {
      if (!/\s/.test(ch)) chars += 1
      if (CJK_CHAR.test(ch)) words += 1
    }
    const latin = node.text.match(LATIN_WORD)
    if (latin) words += latin.length
  })
  return { words, chars, paragraphs }
}

/** 抽取文档标题（目录导航）；按文档顺序返回 */
export function extractHeadings(doc: PMNode): DocHeading[] {
  const headings: DocHeading[] = []
  doc.descendants((node, pos) => {
    if (node.type.name === 'heading') {
      headings.push({ level: Number(node.attrs.level) || 1, text: node.textContent, pos })
    }
  })
  return headings
}
