import { describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { countDocument, extractHeadings } from '../doc-stats'

/** 构造文档后取 doc（JSON 内容：程序化文本保真，不吃 HTML 折叠） */
function buildDoc(content: unknown) {
  const editor = new Editor({
    extensions: [StarterKit],
    content: content as never,
  })
  const doc = editor.state.doc
  editor.destroy()
  return doc
}

describe('countDocument 字数口径', () => {
  it('CJK 逐字计数，空白不计', () => {
    const doc = buildDoc({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: '智能 写作' }] }],
    })
    expect(countDocument(doc)).toEqual({ words: 4, chars: 4, paragraphs: 1 })
  })

  it('连续拉丁字母/数字串计 1 词（与 Word 口径一致）', () => {
    const doc = buildDoc({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: '9800M12 与 UPS' }] }],
    })
    // 9800M12 + 与 + UPS = 3 词；字符 = 7 + 1 + 3 = 11
    expect(countDocument(doc)).toEqual({ words: 3, chars: 11, paragraphs: 1 })
  })

  it('空文档为 0 字', () => {
    const doc = buildDoc({ type: 'doc', content: [{ type: 'paragraph' }] })
    expect(countDocument(doc)).toEqual({ words: 0, chars: 0, paragraphs: 1 })
  })

  it('标点不计入字数；段落数按文本块计（含标题与空段）', () => {
    const doc = buildDoc({
      type: 'doc',
      content: [
        { type: 'heading', attrs: { level: 1 }, content: [{ type: 'text', text: '总述。' }] },
        { type: 'paragraph' },
      ],
    })
    const stats = countDocument(doc)
    expect(stats.words).toBe(2)
    expect(stats.chars).toBe(3) // 句号计入字符数但不计字数
    expect(stats.paragraphs).toBe(2)
  })
})

describe('extractHeadings 目录抽取', () => {
  it('按文档顺序抽取 H1–H3 及位置', () => {
    const doc = buildDoc({
      type: 'doc',
      content: [
        { type: 'heading', attrs: { level: 1 }, content: [{ type: 'text', text: '总述' }] },
        { type: 'paragraph', content: [{ type: 'text', text: '正文一' }] },
        { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '细节' }] },
      ],
    })
    const headings = extractHeadings(doc)
    expect(headings).toHaveLength(2)
    expect(headings[0]).toMatchObject({ level: 1, text: '总述' })
    expect(headings[1]).toMatchObject({ level: 2, text: '细节' })
    expect(headings[1].pos).toBeGreaterThan(headings[0].pos)
  })

  it('无标题返回空数组', () => {
    const doc = buildDoc({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: '纯正文' }] }],
    })
    expect(extractHeadings(doc)).toEqual([])
  })
})
